#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""weather-pg restore drill from the B2 custody copy (weather#33, STD-DR-03/04).

One run, inside namespace weather, as the CronJob weather-pg-restore-drill (or a Job created from
it):

1. Refuses to start while the drill Cluster weather-pg-restore-drill-copy exists (one drill at a
   time; the name is fixed, and the Role grants get and delete on that name only).
2. Renders copy.yaml and creates that one-instance Cluster, which recovers to the end of the WAL
   the B2 custody copy holds through the Argo CD-managed, read-only ObjectStore
   weather-pg-restore-drill-reader (drill.yaml). The Cluster is owned by this Job, so a run that
   dies before its cleanup is collected with the Job.
3. Waits for the Cluster to be healthy (RTO: creation to healthy) and reads the time of the last
   replayed transaction from the recovery log (achieved RPO: drill start minus that time).
4. Reads, as weather_api, the same row counts on the copy and on a production standby: the
   station view, and the obs_st and rapid_wind rows inserted in the day before the restored
   point (less a margin, so no transaction still open at that point is counted).
5. Deletes the copy and waits until its claims are gone; a failed run keeps it for inspection
   until the Job's TTL collects it.

It exits 0 only when the copy is healthy and out of recovery, pgvector matches, every count
matches, the RPO is inside its limit and cleanup finished. Output is counts, times and object
names only, never a Secret value or row data. Standard library plus asyncpg (in weather-app).

  restore_drill.py --plan [--operation ID]   print the rendered manifest (no cluster access)
  restore_drill.py                           run (in the cluster, with the Job's ServiceAccount)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "copy.yaml"
STORE = HERE / "drill.yaml"
COPY = "weather-pg-restore-drill-copy"
READER_STORE = "weather-pg-restore-drill-reader"
NAMESPACE = "weather"
LABEL = "weather.vallery.net/restore-drill"
SOURCE_TIER = "B2"
SOURCE_PATH = "s3://object-custody-vallery/object-custody/weather-pg-archive/cnpg"
READER_SECRET = "weather-custody-restore-reader"
JOB_PREFIX = "weather-pg-restore-drill-"
# The run id, a label value on the copy.
OPERATION = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?")
HEALTHY = "Cluster in healthy state"
VECTOR_VERSION = "0.8.6"
LAST_XACT = re.compile(
    r"last completed transaction was at log time "
    r"([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:.]+(?:[+-][0-9:]+)?)"
)
# Counted on both sides as weather_api. Windowed views count rows whose database-assigned
# inserted_at lies in (cutoff - 1 day, cutoff]; cutoff is the restored point less MARGIN.
WINDOWED = ("public_api.obs_st", "public_api.rapid_wind")
WHOLE = ("public_api.station",)
MARGIN = dt.timedelta(minutes=10)
WINDOW = dt.timedelta(days=1)
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
CLUSTERS = "/apis/postgresql.cnpg.io/v1/namespaces/weather/clusters"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def log(event: str, **fields) -> None:
    print(
        json.dumps({"event": event, "at": now().isoformat(), **fields}, sort_keys=True), flush=True
    )


def operation_from(job_name: str) -> str:
    """The drill id: the Job name less the CronJob prefix (cron runs end in their minute)."""
    operation = job_name.removeprefix(JOB_PREFIX)
    if not OPERATION.fullmatch(operation):
        raise ValueError("operation must be a 1-24 character DNS slug with alphanumeric ends")
    return operation


def manifest(operation: str, expires: str, owner: dict | None) -> str:
    """copy.yaml rendered for this run: the Cluster weather-pg-restore-drill-copy."""
    if not OPERATION.fullmatch(operation):
        raise ValueError("operation must be a 1-24 character DNS slug with alphanumeric ends")
    dt.date.fromisoformat(expires)
    text = TEMPLATE.read_text()
    for key, value in {
        "__OPERATION__": operation,
        "__EXPIRES__": expires,
        # A JSON flow sequence is valid YAML.
        "__OWNER_REFERENCES__": json.dumps([owner] if owner else []),
    }.items():
        text = text.replace(key, value)
    if re.findall(r"(?m)^  name: (\S+)$", text) != [COPY]:
        raise ValueError("the drill creates only Cluster " + COPY)
    return text


def postgres_time(value: str) -> dt.datetime:
    """'2026-11-20 09:12:01.123456+00' (a PostgreSQL log time) as an aware datetime."""
    text = value.strip().replace(" ", "T")
    if re.search(r"[+-][0-9]{2}$", text):
        text += ":00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def last_transaction(log_text: str) -> dt.datetime | None:
    found = LAST_XACT.findall(log_text)
    return postgres_time(found[-1]) if found else None


def compare(copy: dict[str, int], source: dict[str, int]) -> list[str]:
    problems = []
    for name in (*WHOLE, *WINDOWED):
        if copy.get(name) is None or source.get(name) is None:
            problems.append(f"{name}: missing")
        elif copy[name] != source[name]:
            problems.append(f"{name}: copy {copy[name]} source {source[name]}")
    return problems


class Kube:
    """A minimal client for namespace weather, with the pod's ServiceAccount token."""

    def __init__(self) -> None:
        self.context = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))

    def request(
        self,
        method,
        path,
        body=None,
        query=None,
        content_type="application/json",
        raw=False,
        missing_ok=False,
    ):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        if isinstance(body, dict):
            body = json.dumps(body)
        req = urllib.request.Request(
            url,
            data=body.encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + (SA_DIR / "token").read_text().strip(),
                "Accept": "application/json",
                "Content-Type": content_type,
            },
        )
        for delay in (2, 4, 8, 15, 30, None):
            try:
                with urllib.request.urlopen(req, context=self.context, timeout=60) as resp:
                    payload = resp.read()
                break
            except urllib.error.HTTPError as err:
                if err.code == 404 and missing_ok:
                    return None
                reason = (err.read() or b"")[:300].decode("utf-8", "replace")
                raise RuntimeError(f"{method} {path}: HTTP {err.code} {reason}") from None
            except urllib.error.URLError:
                # NetworkPolicy chains for a new pod settle asynchronously.
                if delay is None:
                    raise
                time.sleep(delay)
        if raw:
            return payload.decode("utf-8", "replace")
        return json.loads(payload) if payload else None


async def counts(dsn: dict, cutoff: dt.datetime) -> dict:
    import asyncpg  # in the weather-app image

    conn = await asyncpg.connect(**dsn, timeout=30)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        found: dict = {"rows": {}}
        for name in WHOLE:
            found["rows"][name] = await conn.fetchval(f"SELECT count(*) FROM {name}")
        for name in WINDOWED:
            found["rows"][name] = await conn.fetchval(
                f"SELECT count(*) FROM {name} WHERE inserted_at > $1 AND inserted_at <= $2",
                cutoff - WINDOW,
                cutoff,
            )
        found["in_recovery"] = await conn.fetchval("SELECT pg_is_in_recovery()")
        found["vector"] = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
        found["newest_rapid_wind"] = await conn.fetchval(
            "SELECT max(inserted_at) FROM public_api.rapid_wind"
        )
        return found
    finally:
        await conn.close()


class Drill:
    def __init__(self) -> None:
        self.job_name = os.environ["JOB_NAME"]
        self.job_uid = os.environ["JOB_UID"]
        self.operation = operation_from(self.job_name)
        self.name = COPY
        self.recovery_timeout = int(os.environ.get("RECOVERY_TIMEOUT_SECONDS", "7200"))
        self.max_rpo = int(os.environ.get("MAX_RPO_SECONDS", "93600"))
        self.kube = Kube()
        self.created_uid: str | None = None
        user = Path(os.environ["DATABASE_CREDENTIAL_DIRECTORY"])
        self.credentials = {
            "user": (user / "username").read_text().strip(),
            "password": (user / "password").read_text().strip(),
            "database": "weather",
            "port": 5432,
        }
        self.ca = str(user / "ca.crt")
        self.receipt: dict = {
            "source_tier": SOURCE_TIER,
            "source_path": SOURCE_PATH,
            "reader_store": READER_STORE,
            "reader_secret": READER_SECRET,
            "cluster": self.name,
            "job": self.job_name,
        }

    def get_copy(self) -> dict | None:
        return self.kube.request("GET", f"{CLUSTERS}/{COPY}", missing_ok=True)

    def claims(self) -> list[str]:
        items = self.kube.request(
            "GET",
            "/api/v1/namespaces/weather/persistentvolumeclaims",
            query={"labelSelector": "cnpg.io/cluster=" + self.name},
        )
        return [i["metadata"]["name"] for i in items["items"]]

    def create(self) -> None:
        owner = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "name": self.job_name,
            "uid": self.job_uid,
            "blockOwnerDeletion": False,
            "controller": False,
        }
        expires = (now().date() + dt.timedelta(days=2)).isoformat()
        text = manifest(self.operation, expires, owner)
        made = self.kube.request("POST", CLUSTERS, body=text, content_type="application/yaml")
        self.created_uid = made["metadata"]["uid"]

    def recovery_log(self) -> str:
        pods = self.kube.request(
            "GET",
            "/api/v1/namespaces/weather/pods",
            query={"labelSelector": f"batch.kubernetes.io/job-name={self.name}-1-full-recovery"},
        )
        text = ""
        for pod in pods["items"]:
            try:
                text += self.kube.request(
                    "GET",
                    f"/api/v1/namespaces/weather/pods/{pod['metadata']['name']}/log",
                    query={"container": "full-recovery", "tailLines": "400"},
                    raw=True,
                )
            except RuntimeError:
                continue
        return text

    def wait_healthy(self) -> dt.datetime | None:
        deadline = time.monotonic() + self.recovery_timeout
        last, phase = None, None
        while time.monotonic() < deadline:
            last = last_transaction(self.recovery_log()) or last
            status = (self.get_copy() or {}).get("status", {})
            if status.get("phase") != phase:
                phase = status.get("phase")
                log("cluster_phase", phase=phase)
            if phase == HEALTHY and status.get("readyInstances") == 1:
                return last
            time.sleep(10)
        raise RuntimeError(f"no healthy copy within {self.recovery_timeout} s (phase {phase})")

    def delete(self) -> None:
        if self.created_uid is None:
            return
        # Only the copy this run created: the UID precondition refuses a replacement.
        self.kube.request(
            "DELETE",
            f"{CLUSTERS}/{COPY}",
            body={"preconditions": {"uid": self.created_uid}, "propagationPolicy": "Background"},
            missing_ok=True,
        )
        deadline = time.monotonic() + 600
        while self.claims() or self.get_copy():
            if time.monotonic() > deadline:
                raise RuntimeError("cleanup did not finish within 600 s")
            time.sleep(5)

    def run(self) -> bool:
        started = now()
        self.receipt["started_at"] = started.isoformat()
        ok = False
        try:
            if self.get_copy():
                raise RuntimeError(f"{COPY} exists: another drill is running or was kept")
            created = now()
            self.create()
            log("created", cluster=self.name, source_tier=SOURCE_TIER)
            last = self.wait_healthy()
            healthy = now()
            self.receipt["ready"] = True
            self.receipt["rto_seconds"] = round((healthy - created).total_seconds())
            copy_dsn = {**self.credentials, "host": self.name + "-rw", "ssl": "require"}
            tls = ssl.create_default_context(cafile=self.ca)
            source_dsn = {**self.credentials, "host": "weather-pg-ro", "ssl": tls}
            # Fall back to the newest restored row when the recovery log was not readable.
            probe = asyncio.run(counts(copy_dsn, now()))
            point = last or probe["newest_rapid_wind"]
            if point is None:
                raise RuntimeError("no restored point: no recovery log line and no rows")
            self.receipt["last_restored_transaction"] = point.isoformat()
            self.receipt["restored_point_source"] = "recovery-log" if last else "newest-row"
            self.receipt["rpo_seconds"] = round((started - point).total_seconds())
            self.receipt["last_transaction_before_healthy_seconds"] = round(
                (healthy - point).total_seconds()
            )
            cutoff = point - MARGIN
            copy = asyncio.run(counts(copy_dsn, cutoff))
            source = asyncio.run(counts(source_dsn, cutoff))
            problems = compare(copy["rows"], source["rows"])
            self.receipt["window"] = [(cutoff - WINDOW).isoformat(), cutoff.isoformat()]
            self.receipt["rows"] = {
                name: {"copy": copy["rows"].get(name), "source": source["rows"].get(name)}
                for name in (*WHOLE, *WINDOWED)
            }
            self.receipt["rows_match"] = not problems
            self.receipt["copy_in_recovery"] = copy["in_recovery"]
            self.receipt["vector"] = copy["vector"]
            for problem in problems:
                log("row_count_mismatch", detail=problem)
            ok = (
                not problems
                and not copy["in_recovery"]
                and copy["vector"] == VECTOR_VERSION
                and self.receipt["rpo_seconds"] <= self.max_rpo
            )
        except Exception as err:  # report; the Job fails below
            log("drill_error", error=f"{type(err).__name__}: {err}"[:500])
        finally:
            if ok:
                try:
                    self.delete()
                    self.receipt["cleanup"] = "removed"
                except Exception as err:
                    log("cleanup_error", error=f"{type(err).__name__}: {err}"[:500])
                    ok = False
            else:
                # Kept for inspection; the Job's TTL deletes the Job and, through the owner
                # references, the copy.
                self.receipt["cleanup"] = "kept for inspection until the Job TTL"
            self.receipt["finished_at"] = now().isoformat()
            self.receipt["result"] = "pass" if ok else "fail"
            print("weather_pg_restore_drill_receipt " + json.dumps(self.receipt, sort_keys=True))
        return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", action="store_true", help="print the rendered manifest only")
    parser.add_argument("--operation", default="plan")
    args = parser.parse_args()
    if args.plan:
        expires = (now().date() + dt.timedelta(days=2)).isoformat()
        print(manifest(args.operation, expires, None), end="")
        return 0
    return 0 if Drill().run() else 1


if __name__ == "__main__":
    sys.exit(main())
