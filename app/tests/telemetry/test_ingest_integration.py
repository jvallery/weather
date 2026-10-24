# SPDX-License-Identifier: MIT
"""Postgres, outage and abrupt-process tests; the approved fleet CI image only."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.telemetry.drain import Drain
from weather.telemetry.receiver import AdmissionConfig, Datagram, Receiver
from weather.telemetry.spool import Receipt, Spool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]

ST_ROW = [1700000000, 0.1, 0.2, 0.3, 90, 3, 1000, 20, 50, 100, 1, 50, 0, 0, 10, 2, 2.5, 1]


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
                await c.execute(text("TRUNCATE weather.station,weather.telemetry_receipt CASCADE"))
                await c.execute(text("DELETE FROM weather.outbox WHERE topic='telemetry.conflict'"))
                await c.execute(text("TRUNCATE weather.telemetry_loss_snapshot"))
                station = (
                    await c.execute(
                        text("""
                    INSERT INTO weather.station(alias,site,private_configuration)
                    VALUES('synthetic-station','synthetic-site','{}') RETURNING id
                """)
                    )
                ).scalar_one()
                for serial, alias, kind in [
                    ("SYNTHETIC-ST", "synthetic-device", "tempest"),
                    ("SYNTHETIC-HUB", "synthetic-hub", "hub"),
                ]:
                    await c.execute(
                        text("""
                        INSERT INTO weather.device(station_id,alias,private_id,kind,valid_from,
                                                   private_configuration)
                        VALUES(:station,:alias,:serial,:kind,'2020-01-01T00:00Z','{}')
                    """),
                        {"station": station, "alias": alias, "serial": serial, "kind": kind},
                    )
        finally:
            await db.dispose()

    asyncio.run(prepare())


def payload(family: str = "rapid_wind", **kwargs: Any) -> bytes:
    body = {"serial_number": "SYNTHETIC-ST", "type": family, **kwargs}
    if family == "rapid_wind" and "ob" not in body:
        body["ob"] = [1700000000, 2.3, 128]
    return json.dumps(body).encode()


def append(spool: Spool, raw: bytes, **kwargs: Any) -> Receipt:
    r = Receipt.create(
        raw,
        spool.incarnation,
        1700000001000000000,
        time.monotonic_ns(),
        7,
        "192.0.2.1",
        50222,
        **kwargs,
    )
    assert spool.append(r)
    return r


async def counts(db: AsyncEngine) -> tuple[int, int, int]:
    async with db.connect() as c:
        return tuple(
            (
                await c.execute(
                    text("""
            SELECT (SELECT count(*) FROM weather.telemetry_receipt),
                   (SELECT count(*) FROM weather.rapid_wind),
                   (SELECT count(*) FROM weather.receipt_row_provenance)
        """)
                )
            ).one()
        )  # type: ignore[return-value]


def test_t01_t02_duplicate_datagrams_and_uncertain_ack(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            first = append(spool, payload())
            append(spool, payload())
            drain = Drain(spool, db)
            # Commit returned; simulate loss of local acknowledgement by leaving the spool intact.
            async with db.begin() as c:
                await drain._receipt(c, first)
            assert spool.stats()["depth"] == 2
            assert await drain.once() == 2
            assert await counts(db) == (2, 1, 2)
            # Same UUID/bytes/metadata replay never creates another receipt/provenance row.
            for _ in range(3):
                assert spool.append(first)
                assert await drain.once() == 1
            assert await counts(db) == (2, 1, 2)
            assert spool.stats()["depth"] == 0
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_t03_receipt_collision_retains_both_payloads(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            first = append(spool, payload())
            alternate = payload(ob=[1700000000, 9, 128])
            append(spool, alternate, receipt_id=first.id)
            assert await Drain(spool, db).once() == 2
            async with db.connect() as c:
                evidence = (
                    await c.execute(
                        text("""
                    SELECT r.raw_bytes,i.alternate_raw,i.alternate_sha256
                    FROM weather.telemetry_receipt r JOIN weather.receipt_integrity_incident i
                      ON i.receipt_id=r.id
                """)
                    )
                ).one()
                assert tuple(evidence) == (
                    first.raw,
                    alternate,
                    hashlib.sha256(alternate).hexdigest(),
                )
            assert await counts(db) == (1, 1, 1)
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_t04_t05_t06_t07_all_rows_and_raw_quarantine(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            later = [1700000060, *ST_ROW[1:]]
            append(spool, payload("obs_st", obs=[ST_ROW, [], later], future_field={"kept": True}))
            malformed = [b'{"type":"rapid_wind","type":"obs_st"}', bytes.fromhex("fffe00")]
            for raw in malformed:
                append(spool, raw)
            append(spool, payload("synthetic_future_family", values=[1, None, 3]))
            assert await Drain(spool, db).once() == 4
            async with db.connect() as c:
                assert (
                    await c.execute(text("SELECT count(*) FROM weather.obs_st"))
                ).scalar_one() == 2
                assert (
                    await c.execute(text("SELECT count(*) FROM weather.receipt_row_provenance"))
                ).scalar_one() == 3
                retained = (
                    await c.execute(
                        text("""
                    SELECT raw_bytes,parsed_json IS NULL FROM weather.telemetry_receipt
                    WHERE parse_state='quarantined'
                """)
                    )
                ).all()
                assert {row.raw_bytes for row in retained} == set(malformed)
                assert all(row[1] for row in retained)
                assert (
                    await c.execute(
                        text("""
                    SELECT parsed_json->'values' FROM weather.telemetry_receipt
                    WHERE parse_state='unsupported'
                """)
                    )
                ).scalar_one() == [1, None, 3]
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_t08_t09_conflict_fields_and_ambiguous_events(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            alternate = list(ST_ROW)
            alternate[7] = 23
            append(spool, payload("obs_st", obs=[ST_ROW]))
            append(spool, payload("obs_st", obs=[alternate]))
            for _ in range(3):
                append(spool, payload("evt_strike", evt=[1700000000, 10, 100]))
            assert await Drain(spool, db).once() == 5
            async with db.connect() as c:
                row = (await c.execute(text('SELECT "values",quality FROM weather.obs_st'))).one()
                assert row.values["air_temperature"] == 20
                assert row.values["lightning_strike_count"] == 2
                assert not row.quality["fields"]["air_temperature"]["eligible"]
                assert row.quality["fields"]["relative_humidity"]["eligible"]
                assert (
                    await c.execute(
                        text("""
                    SELECT quality->>'event_multiplicity' FROM weather.evt_strike
                """)
                    )
                ).scalar_one() == "ambiguous"
                assert (
                    await c.execute(
                        text("""
                    SELECT count(*) FROM weather.outbox WHERE topic='telemetry.conflict'
                """)
                    )
                ).scalar_one() == 1
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("point", ["after_spool_commit", "after_db_commit"])
def test_t16_abrupt_kill_replays_exactly_once(tmp_path: Path, point: str) -> None:
    # Guard and fixture preparation happened before subprocess creation. Never run on the Mac.
    source = """
import asyncio,os,time
from pathlib import Path
from test_ingest_integration import engine,payload
from weather.telemetry.spool import Spool,Receipt
from weather.telemetry.drain import Drain
async def run():
    spool=Spool(Path(os.environ["SYNTHETIC_SPOOL"]),1<<20)
    receipt=Receipt.create(payload(),spool.incarnation,1700000001000000000,
        time.monotonic_ns(),7,"192.0.2.1",50222)
    assert spool.append(receipt)
    if os.environ["SYNTHETIC_KILL_POINT"]=="after_db_commit":
        db=engine("weather_ingest")
        async with db.begin() as c:
            await Drain(spool,db)._receipt(c,receipt)
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
            assert await Drain(spool, db).once() == 1
            assert await counts(db) == (1, 1, 1)
            async with db.connect() as c:
                row = (
                    await c.execute(text("SELECT id,raw_bytes FROM weather.telemetry_receipt"))
                ).one()
                assert row.id == stable.id and row.raw_bytes == stable.raw
            assert spool.stats()["depth"] == 0 and spool.stats()["volatile_loss_unknown"]
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_db_transport_outage_receiving_continues_and_source_time_unchanged(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        await guard(db)
        url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username="weather_ingest")
        unavailable = create_async_engine(
            url.update_query_dict({"host": str(tmp_path / "unavailable-socket")}),
            echo=False,
            connect_args={"timeout": 1},
        )
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            receiver = Receiver(
                AdmissionConfig(7, "192.0.2.1", "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"})), spool
            )
            for n in range(3):
                r = receiver.accept(
                    Datagram(
                        payload(ob=[1700000000 + n * 3, 2.3, 128]),
                        7,
                        "192.0.2.1",
                        50222,
                        1700000001000000000 + n * 3_000_000_000,
                        time.monotonic_ns(),
                    )
                )
                assert r is not None
            assert await Drain(spool, unavailable).once() == 0
            assert spool.stats()["depth"] == 3
            assert await Drain(spool, db).once() == 3
            assert spool.stats()["depth"] == 0
            async with db.connect() as c:
                times = (
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
                assert times == [1700000000, 1700000003, 1700000006]
        finally:
            spool.close()
            await unavailable.dispose()
            await db.dispose()

    asyncio.run(check())


def test_t18_t20_source_reset_and_equal_values_are_distinct_source_keys(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_ingest")
        spool = Spool(tmp_path / "spool", 1 << 20)
        try:
            for stamp in [1700000000, 1699999900, 1700000000, 1700000003]:
                append(spool, payload(ob=[stamp, 2.3, 128]))
            drain = Drain(spool, db)
            assert await drain.once() == 4, drain.last_error_code
            assert await counts(db) == (4, 3, 4)
            async with db.connect() as c:
                times = (
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
                assert times == [1699999900, 1700000000, 1700000003]
        finally:
            spool.close()
            await db.dispose()

    asyncio.run(check())


def test_t16_uncommitted_sqlite_tail_is_not_an_invented_receipt(tmp_path: Path) -> None:
    source = """
import os,time
from pathlib import Path
from weather.telemetry.spool import Spool,Receipt
spool=Spool(Path(os.environ["SYNTHETIC_SPOOL"]),1<<20)
for raw in [b"synthetic-committed-1",b"synthetic-committed-2"]:
    assert spool.append(Receipt.create(raw,spool.incarnation,1700000001000000000,
        time.monotonic_ns(),7,"192.0.2.1",50222))
db=spool._conn_required()
db.execute("BEGIN IMMEDIATE")
db.execute("INSERT INTO receipt(receipt_id,metadata_json,metadata_sha256,sha256,raw,charge) "
           "SELECT receipt_id,metadata_json,metadata_sha256,sha256,raw,charge FROM receipt LIMIT 1")
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
        assert spool.stats()["volatile_loss_unknown"]
        assert not (tmp_path / "spool" / "quarantine").exists()
    finally:
        spool.close()
