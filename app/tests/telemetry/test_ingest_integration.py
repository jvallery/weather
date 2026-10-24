# SPDX-License-Identifier: MIT
"""Postgres, outage and abrupt-process tests; the approved fleet CI image only."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.telemetry import drain as drain_module
from weather.telemetry.drain import Drain, reproject, seed
from weather.telemetry.receiver import AdmissionConfig, Datagram, Receiver
from weather.telemetry.spool import Receipt, Spool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]

ST_ROW = [1700000000, 0.1, 0.2, 0.3, 90, 3, 1000, 20, 50, 100, 1, 50, 0, 0, 10, 2, 2.5, 1]
HUB, DEVICES = "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"})
RECEIVED_NS = 1700000001000000000
CONTRACT = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/contracts/telemetry-vectors.json").read_text()
)


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Cluster-only telemetry integration")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    path = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or path.name != "socket"
        or not path.is_absolute()
        or not path.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Disposable socket-only fixture required")
    return create_async_engine(url, echo=False, pool_size=2, max_overflow=0)


async def guard(db: AsyncEngine) -> None:
    async with db.connect() as connection:
        row = (
            await connection.execute(
                text("""
            SELECT current_database(),current_setting('server_version_num')::integer,
                   current_setting('weather.integration_test',true),inet_server_addr()
        """)
            )
        ).one()
        assert tuple(row) == ("weather_ci", 160015, "on", None)


@pytest.fixture(autouse=True)
def isolated_telemetry() -> None:
    async def prepare() -> None:
        db = engine()
        try:
            await guard(db)
            async with db.begin() as c:
                from weather.db.models import metadata

                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
        finally:
            await db.dispose()

    asyncio.run(prepare())


def payload(family: str = "rapid_wind", **kwargs: Any) -> bytes:
    body = {"serial_number": "SYNTHETIC-ST", "hub_sn": HUB, "type": family, **kwargs}
    if family == "rapid_wind" and "ob" not in body:
        body["ob"] = [1700000000, 2.3, 128]
    return json.dumps(body).encode()


def append(spool: Spool, raw: bytes, **kwargs: Any) -> Receipt:
    r = Receipt.create(raw, spool.incarnation, RECEIVED_NS, **kwargs)
    assert spool.append(r)
    return r


def seeded(spool: Spool, db: AsyncEngine) -> Drain:
    return Drain(spool, db, 120, HUB, DEVICES)


async def scalar(db: AsyncEngine, sql: str) -> Any:
    async with db.connect() as c:
        return (await c.execute(text(sql))).scalar_one()


async def counts(db: AsyncEngine, family: str = "rapid_wind") -> tuple[int, int]:
    async with db.connect() as c:
        return tuple(
            (
                await c.execute(
                    text(f"""
            SELECT (SELECT count(*) FROM weather.telemetry_receipt),
                   (SELECT count(*) FROM weather.{family})
        """)
                )
            ).one()
        )  # type: ignore[return-value]


async def source_epochs(db: AsyncEngine) -> list[int]:
    async with db.connect() as c:
        return list(
            (
                await c.execute(
                    text("""
            SELECT extract(epoch FROM source_ts)::bigint FROM weather.rapid_wind
            ORDER BY source_ts
        """)
                )
            )
            .scalars()
            .all()
        )


async def postgres_vector(vector: dict[str, Any], tmp_path: Path) -> None:
    db = engine("weather_ingest")
    spool = Spool(tmp_path / "spool", 1 << 20)
    data, expect = vector["input"], vector["expect"]
    try:
        drain = seeded(spool, db)
        if vector["id"] == "T01":
            for _ in range(data["deliveries"]):
                append(spool, json.dumps(data["packet"]).encode())
            assert await drain.once() == data["deliveries"], drain.last_error_code
        elif vector["id"] == "T02":
            raw = json.dumps(data["packet"]).encode()
            receipt = append(spool, raw, receipt_id=uuid.UUID(data["receipt_id"]))
            # Commit returned, but the local acknowledgement was lost: replay the same ID.
            async with db.begin() as c:
                await seed(c, HUB, DEVICES)
                await drain._receipt(c, receipt, Counter())
            for _ in range(data["attempts"] - 1):
                assert spool.stats()["depth"] >= 1
                assert await drain.once() >= 1, drain.last_error_code
                assert spool.append(receipt)
            assert await drain.once() == 1
            assert spool.stats()["depth"] == 0
        else:
            stamps = data.get("arrival_source_epochs") or [s[0] for s in data["samples"]]
            first = [append(spool, payload(ob=[stamp, 2.3, 128])) for stamp in stamps][0]
            assert await drain.once() == len(stamps), drain.last_error_code
        receipts, rows = await counts(db)
        assert receipts == expect.get("raw_receipts", receipts)
        assert rows == expect.get("canonical_observations", rows)
        if "canonical_source_epochs" in expect:
            assert await source_epochs(db) == expect["canonical_source_epochs"]
            kept = await scalar(
                db,
                "SELECT receipt_id FROM weather.rapid_wind "
                "WHERE source_ts=to_timestamp(1700000000)",
            )
            assert (kept == first.id) is expect["first_row_kept"]
    finally:
        spool.close()
        await db.dispose()


@pytest.mark.parametrize(
    "vector",
    [pytest.param(v, id=v["id"]) for v in CONTRACT["vectors"] if v["suite"] == "integration"],
)
def test_integration_vector(vector: dict[str, Any], tmp_path: Path) -> None:
    asyncio.run(postgres_vector(vector, tmp_path))


def test_seed_is_idempotent_and_aliases_are_public(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        try:
            for devices in (DEVICES, DEVICES, DEVICES | {"SYNTHETIC-ST-2"}):
                async with db.begin() as c:
                    await seed(c, HUB, devices)
            async with db.connect() as c:
                rows = (
                    await c.execute(
                        text("SELECT alias,kind FROM weather.device ORDER BY alias")
                    )
                ).all()
                assert [tuple(r) for r in rows] == [
                    ("hub", "hub"),
                    ("tempest", "tempest"),
                    ("tempest-2", "tempest"),
                ]
            assert await scalar(db, "SELECT count(*) FROM weather.station") == 1
        finally:
            await db.dispose()

    asyncio.run(check())


def test_all_rows_and_raw_quarantine(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            later = [1700000060, *ST_ROW[1:]]
            append(spool, payload("obs_st", obs=[ST_ROW, [], later], future_field={"kept": True}))
            # The receiver rejects these; the drain still keeps raw bytes if one is spooled.
            malformed = [b'{"type":"rapid_wind","type":"obs_st"}', bytes.fromhex("fffe00")]
            for raw in malformed:
                append(spool, raw)
            append(spool, payload("synthetic_future_family", values=[1, None, 3]))
            append(spool, payload(), quarantine_reason="missing_hub_identity")
            drain = seeded(spool, db)
            assert await drain.once() == 5, drain.last_error_code
            assert await scalar(db, "SELECT count(*) FROM weather.obs_st") == 2
            assert await scalar(db, "SELECT count(*) FROM weather.rapid_wind") == 0
            assert drain.counts["unprojected"] == 1
            async with db.connect() as c:
                retained = (
                    await c.execute(
                        text("""
                    SELECT raw_bytes,parse_state,parse_reason FROM weather.telemetry_receipt
                    WHERE parse_state<>'parsed' ORDER BY parse_reason NULLS LAST
                """)
                    )
                ).all()
                assert [(r.parse_state, r.parse_reason) for r in retained] == [
                    ("quarantined", "duplicate_json_key"),
                    ("quarantined", "malformed_utf8"),
                    ("quarantined", "missing_hub_identity"),
                    ("unsupported", None),
                ]
                assert set(malformed) <= {r.raw_bytes for r in retained}
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_one_receipt_raises_while_the_others_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = drain_module.project

    async def poisoned(db: Any, receipt_id: uuid.UUID, packet: Any, counts: Any) -> int:
        if packet.rows and packet.rows[0].source_ts == 1700000003:
            # Leave a partial write inside the SAVEPOINT, then fail deterministically.
            await original(db, receipt_id, packet, counts)
            raise RuntimeError("synthetic deterministic failure")
        return await original(db, receipt_id, packet, counts)

    original_store, failed = drain_module.store, []

    async def flaky(db: Any, receipt: Any, state: str, reason: str | None) -> bool:
        # The first store of an admission-quarantined receipt fails, sending it to the fallback.
        if reason == "missing_hub_identity" and not failed:
            failed.append(receipt.id)
            raise RuntimeError("synthetic store failure")
        return await original_store(db, receipt, state, reason)

    monkeypatch.setattr(drain_module, "project", poisoned)
    monkeypatch.setattr(drain_module, "store", flaky)

    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            for stamp in (1700000000, 1700000003, 1700000006):
                append(spool, payload(ob=[stamp, 2.3, 128]))
            raw = payload(ob=[1700000009, 2.3, 128])
            append(spool, raw, quarantine_reason="missing_hub_identity")
            drain = seeded(spool, db)
            assert await drain.once() == 4, drain.last_error_code
            assert spool.stats()["depth"] == 0 and drain.counts["drain_quarantined"] == 2
            assert drain.counts["committed"] == 4 and drain.counts["projected"] == 2
            assert await source_epochs(db) == [1700000000, 1700000006]
            async with db.connect() as c:
                reasons = (
                    await c.execute(
                        text("""
                    SELECT parse_reason FROM weather.telemetry_receipt
                    WHERE parse_state='quarantined' ORDER BY parse_reason
                """)
                    )
                ).scalars().all()
            # The fallback keeps the admission reason, so reproject still never projects it.
            assert reasons == ["drain_failed", "missing_hub_identity"]
            # Once the defect is fixed, reproject recovers the quarantined receipt's row.
            monkeypatch.setattr(drain_module, "project", original)
            receipts, rows, _ = await reproject(db, datetime(2023, 1, 1, tzinfo=UTC))
            assert (receipts, rows) == (3, 1)
            assert await source_epochs(db) == [1700000000, 1700000003, 1700000006]
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_unseeded_device_then_reproject(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            append(spool, payload())
            append(spool, payload("obs_st", obs=[ST_ROW]))
            drain = Drain(spool, db)
            assert await drain.once() == 2, drain.last_error_code
            assert await counts(db) == (2, 0)
            assert drain.counts["unprojected"] == 2
            async with db.begin() as c:
                await seed(c, HUB, DEVICES)
            since = datetime(2023, 11, 14, tzinfo=UTC)
            assert (await reproject(db, since, page=1))[:2] == (2, 2)
            assert await counts(db) == (2, 1)
            assert await scalar(db, "SELECT count(*) FROM weather.obs_st") == 1
            # Idempotent: a second pass finds every row already present.
            assert (await reproject(db, since))[:2] == (2, 0)
            assert (await reproject(db, datetime(2030, 1, 1, tzinfo=UTC)))[:2] == (0, 0)
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("point", ["after_spool_commit", "after_db_commit"])
def test_abrupt_kill_replays_exactly_once(tmp_path: Path, point: str) -> None:
    # Guard and fixture preparation happened before subprocess creation. Never run on the Mac.
    source = """
import asyncio,os
from pathlib import Path
from test_ingest_integration import DEVICES,HUB,engine,payload
from collections import Counter
from weather.telemetry.drain import Drain,seed
from weather.telemetry.spool import Spool,Receipt
async def run():
    spool=Spool(Path(os.environ["SYNTHETIC_SPOOL"]),1<<20)
    receipt=Receipt.create(payload(),spool.incarnation,1700000001000000000)
    assert spool.append(receipt)
    if os.environ["SYNTHETIC_KILL_POINT"]=="after_db_commit":
        db=engine("weather_ingest")
        async with db.begin() as c:
            await seed(c,HUB,DEVICES)
            await Drain(spool,db)._receipt(c,receipt,Counter())
    os._exit(17)
asyncio.run(run())
"""
    env = {
        **os.environ,
        "SYNTHETIC_SPOOL": str(tmp_path / "spool"),
        "SYNTHETIC_KILL_POINT": point,
        "PYTHONPATH": os.pathsep.join([str(Path("app").resolve()), str(Path(__file__).parent)]),
    }
    child = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True, timeout=40)
    assert child.returncode == 17, "Abrupt-process fixture did not reach its bounded kill point"

    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            stable = spool.batch()[0].receipt
            drain = seeded(spool, db)
            await drain.once()
            assert drain.counts["drain_errors"] == 0, drain.last_error_code
            assert await counts(db) == (1, 1)
            async with db.connect() as c:
                row = (
                    await c.execute(text("SELECT id,raw_bytes FROM weather.telemetry_receipt"))
                ).one()
                assert row.id == stable.id and row.raw_bytes == stable.raw
            assert spool.stats()["depth"] == 0
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_database_outage_then_recovery_loses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = drain_module.project

    async def check() -> None:
        admin, db, doomed = engine(), engine("weather_ingest"), engine("weather_ingest")
        await guard(db)
        url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username="weather_ingest")
        unavailable = create_async_engine(
            url.update_query_dict({"host": str(tmp_path / "unavailable-socket")}),
            echo=False,
            connect_args={"timeout": 1},
        )
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            receiver = Receiver(AdmissionConfig(7, HUB, DEVICES), spool)
            # Already seeded, so every attempt reaches spool.batch() and the batch transaction.
            down = Drain(spool, unavailable)
            for n in range(3):
                r = receiver.accept(
                    Datagram(
                        payload(ob=[1700000000 + n * 3, 2.3, 128]),
                        7,
                        "192.0.2.1",
                        50222,
                        RECEIVED_NS + n * 3_000_000_000,
                    )
                )
                assert r is not None
                # Receiving continues during the outage; nothing is acknowledged.
                assert await down.once() == 0
                assert spool.stats()["depth"] == n + 1
            assert down.counts["drain_errors"] == 3 and down.counts["spool_ack_errors"] == 0

            async def terminated(db: Any, receipt_id: uuid.UUID, packet: Any, counts: Any) -> int:
                # Every receipt is written inside the batch; the connection dies before COMMIT.
                rows = await original(db, receipt_id, packet, counts)
                if packet.rows[0].source_ts == 1700000006:
                    pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                    async with admin.connect() as c:
                        stmt = text("SELECT pg_terminate_backend(:pid,5000)")
                        assert (await c.execute(stmt, {"pid": pid})).scalar_one()
                return rows

            monkeypatch.setattr(drain_module, "project", terminated)
            # Unseeded, so project() also counts unprojected rows that must never be reported.
            lost = Drain(spool, doomed)
            assert await lost.once() == 0
            monkeypatch.setattr(drain_module, "project", original)
            assert lost.counts["drain_errors"] == 1 and lost.counts["spool_ack_errors"] == 0
            assert lost.counts["committed"] == lost.counts["unprojected"] == 0
            assert spool.stats()["depth"] == 3
            assert await counts(db) == (0, 0)
            up = seeded(spool, db)
            assert await up.once() == 3, up.last_error_code
            assert spool.stats()["depth"] == 0
            assert up.counts["committed"] == up.counts["projected"] == 3
            assert await counts(db) == (3, 3)
            assert await source_epochs(db) == [1700000000, 1700000003, 1700000006]
            assert await up.once() == 0 and await counts(db) == (3, 3)
        finally:
            spool.close()
            for item in (unavailable, doomed, db, admin):
                await item.dispose()

    asyncio.run(check())


def test_uncommitted_sqlite_tail_is_not_an_invented_receipt(tmp_path: Path) -> None:
    source = """
import os
from pathlib import Path
from weather.telemetry.spool import Spool,Receipt
spool=Spool(Path(os.environ["SYNTHETIC_SPOOL"]),1<<20)
for raw in [b"synthetic-committed-1",b"synthetic-committed-2"]:
    assert spool.append(Receipt.create(raw,spool.incarnation,1700000001000000000))
db=spool._conn_required()
db.execute("BEGIN IMMEDIATE")
db.execute("INSERT INTO receipt(receipt_id,incarnation,received_ns,sha256,raw,charge) "
           "SELECT receipt_id,incarnation,received_ns,sha256,raw,charge FROM receipt LIMIT 1")
os._exit(17)
"""
    env = {
        **os.environ,
        "SYNTHETIC_SPOOL": str(tmp_path / "spool"),
        "PYTHONPATH": str(Path("app").resolve()),
    }
    child = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True, timeout=10)
    assert child.returncode == 17
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        assert [item.receipt.raw for item in spool.batch()] == [
            b"synthetic-committed-1",
            b"synthetic-committed-2",
        ]
        assert not (tmp_path / "spool" / "quarantine").exists()
    finally:
        spool.close()


def test_public_observations_gate_on_validity_and_private_tables_denied(tmp_path: Path) -> None:
    from sqlalchemy.exc import DBAPIError

    async def check() -> None:
        ingest = engine("weather_ingest")
        api = engine("weather_api")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            invalid = list(ST_ROW)
            invalid[0], invalid[8], invalid[12] = 1700000060, 101, 1
            future = [1700009999, *ST_ROW[1:]]
            append(spool, payload("obs_st", obs=[ST_ROW, invalid, future]))
            drain = seeded(spool, ingest)
            assert await drain.once() == 1, drain.last_error_code
            async with api.begin() as c:
                rows = (
                    (
                        await c.execute(
                            text("SELECT * FROM public_api.observations ORDER BY observed_at")
                        )
                    )
                    .mappings()
                    .all()
                )
                public = json.dumps([dict(r) for r in rows], default=str)
                assert "SYNTHETIC" not in public and "192.0.2.1" not in public
                assert "battery" not in rows[0]["values"]
                assert rows[0]["values"]["air_temperature"] == 20
                assert rows[0]["quality"] == {"valid": True, "reason": "valid", "invalid": []}
                assert rows[1]["values"]["relative_humidity"] is None
                assert rows[1]["values"]["precip_accumulated"] == 1
                assert rows[1]["quality"]["invalid"] == ["relative_humidity"]
                assert rows[2]["quality"] == {
                    "valid": False,
                    "reason": "future_timestamp",
                    "invalid": [],
                }
                assert all(value is None for value in rows[2]["values"].values())
                forged = (
                    await c.execute(
                        text("""
                    SELECT public_api.public_metric_values(CAST(:v AS jsonb),CAST(:q AS jsonb)),
                           public_api.public_metric_quality(CAST(:r AS jsonb))
                """),
                        {
                            "v": json.dumps({"air_temperature": 1}),
                            "q": json.dumps({"valid": "true", "invalid": "air_temperature"}),
                            "r": json.dumps({"reason": "SYNTHETIC-SECRET"}),
                        },
                    )
                ).one()
                assert forged[0] == {"air_temperature": None}
                assert forged[1] == {"valid": False, "reason": "unknown", "invalid": []}
                for table in ("telemetry_receipt", "device", "obs_st"):
                    with pytest.raises(DBAPIError) as error:
                        async with c.begin_nested():
                            await c.execute(text("SELECT * FROM weather." + table))
                    assert getattr(error.value.orig, "sqlstate", None) == "42501"
        finally:
            spool.close()
            await ingest.dispose()
            await api.dispose()

    asyncio.run(check())
