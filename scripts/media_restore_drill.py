#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""weather#77 media/originals restore drill from B2 custody. Operator orchestration only.

The copy, checksum verification, catalog copy and reconcile run in one disposable pod in the
vallery cluster (deploy/acceptance/restore/media-drill.yaml); this script renders and applies
it, probes the drill api and web through kubectl port-forward, records the receipt and tears
everything down by its exact UIDs. Metadata only: never read, copy or print Secret data.

  media_restore_drill.py --operation 20261117a --day 2026-10-29 --evidence E.json --plan
  media_restore_drill.py --operation 20261117a --day 2026-10-29 --evidence E.json --run
  media_restore_drill.py --operation 20261117a --evidence E.json --cleanup
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "deploy/acceptance/restore"
LABEL = "weather.vallery.net/restore-drill"
BASE = ["kubectl", "--context", "vallery", "--namespace", "weather"]
# barman-cloud ships in the CNPG "system" image of the same major (weather-pg runs "standard").
PG_IMAGE = (
    "registry.vallery.net/jvallery/mirror-ghcr-io-cloudnative-pg-postgresql:16.16-system-trixie-"
    "d7289505a60b@sha256:d7289505a60b6cc996da0d514d5a4e3217b8051725a42346090f5d7f15db1be6"
)
GARAGE_IMAGE = (
    "registry.vallery.net/jvallery/garage-runtime-deadline"
    "@sha256:f54c84dfdff9e8f219de23d9e0f10ec36d1f1d1c83cab15c6a4520fd6dcc4d94"
)
FILES = ("keys.sh", "catalog-init.sh", "catalog.sh", "garage.sh", "driver.py")


def kubectl(*args, stdin=None):
    return subprocess.check_output([*BASE, *args], input=stdin, text=True).strip()


def live_image(deployment, container):
    spec = json.loads(kubectl("get", "deployment", deployment, "-o", "json"))["spec"]["template"][
        "spec"
    ]
    image = next(c["image"] for c in spec["containers"] if c["name"] == container)
    if "@sha256:" not in image:
        raise RuntimeError(f"{deployment} is not pinned by digest")
    return image


def rpo_candidates():
    """Newest production media rows (content-hash keys and times only), read on a replica."""
    pods = json.loads(
        kubectl(
            "get",
            "pods",
            "-l",
            "cnpg.io/instanceRole=replica,cnpg.io/cluster=weather-pg",
            "-o",
            "json",
        )
    )["items"]
    # The newest 40 rows plus the newest row of every hour for 21 days, newest first:
    # custody trails production by more than 40 objects, so the hourly series is
    # what locates the custody frontier (RPO resolution one hour).
    sql = (
        "SELECT json_agg(t ORDER BY created_epoch DESC) FROM ("
        "SELECT bucket, object_key, extract(epoch FROM created_at)::bigint AS created_epoch"
        " FROM (SELECT * FROM weather.media_asset ORDER BY created_at DESC LIMIT 40) n"
        " UNION SELECT bucket, object_key, extract(epoch FROM created_at)::bigint"
        " FROM (SELECT DISTINCT ON (date_trunc('hour', created_at)) bucket, object_key, created_at"
        " FROM weather.media_asset WHERE created_at > now() - interval '21 days'"
        " ORDER BY date_trunc('hour', created_at) DESC, created_at DESC) h) t"
    )
    out = kubectl(
        "exec",
        pods[0]["metadata"]["name"],
        "-c",
        "postgres",
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        "weather",
        "-Atc",
        sql,
    )
    return json.loads(out)


def render(operation, day):
    name = "weather-media-drill-" + operation
    expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    text = (DRILL / "media-drill.yaml").read_text()
    for key, value in {
        "__NAME__": name,
        "__OPERATION__": operation,
        "__EXPIRES__": expires,
        "__DAY__": day,
        "__PG_IMAGE__": PG_IMAGE,
        "__GARAGE_IMAGE__": GARAGE_IMAGE,
        "__APP_IMAGE__": live_image("api", "api"),
        "__WEB_IMAGE__": live_image("web", "web"),
    }.items():
        text = text.replace(key, value)
    if re.search(r"__[A-Z_]+__", text):
        raise RuntimeError("unrendered placeholder")
    return name, text


def configmap(name, operation, candidates):
    args = ["create", "configmap", name, "--dry-run=client", "-o", "json"]
    args += [f"--from-file={f}={DRILL / 'media-drill' / f}" for f in FILES]
    cm = json.loads(subprocess.check_output([*BASE, *args], text=True))
    cm["data"]["rpo-candidates.json"] = json.dumps(candidates)
    cm["metadata"]["labels"] = {LABEL: operation, "estate.vallery.net/disposable": "true"}
    return json.dumps(cm)


def wait_receipt(name, deadline):
    while time.time() < deadline:
        status = json.loads(kubectl("get", "pod", name, "-o", "json"))["status"]
        if status.get("phase") in ("Failed", "Succeeded"):
            raise RuntimeError(f"drill pod ended early ({status.get('phase')})")
        ended = [
            c["name"] for c in status.get("containerStatuses", []) if "terminated" in c["state"]
        ]
        if ended:
            raise RuntimeError(f"drill containers ended early: {ended}")
        try:
            log = kubectl("logs", name, "-c", "driver")
        except subprocess.CalledProcessError:
            log = ""
        for line in log.splitlines():
            if '"weather_media_drill_receipt"' in line:
                return json.loads(line)
        time.sleep(30)
    raise RuntimeError("no receipt before the deadline")


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def probes(name, receipt, day):
    forward = subprocess.Popen(
        [*BASE, "port-forward", "pod/" + name, "18300:3000", "18800:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(5)
        ids = receipt["probe_ids"]
        status, body = get(f"http://localhost:18300/day/{day}")
        page = body.decode(errors="replace")
        result = {
            "day_page_status": status,
            "day_page_bytes": len(body),
            "day_page_has_media": "/media/" in page,
            "day_page_mentions_unavailable": "unavailable" in page.lower(),
        }
        for label, asset in (
            ("restored_still", ids["restored_still"]),
            ("withheld", ids["withheld"]),
            ("original", ids["original"]),
        ):
            if asset:
                result[f"media_{label}_status"] = get(f"http://127.0.0.1:18800/media/{asset}")[0]
        return result
    finally:
        forward.terminate()
        forward.wait(10)


def run(args):
    if not re.fullmatch(r"[a-z0-9]{6,20}", args.operation) or not args.day:
        raise SystemExit("--operation [a-z0-9]{6,20} and --day YYYY-MM-DD are required")
    name, manifest = render(args.operation, args.day)
    if args.plan:
        print(manifest)
        return
    candidates = rpo_candidates()
    evidence = {
        "operation": args.operation,
        "day": args.day,
        "name": name,
        "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    kubectl("apply", "-f", "-", stdin=configmap(name, args.operation, candidates))
    kubectl("apply", "-f", "-", stdin=manifest)
    evidence["objects"] = [
        {
            "kind": kind,
            "name": name,
            "uid": json.loads(kubectl("get", kind, name, "-o", "json"))["metadata"]["uid"],
        }
        for kind in ("pod", "networkpolicy", "configmap")
    ]
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    receipt = wait_receipt(name, time.time() + 10200)
    evidence["receipt"] = receipt
    evidence["catalog"] = next(
        (
            json.loads(line)
            for line in kubectl("logs", name, "-c", "catalog-init").splitlines()
            if '"weather_drill_catalog"' in line
        ),
        None,
    )
    evidence["probes"] = probes(name, receipt, args.day)
    evidence["rendered_epoch"] = round(time.time())
    evidence["rto_seconds"] = evidence["rendered_epoch"] - receipt["start_epoch"]
    evidence["images"] = {
        c["name"]: c["image"]
        for c in json.loads(kubectl("get", "pod", name, "-o", "json"))["spec"]["containers"]
    }
    kubectl("exec", name, "-c", "driver", "--", "touch", "/work/probes-done")
    p = evidence["probes"]
    evidence["verified"] = (
        receipt["result"] == "pass"
        and p["day_page_status"] == 200
        and p["day_page_has_media"]
        and p.get("media_restored_still_status") == 200
        and p.get("media_original_status") == 404
    )
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps({k: evidence[k] for k in ("verified", "rto_seconds", "probes")}, sort_keys=True)
    )


def cleanup(args):
    evidence = json.loads(args.evidence.read_text())
    if evidence.get("operation") != args.operation:
        raise SystemExit("evidence is for another operation")
    for item in evidence["objects"]:
        current = json.loads(
            kubectl(
                "get",
                item["kind"],
                "--field-selector",
                "metadata.name=" + item["name"],
                "-o",
                "json",
            )
        )["items"]
        if not current:
            continue
        meta = current[0]["metadata"]
        if meta["uid"] != item["uid"] or meta.get("labels", {}).get(LABEL) != args.operation:
            raise SystemExit(f"{item['kind']}/{item['name']} was replaced; refuse deletion")
        kubectl("delete", item["kind"], item["name"], "--wait=true", "--timeout=180s")
    left = kubectl(
        "get",
        "all,pvc,secret,configmap,networkpolicy",
        "-l",
        f"{LABEL}={args.operation}",
        "-o",
        "name",
    )
    evidence["cleanup"] = {
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "labelled_objects_left": left.splitlines() if left else [],
    }
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence["cleanup"]))
    if left:
        raise SystemExit("labelled drill objects remain")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--operation", required=True)
    parser.add_argument("--day")
    parser.add_argument("--evidence", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    cleanup(args) if args.cleanup else run(args)


if __name__ == "__main__":
    main()
