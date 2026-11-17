# SPDX-License-Identifier: MIT
"""weather#77 media drill driver (runs in the production weather-app image, in the drill pod).

Copies one complete local day of weather-media and weather-originals objects from B2 custody
(object-custody-vallery/object-custody/<bucket>/<key>, reader weather-custody-restore-reader
referenced in place) into the pod's own Garage, verifies every SHA-256 against the catalog
copy, withholds one object, runs `weather reconcile-objects` for both buckets and checks that
exactly the withheld or custody-missing objects were marked unavailable. Prints metadata only
(counts, bytes, keys are content hashes, timings) and never a credential.
"""

import asyncio
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text
from weather.db.connection import create_database_engine, wait_for_database
from weather.publish.objects import ObjectStore, StoreError

WORK = Path("/work")
DAY = os.environ["DRILL_DAY"]
DAY_DATE = datetime.date.fromisoformat(DAY)  # asyncpg binds a date, not a string
B2 = "https://s3.us-west-000.backblazeb2.com"
INDAY = "(created_at AT TIME ZONE 'America/Denver')::date = CAST(:day AS date)"


def log(**fields):
    print(json.dumps(fields, sort_keys=True), flush=True)


async def fetch(custody: ObjectStore, key: str, size: int, target: Path) -> str | None:
    """Stream one custody object to `target`; return its SHA-256, or None when custody lacks it."""
    digest = hashlib.sha256()
    try:
        span = await custody.open_span(key, 0, size - 1, size)
    except StoreError as error:
        if error.status in (404, 416):
            return None
        raise
    try:
        with target.open("wb") as handle:
            async for chunk in span.chunks(1 << 20):
                digest.update(chunk)
                handle.write(chunk)
    finally:
        await span.aclose()
    return digest.hexdigest()


async def main() -> int:
    start = time.time()
    while not Path("/gdata/ready").exists():
        await asyncio.sleep(2)
    os.environ["DATABASE_CREDENTIAL_DIRECTORY"] = "/keys/db-weather_worker"
    engine = create_database_engine()
    await wait_for_database(engine, timeout=7200)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(f"""SELECT id, bucket, class, object_key, sha256, bytes, content_type
                         FROM weather.media_asset WHERE state='available' AND {INDAY}
                         ORDER BY bucket, class, bytes, id"""),
                {"day": DAY_DATE},
            )
        ).all()
    custody = ObjectStore(
        endpoint=B2,
        bucket="object-custody-vallery",
        region=os.environ["CUSTODY_REGION"],
        access_key=os.environ["CUSTODY_ACCESS_KEY_ID"],
        secret_key=os.environ["CUSTODY_SECRET_ACCESS_KEY"],
    )
    os.environ.update(
        GARAGE_ENDPOINT="http://127.0.0.1:3900", GARAGE_CREDENTIAL_DIRECTORY="/keys/garage"
    )
    drill = {b: ObjectStore.from_environment(b) for b in ("weather-media", "weather-originals")}
    stills = [r for r in rows if r.bucket == "weather-media" and r[2] == "still"]
    withheld = stills[0] if stills else None
    tally: dict[str, dict[str, int]] = {}
    mismatches, custody_missing = [], []
    copy_start = time.time()
    tmp = WORK / "object.tmp"
    for row in rows:
        t = tally.setdefault(
            row.bucket,
            {
                "catalog_rows": 0,
                "copied": 0,
                "bytes": 0,
                "withheld": 0,
                "custody_missing": 0,
                "sha256_mismatch": 0,
            },
        )
        t["catalog_rows"] += 1
        if withheld is not None and row.id == withheld.id:
            t["withheld"] += 1
            continue
        got = await fetch(
            custody, f"object-custody/{row.bucket}/{row.object_key}", int(row.bytes), tmp
        )
        if got is None:
            t["custody_missing"] += 1
            custody_missing.append(str(row.id))
            continue
        if got != row.sha256 or tmp.stat().st_size != int(row.bytes):
            t["sha256_mismatch"] += 1
            mismatches.append(row.object_key)
            continue
        await drill[row.bucket].put(
            row.object_key,
            sha256=row.sha256,
            size=int(row.bytes),
            content_type=row.content_type,
            path=tmp,
        )
        t["copied"] += 1
        t["bytes"] += int(row.bytes)
    tmp.unlink(missing_ok=True)
    copy_end = time.time()
    log(
        event="weather_drill_copy",
        day=DAY,
        buckets=tally,
        mismatches=mismatches,
        withheld=withheld.object_key if withheld else None,
        copy_seconds=round(copy_end - copy_start),
    )

    reconcile = {}
    env = {**os.environ}
    for bucket in ("weather-media", "weather-originals"):
        result = subprocess.run(
            ["weather", "reconcile-objects", "--bucket", bucket, "--rate", "200"],
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        line = (result.stdout.strip().splitlines() or ["{}"])[-1]
        reconcile[bucket] = {
            "rc": result.returncode,
            "counts": json.loads(line) if line.startswith("{") else {},
        }
    async with engine.connect() as connection:
        marked = {
            str(r.id)
            for r in (
                await connection.execute(
                    text(
                        f"SELECT id FROM weather.media_asset WHERE state='unavailable' AND {INDAY}"
                    ),
                    {"day": DAY_DATE},
                )
            ).all()
        }
    expected = set(custody_missing) | ({str(withheld.id)} if withheld else set())
    exact = marked == expected

    # Achieved RPO: newest production asset (read by the orchestrator) present in custody.
    rpo = None
    candidates = json.loads(Path("/drill/rpo-candidates.json").read_text())
    absent_newer = 0
    for c in candidates:
        info = await custody.head(f"object-custody/{c['bucket']}/{c['object_key']}")
        if info is not None:
            rpo = {
                "newest_present_created_epoch": c["created_epoch"],
                "achieved_rpo_seconds": round(start - c["created_epoch"]),
                "newer_production_objects_not_yet_in_custody": absent_newer,
            }
            break
        absent_newer += 1
    await custody.aclose()
    for store in drill.values():
        await store.aclose()
    await engine.dispose()
    ok = (
        exact
        and not mismatches
        and withheld is not None
        and all(v["rc"] == 0 for v in reconcile.values())
        and all(t["copied"] > 0 for t in tally.values())
    )
    receipt = {
        "event": "weather_media_drill_receipt",
        "result": "pass" if ok else "fail",
        "source_tier": "b2",
        "day": DAY,
        "start_epoch": round(start),
        "copy_start_epoch": round(copy_start),
        "copy_end_epoch": round(copy_end),
        "buckets": tally,
        "sha256_mismatches": len(mismatches),
        "withheld": withheld.object_key if withheld else None,
        "reconcile": reconcile,
        "marked_unavailable_exactly_expected": exact,
        "marked_unavailable": len(marked),
        "expected_unavailable": len(expected),
        "rpo": rpo,
        # Asset ids (UUIDs) for the orchestrator's port-forward probes.
        "probe_ids": {
            "withheld": str(withheld.id) if withheld else None,
            "restored_still": next((str(r.id) for r in stills[1:]), None),
            "original": next((str(r.id) for r in rows if r.bucket == "weather-originals"), None),
        },
    }
    (WORK / "receipt.json").write_text(json.dumps(receipt, sort_keys=True))
    log(**receipt)
    (WORK / "driver-done").write_text("pass" if ok else "fail")
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    # Stay up for the orchestrator's port-forward checks; the pod deadline bounds this.
    while not Path("/work/probes-done").exists():
        time.sleep(5)
    sys.exit(code)
