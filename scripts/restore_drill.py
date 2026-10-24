#!/usr/bin/env python3
"""Operator orchestration only; restoration and SQL run in the vallery cluster.

Default is a manifest plan. --run commissions a bounded, isolated reader-only
PITR drill after checking the production prerequisites. State is metadata only;
never read, copy or print credential Secret data.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
LABEL = "weather.vallery.net/restore-drill"
BASE = ["kubectl", "--context", "vallery", "--namespace", "weather"]


def kubectl(*args, stdin=None):
    return subprocess.check_output([*BASE, *args], input=stdin, text=True).strip()


def read(kind, name):
    return json.loads(kubectl("get", kind, name, "-o", "json"))


def sql(cluster, statement):
    primary = read("cluster", cluster)["status"]["currentPrimary"]
    return kubectl("exec", primary, "-c", "postgres", "--", "psql", "-X", "-U", "postgres",
                   "-d", "weather", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", statement)


def manifest(operation, target, expires):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", operation):
        raise ValueError("operation must be 1-31 lowercase letters, digits or hyphens")
    target_time = dt.datetime.fromisoformat(target.replace("Z", "+00:00"))
    if target_time.tzinfo is None:
        raise ValueError("recovery target requires an explicit timezone")
    dt.date.fromisoformat(expires)
    text = (ROOT / "deploy/acceptance/restore/drill.yaml").read_text()
    for key, value in {"__DRILL_NAME__": "weather-drill-pg-" + operation,
                       "__OPERATION__": operation, "__EXPIRES__": expires,
                       "__RECOVERY_TARGET__": target_time.astimezone(dt.timezone.utc).isoformat()}.items():
        text = text.replace(key, value)
    return text


def preflight():
    cluster = read("cluster", "weather-pg")
    if cluster["status"].get("readyInstances") != 3:
        raise RuntimeError("production weather-pg must have three ready instances")
    archiving = next((c for c in cluster["status"].get("conditions", []) if c["type"] == "ContinuousArchiving"), {})
    if archiving.get("status") != "True":
        raise RuntimeError("production ContinuousArchiving must be true")
    store = read("objectstore", "weather-pg-b2")["spec"]["configuration"]
    if store["destinationPath"] != "s3://weather-db-vallery/cnpg":
        raise RuntimeError("production archive binding changed")
    if store["s3Credentials"]["accessKeyId"]["name"] != "weather-pg-backup-b2-writer":
        raise RuntimeError("production writer binding changed")
    backups = json.loads(kubectl("get", "backups", "-o", "json"))["items"]
    completed = [b for b in backups if b["spec"]["cluster"]["name"] == "weather-pg"
                 and b.get("status", {}).get("phase") == "completed"]
    if not completed:
        raise RuntimeError("a completed production base backup is required before PITR")
    # Names/keys only: no Secret values enter this process or its output.
    for role in ("writer", "reader"):
        keys = kubectl("get", "secret", "weather-pg-backup-b2-" + role,
                       "-o", "go-template={{range $k, $v := .data}}{{$k}}{{\"\\n\"}}{{end}}")
        if set(keys.splitlines()) != {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"}:
            raise RuntimeError("archive credential key names differ")
    return cluster


def cleanup(operation, evidence, evidence_path):
    name = "weather-drill-pg-" + operation
    if not evidence.get("verified") or evidence.get("operation") != operation:
        raise RuntimeError("cleanup requires this operation's verified PITR evidence")
    paths = {"cluster": "/apis/postgresql.cnpg.io/v1/namespaces/weather/clusters/",
             "pvc": "/api/v1/namespaces/weather/persistentvolumeclaims/",
             "objectstore": "/apis/barmancloud.cnpg.io/v1/namespaces/weather/objectstores/",
             "networkpolicy": "/apis/networking.k8s.io/v1/namespaces/weather/networkpolicies/"}
    if "cleanupPlan" not in evidence:
        cluster = read("cluster", name)
        pvcs = json.loads(kubectl("get", "pvc", "-l", "cnpg.io/cluster=" + name, "-o", "json"))["items"]
        if cluster["metadata"]["uid"] != evidence["clusterUid"]:
            raise RuntimeError("drill Cluster was replaced; refuse deletion")
        for pvc in pvcs:
            if not any(r["uid"] == evidence["clusterUid"] for r in pvc["metadata"].get("ownerReferences", [])):
                raise RuntimeError("PVC ownership differs; refuse deletion")
        objects = [("cluster", cluster)] + [("pvc", p) for p in pvcs]
        objects += [("objectstore", read("objectstore", name + "-reader")),
                    ("networkpolicy", read("networkpolicy", name + "-isolation"))]
        for _, obj in objects:
            labels = obj["metadata"].get("labels", {})
            if labels.get(LABEL) != operation or labels.get("estate.vallery.net/disposable") != "true":
                raise RuntimeError("disposable object labels differ; refuse cleanup")
        evidence["cleanupPlan"] = [{"kind": kind, "name": obj["metadata"]["name"],
                                    "uid": obj["metadata"]["uid"]} for kind, obj in objects]
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    # Record identities before mutation; cleanup can resume the same plan after
    # response loss. Every DELETE uses UID and resourceVersion preconditions.
    for item in evidence["cleanupPlan"]:
        kind, target_name = item["kind"], item["name"]
        if kind not in paths or not target_name.startswith(name):
            raise RuntimeError("cleanup plan leaves this exact disposable operation")
        current = json.loads(kubectl("get", kind, "--field-selector", "metadata.name=" + target_name, "-o", "json"))["items"]
        if not current:
            continue
        metadata = current[0]["metadata"]
        if (metadata["uid"] != item["uid"] or metadata.get("labels", {}).get(LABEL) != operation
                or metadata.get("labels", {}).get("estate.vallery.net/disposable") != "true"):
            raise RuntimeError("disposable resource was replaced; refuse deletion")
        options = {"apiVersion": "v1", "kind": "DeleteOptions",
                   "preconditions": {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]}}
        kubectl("delete", "--raw", paths[kind] + target_name, "-f", "-", stdin=json.dumps(options))
        deadline = time.monotonic() + 180
        while True:
            observed = json.loads(kubectl("get", kind, "--field-selector", "metadata.name=" + target_name, "-o", "json"))["items"]
            if not observed:
                break
            if observed[0]["metadata"]["uid"] != item["uid"]:
                raise RuntimeError("replacement appeared during cleanup; preserve it")
            if time.monotonic() > deadline:
                raise RuntimeError("deletion observation timed out; resume the same cleanup plan")
            time.sleep(2)
    evidence["cleanup"] = {"cluster": name, "removedPvcUids": [p["uid"] for p in evidence["cleanupPlan"] if p["kind"] == "pvc"],
                           "remainingPvcCount": 0, "readerObjectStoreRemoved": True, "isolationPolicyRemoved": True}


def verify(args, evidence):
    if evidence.get("operation") != args.operation or evidence.get("cluster") != "weather-drill-pg-" + args.operation:
        raise RuntimeError("evidence identity differs from this exact drill")
    name = evidence["cluster"]
    kubectl("wait", "cluster/" + name, "--for=condition=Ready", "--timeout=1800s")
    drill = read("cluster", name)
    restored = sql(name, "SELECT sequence FROM weather_recovery_drill.sentinel "
                   f"WHERE operation='{args.operation}' ORDER BY sequence;")
    vector = sql(name, "SELECT extversion FROM pg_extension WHERE extname='vector';")
    if restored != "0\n1" or vector != "0.8.6" or drill["spec"].get("plugins"):
        raise RuntimeError("PITR sentinels, vector version or no-archiver invariant failed; preserve drill")
    if drill["metadata"].get("labels", {}).get(LABEL) != args.operation:
        raise RuntimeError("drill was replaced or is not bound to this operation")
    if evidence.get("clusterUid") not in (None, drill["metadata"]["uid"]):
        raise RuntimeError("drill Cluster UID differs; preserve the replacement")
    evidence["clusterUid"] = drill["metadata"]["uid"]
    start = dt.datetime.fromisoformat(evidence["restoreStartedAt"])
    target = dt.datetime.fromisoformat(evidence["recoveryTarget"])
    last_sentinel = dt.datetime.fromisoformat(evidence["sentinels"][1])
    evidence.update({"verified": True, "restoredSequences": [0, 1], "excludedSequences": [2],
                     "vectorVersion": vector, "walArchiverEnabled": False,
                     "productionServerNameWriteAuthority": False,
                     "rtoSeconds": round((dt.datetime.now(dt.timezone.utc) - start).total_seconds(), 3),
                     "measuredRpoSeconds": round((target - last_sentinel).total_seconds(), 3),
                     "rpoSentinelResolutionSeconds": 60,
                     "rpoDefinition": "target minus latest restored sentinel; post-target records intentionally excluded"})
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))


def run(args):
    preflight()
    name = "weather-drill-pg-" + args.operation
    # Admission and capacity are checked again by Kubernetes; refuse an existing
    # operation so an observation timeout can never accidentally restart a drill.
    existing = json.loads(kubectl("get", "cluster", "--field-selector", "metadata.name=" + name, "-o", "json"))["items"]
    if existing or args.evidence.exists():
        raise RuntimeError("operation already exists; inspect/resume it rather than restarting")
    sql("weather-pg", "CREATE SCHEMA IF NOT EXISTS weather_recovery_drill; "
        "CREATE TABLE IF NOT EXISTS weather_recovery_drill.sentinel "
        "(operation text NOT NULL, sequence integer NOT NULL, committed_at timestamptz NOT NULL DEFAULT clock_timestamp(), "
        "PRIMARY KEY (operation, sequence));")
    times = []
    for sequence in range(3):
        if sequence:
            time.sleep(60)
        times.append(sql("weather-pg", f"INSERT INTO weather_recovery_drill.sentinel(operation,sequence) "
                         f"VALUES ('{args.operation}',{sequence}) RETURNING committed_at;" ).splitlines()[0])
    # Target after sentinel1 and before sentinel2. A timestamp, rather than latest
    # recovery, proves that later committed records are excluded.
    target = dt.datetime.fromisoformat(times[1]) + dt.timedelta(seconds=1)
    forced_wal = sql("weather-pg", "SELECT pg_walfile_name(pg_current_wal_insert_lsn());")
    if not re.fullmatch(r"[A-F0-9]{24}", forced_wal):
        raise RuntimeError("unexpected WAL identity")
    sql("weather-pg", "SELECT pg_switch_wal();")
    # Wait for forced WAL archival before recovery; the bounded API observation
    # cannot declare a previous failed archive a success.
    wal_deadline = time.monotonic() + 300
    while True:
        archived = sql("weather-pg", f"SELECT coalesce(last_archived_wal >= '{forced_wal}', false) FROM pg_stat_archiver;")
        if archived == "t":
            break
        if time.monotonic() > wal_deadline:
            raise RuntimeError("forced WAL did not finish archiving within five minutes")
        time.sleep(5)
    evidence = {"operation": args.operation, "cluster": name, "recoveryTarget": target.isoformat(),
                "sentinels": times, "sourceServerName": "weather-pg", "readerSecretReferencedInPlace": True,
                "forcedWal": forced_wal, "forcedWalArchivedBeforeRestore": True,
                "restoreStartedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "verified": False}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    kubectl("apply", "-f", "-", stdin=manifest(args.operation, target.isoformat(), args.expires))
    evidence["clusterUid"] = read("cluster", name)["metadata"]["uid"]
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    # Kubernetes wait has a finite timeout. Failure preserves the same live
    # Cluster and evidence; it never deletes or restarts it automatically.
    verify(args, evidence)
    # Deliberately separate cleanup: retain evidence before exact disposable
    # resource deletion and preserve the restore for inspection on failure.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--target", default=dt.datetime.now(dt.timezone.utc).isoformat())
    parser.add_argument("--expires", default=(dt.date.today() + dt.timedelta(days=1)).isoformat())
    parser.add_argument("--evidence", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--cleanup", action="store_true")
    modes.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    # Validate the operation before it can enter SQL or exact deletion targets.
    plan = manifest(args.operation, args.target, args.expires)
    if args.run:
        run(args)
    elif args.cleanup:
        evidence = json.loads(args.evidence.read_text())
        cleanup(args.operation, evidence, args.evidence)
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence))
    elif args.verify:
        verify(args, json.loads(args.evidence.read_text()))
    else:
        print(plan)


if __name__ == "__main__":
    main()
