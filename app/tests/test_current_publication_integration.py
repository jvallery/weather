# SPDX-License-Identifier: MIT
"""UNRUN source preparation: requires merged 0001/0002/0003 and fleet-only PG.

Never run this module on the Mac. The parent issue's database integration runner
must supply its isolated PostgreSQL 16.15/vector0.8.6 Unix socket and test marker.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.db.ddl_v3 import statements
from weather.db.schema_v3 import ALL_FAMILIES, BINDINGS, EVIDENCE_TABLES

pytestmark = pytest.mark.integration
SOURCE = datetime(2026, 10, 23, 12, tzinfo=UTC)


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Current/publication tests require fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or socket.name != "socket"
        or not socket.is_absolute()
        or not socket.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Current/publication tests require their disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=5, max_overflow=0)


async def guard(connection: AsyncConnection) -> None:
    row = (
        await connection.execute(
            text("""SELECT current_database(),current_setting('server_version_num')::integer,
            current_setting('weather.integration_test',true),inet_server_addr(),
            (SELECT extversion FROM pg_extension WHERE extname='vector'),
            to_regclass('weather.metric_current') IS NOT NULL""")
        )
    ).one()
    assert tuple(row) == ("weather_ci", 160015, "on", None, "0.8.6", True)


@pytest.fixture(autouse=True)
def disposable_database() -> None:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                await guard(connection)
                names = (*EVIDENCE_TABLES, *ALL_FAMILIES, "metric_current", "telemetry_publication")
                await connection.execute(
                    text("TRUNCATE " + ",".join("weather." + n for n in names) + " CASCADE")
                )
                await connection.execute(text("TRUNCATE weather.device,weather.station CASCADE"))
                await connection.execute(
                    text("UPDATE weather.telemetry_publication_clock SET published_through=0")
                )
        finally:
            await db.dispose()

    asyncio.run(clean())


async def device(connection: AsyncConnection) -> UUID:
    alias = "fixture-" + uuid4().hex[:12]
    station = (
        await connection.execute(
            text("INSERT INTO weather.station(alias,site) VALUES(:alias,:alias) RETURNING id"),
            {"alias": alias},
        )
    ).scalar_one()
    return (
        await connection.execute(
            text("""INSERT INTO weather.device(station_id,alias,private_id,kind,valid_from)
            VALUES(:station,:alias,:private,'tempest',:start) RETURNING id"""),
            {
                "station": station,
                "alias": alias,
                "private": "fixture-private-" + uuid4().hex,
                "start": SOURCE - timedelta(days=100),
            },
        )
    ).scalar_one()


async def receipt(
    connection: AsyncConnection,
    *,
    identity: UUID | None = None,
    received: datetime = SOURCE,
) -> UUID:
    identity = identity or uuid4()
    raw = b"synthetic integration receipt"
    await connection.execute(
        text("""INSERT INTO weather.telemetry_receipt
        (id,received_at,raw_bytes,sha256,parse_state,parser_revision,receiver_incarnation)
        VALUES(:id,:received,:raw,:hash,'parsed','fixture','fixture')
        ON CONFLICT(id) DO NOTHING"""),
        {"id": identity, "received": received, "raw": raw, "hash": hashlib.sha256(raw).hexdigest()},
    )
    return identity


def quality(values: dict[str, Any], *, source_eligible: bool = True) -> dict[str, Any]:
    units = {field: unit for _, field, unit, _ in BINDINGS}
    return {
        "valid": source_eligible,
        "reason": "valid" if source_eligible else "future_timestamp",
        "source_time_eligible": source_eligible,
        "fields": {
            key: {
                "state": "valid" if value is not None else "invalid",
                "valid": value is not None,
                "eligible": value is not None and source_eligible,
                "reason": "valid" if value is not None else "out_of_range",
                "reported_unit": units[key],
                "canonical_unit": units[key],
                "range_policy": "v171-basic-domains-1",
                "support": {
                    "kind": "instantaneous",
                    "qualified": False,
                    "policy": "requires-source-time-hold-policy",
                },
                "private_extra": "never public",
            }
            for key, value in values.items()
        },
        "private_extra": "never public",
    }


async def observation(
    connection: AsyncConnection,
    device_id: UUID,
    *,
    source: datetime = SOURCE,
    received: datetime = SOURCE,
    values: dict[str, Any] | None = None,
    source_eligible: bool = True,
    identity: UUID | None = None,
    family: str = "obs_st",
    receipt_id: UUID | None = None,
    row_idx: int = 0,
) -> tuple[UUID, UUID]:
    assert family in ALL_FAMILIES
    identity = identity or uuid4()
    values = values if values is not None else {"air_temperature": 20, "relative_humidity": 50}
    receipt_id = receipt_id or await receipt(connection, received=received)
    await connection.execute(
        text(f"""INSERT INTO weather.{family}
        (id,device_id,receipt_id,message_type,source_ts,row_idx,"values",quality)
        VALUES(:id,:device,:receipt,:family,:source,:idx,
               CAST(:values AS jsonb),CAST(:quality AS jsonb))
        ON CONFLICT(device_id,message_type,source_ts,row_idx) DO NOTHING"""),
        {
            "id": identity,
            "device": device_id,
            "receipt": receipt_id,
            "family": family,
            "source": source,
            "idx": row_idx,
            "values": json.dumps(values),
            "quality": json.dumps(quality(values, source_eligible=source_eligible)),
        },
    )
    return identity, receipt_id


async def current(connection: AsyncConnection) -> dict[str, dict[str, Any]]:
    rows = (await connection.execute(text("SELECT * FROM public_api.metric_current"))).mappings()
    return {str(row["metric"]): dict(row) for row in rows}


async def incident(
    connection: AsyncConnection,
    observation_id: UUID,
    first_receipt: UUID,
    *,
    source: datetime = SOURCE,
) -> UUID:
    other = await receipt(connection)
    return (
        await connection.execute(
            text("""INSERT INTO weather.observation_conflict
            (family,observation_id,first_receipt_id,conflicting_receipt_id,fields,source_ts,received_at)
            VALUES('obs_st',:id,:first,:other,ARRAY['air_temperature'],:source,:source)
            RETURNING id"""),
            {"id": observation_id, "first": first_receipt, "other": other, "source": source},
        )
    ).scalar_one()


async def rejects(
    connection: AsyncConnection, sql: str, params: dict[str, Any], state: str
) -> None:
    with pytest.raises(DBAPIError) as failure:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    assert getattr(failure.value.orig, "sqlstate", None) == state


def test_source_time_projection_invalid_newer_fields_and_immutable_canonical_values() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, _ = await observation(connection, d)
                await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(minutes=1),
                    received=SOURCE + timedelta(days=1),
                )
                await observation(
                    connection, d, source=SOURCE + timedelta(days=1), source_eligible=False
                )
                # Equal source time from another bound family preserves the first pointer.
                await observation(connection, d, family="obs_air", values={"air_temperature": 99})
                before = await current(connection)
                assert before["temperature"]["observation_id"] == original
                assert before["temperature"]["revision"] == 1
                newer, _ = await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(minutes=1),
                    values={"air_temperature": None, "relative_humidity": 55},
                )
                after = await current(connection)
                assert after["temperature"]["observation_id"] == newer
                assert after["temperature"]["value"] is None
                assert after["temperature"]["field_quality"]["reason"] == "out_of_range"
                assert after["temperature"]["revision"] == 2
                assert after["humidity"]["value"] == 55
                await observation(
                    connection,
                    d,
                    family="rapid_wind",
                    source=SOURCE + timedelta(minutes=2),
                    values={"wind_speed": 3},
                )
                assert (await current(connection))["temperature"] == after["temperature"]
                await rejects(
                    connection,
                    "UPDATE weather.obs_st SET \"values\"='{}' WHERE id=:id",
                    {"id": original},
                    "55000",
                )
                await rejects(
                    connection,
                    "UPDATE weather.obs_st SET source_ts=source_ts+"
                    "interval '1 second' WHERE id=:id",
                    {"id": original},
                    "55000",
                )
                assert "private_extra" not in json.dumps(after, default=str)
        finally:
            await db.dispose()

    asyncio.run(check())


def test_conflict_invalidates_matching_current_only_and_retry_is_revision_noop() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                first, raw = await observation(connection, d)
                conflict = await incident(connection, first, raw)
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            async with db.begin() as connection:
                invalidated = await current(connection)
                assert invalidated["temperature"]["value"] is None
                assert invalidated["temperature"]["field_quality"]["reason"] == "payload_conflict"
                assert invalidated["temperature"]["revision"] == 2
                assert invalidated["humidity"]["value"] == 50
                assert invalidated["humidity"]["revision"] == 1
                assert (
                    await connection.execute(
                        text(
                            "SELECT \"values\"->>'air_temperature',receipt_id FROM weather.obs_st "
                            "WHERE id=:id"
                        ),
                        {"id": first},
                    )
                ).one() == ("20", raw)
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            async with db.begin() as connection:
                assert await current(connection) == invalidated
                await connection.execute(
                    text("""UPDATE weather.obs_st SET quality=
                    jsonb_set(quality,'{private_extra}','"changed private metadata"'::jsonb)
                    WHERE id=:id"""),
                    {"id": first},
                )
                assert await current(connection) == invalidated
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.outbox"))
                ).scalar_one() == 1
                newer, _ = await observation(connection, d, source=SOURCE + timedelta(minutes=1))
                t2 = await current(connection)
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            async with db.begin() as connection:
                assert (await current(connection))["temperature"]["observation_id"] == newer
                assert await current(connection) == t2
        finally:
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())


def test_conflict_rollback_keeps_current_and_outbox_atomic() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                first, raw = await observation(connection, d)
                conflict = await incident(connection, first, raw)
                before = await current(connection)
            with pytest.raises(RuntimeError, match="rollback"):
                async with ingest.begin() as connection:
                    await connection.execute(
                        text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                    )
                    raise RuntimeError("rollback")
            async with db.begin() as connection:
                assert await current(connection) == before
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.outbox"))
                ).scalar_one() == 0
        finally:
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())


async def watermark(connection: AsyncConnection) -> int:
    return int(
        (
            await connection.execute(text("SELECT public_api.telemetry_snapshot_watermark()"))
        ).scalar_one()
    )


def test_snapshot_excludes_pending_and_late_commits_but_keeps_newly_ineligible_members() -> None:
    async def check() -> None:
        db, api, ingest = engine(), engine("weather_api"), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                first, raw = await observation(connection, d)
                second, _ = await observation(connection, d, source=SOURCE + timedelta(minutes=1))
                conflict = await incident(connection, first, raw)
            writer = await ingest.connect()
            transaction = await writer.begin()
            try:
                pending, _ = await observation(
                    writer,
                    d,
                    source=SOURCE - timedelta(days=1),
                    received=SOURCE - timedelta(days=1),
                    identity=UUID(int=1),
                )
                async with api.begin() as reader:
                    await reader.execute(text("SET LOCAL statement_timeout='2s'"))
                    snap = await watermark(reader)  # Must not wait for writer's clock lock.
                await transaction.commit()
            finally:
                if transaction.is_active:
                    await transaction.rollback()
                await writer.close()
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
                late, _ = await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(hours=1),
                    received=SOURCE - timedelta(hours=1),
                    identity=UUID(int=2),
                )
            # Separate pooled requests, no server transaction or snapshot held across pages.
            page_sql = """SELECT id,observed_at,message_type,"values" FROM
            public_api.telemetry_publications WHERE publication_seq<=:watermark
            AND (observed_at,message_type,id)>(:after_time,:after_family,:after_id)
            ORDER BY observed_at,message_type,id LIMIT 1"""
            ids, cursor = [], (SOURCE - timedelta(days=10), "", UUID(int=0))
            points = []
            for _ in range(10):
                async with api.begin() as connection:
                    row = (
                        (
                            await connection.execute(
                                text(page_sql),
                                {
                                    "watermark": snap,
                                    "after_time": cursor[0],
                                    "after_family": cursor[1],
                                    "after_id": cursor[2],
                                },
                            )
                        )
                        .mappings()
                        .first()
                    )
                if row is None:
                    break
                ids.append(row["id"])
                points.append(dict(row))
                cursor = (row["observed_at"], row["message_type"], row["id"])
            else:
                raise AssertionError("Snapshot pagination exceeded its bounded proof-case budget")
            assert ids == [first, second]
            assert pending not in ids and late not in ids
            assert points[0]["values"]["air_temperature"] is None
            assert points[0]["values"]["relative_humidity"] == 50
            async with api.begin() as connection:
                assert await watermark(connection) > snap
        finally:
            await ingest.dispose()
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


async def waits_on_lock(admin: AsyncConnection, pid: int) -> None:
    until = time.monotonic() + 5
    while time.monotonic() < until:
        await admin.execute(text("SELECT pg_stat_clear_snapshot()"))
        row = (
            await admin.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}
            )
        ).scalar_one()
        if row == "Lock":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Concurrent writer did not reach the expected publication lock")


def test_publication_lock_precedes_receipt_locks_for_opposite_replay_batches() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        a, b = uuid4(), uuid4()
        try:
            async with ingest.connect() as first, ingest.connect() as second, db.connect() as admin:
                t1, t2 = await first.begin(), await second.begin()
                waiting = None
                try:
                    await receipt(first, identity=a)
                    pid = int((await second.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    waiting = asyncio.create_task(receipt(second, identity=b))
                    await waits_on_lock(admin, pid)
                    # If B had inserted before waiting on the clock, this creates a deadlock.
                    await asyncio.wait_for(receipt(first, identity=b), timeout=5)
                    await t1.commit()
                    await asyncio.wait_for(waiting, timeout=5)
                    await receipt(second, identity=a)
                    await t2.commit()
                finally:
                    if waiting is not None and not waiting.done():
                        waiting.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await waiting
                    if t1.is_active:
                        await t1.rollback()
                    if t2.is_active:
                        await t2.rollback()
            async with db.begin() as connection:
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.telemetry_receipt"))
                ).scalar_one() == 2
        finally:
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())


def test_qualification_locks_clock_before_canonical_row_and_retries_do_not_revise() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, raw = await observation(connection, d)
                conflict = await incident(connection, original, raw)
            async with ingest.connect() as first, ingest.connect() as second, db.connect() as admin:
                t1, t2 = await first.begin(), await second.begin()
                waiting = None
                try:
                    await observation(first, d, source=SOURCE + timedelta(minutes=1))
                    pid = int((await second.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    sql = text("SELECT weather.qualify_telemetry_conflict(:id)")
                    waiting = asyncio.create_task(second.execute(sql, {"id": conflict}))
                    await waits_on_lock(admin, pid)
                    # A qualifier that locked the canonical row first would deadlock here.
                    await asyncio.wait_for(first.execute(sql, {"id": conflict}), timeout=5)
                    before = await current(first)
                    await t1.commit()
                    await asyncio.wait_for(waiting, timeout=5)
                    await t2.commit()
                finally:
                    if waiting is not None and not waiting.done():
                        waiting.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await waiting
                    if t1.is_active:
                        await t1.rollback()
                    if t2.is_active:
                        await t2.rollback()
            async with db.begin() as connection:
                assert await current(connection) == before
        finally:
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())


def test_publication_rollback_and_narrow_roles() -> None:
    async def check() -> None:
        db, api, worker, ingest = (
            engine(),
            engine("weather_api"),
            engine("weather_worker"),
            engine("weather_ingest"),
        )
        try:
            async with db.begin() as connection:
                d = await device(connection)
            async with api.begin() as connection:
                before = await watermark(connection)
                await rejects(
                    connection, "SELECT * FROM weather.telemetry_publication_clock", {}, "42501"
                )
            with pytest.raises(RuntimeError, match="rollback"):
                async with ingest.begin() as connection:
                    await observation(connection, d)
                    raise RuntimeError("rollback")
            async with api.begin() as connection:
                assert await watermark(connection) == before
                assert not (
                    await connection.execute(
                        text("SELECT id FROM public_api.telemetry_publications")
                    )
                ).all()
            async with ingest.begin() as connection:
                await observation(connection, d)
                await rejects(
                    connection, "UPDATE weather.metric_current SET revision=9", {}, "42501"
                )
                await rejects(
                    connection,
                    "UPDATE weather.telemetry_publication_clock SET published_through=99",
                    {},
                    "42501",
                )
            async with worker.begin() as connection:
                row = (
                    await connection.execute(
                        text("""SELECT value,field_quality,quality
                    FROM public_api.metric_current WHERE metric='temperature'""")
                    )
                ).one()
                assert row[0] == 20
                assert row[1]["valid"] and row[2]["valid"]
        finally:
            for item in (ingest, worker, api, db):
                await item.dispose()

    asyncio.run(check())


def test_existing_committed_rows_backfill_idempotently_without_revising_pointers() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, _ = await observation(connection, d)
                before = await current(connection)
                seq = await watermark(connection)
                # Simulate pre-0003 committed history, then exercise the exact
                # migration backfill and its safe repeat against existing publications.
                await connection.execute(
                    text("TRUNCATE weather.metric_current,weather.telemetry_publication")
                )
                await connection.execute(
                    text("UPDATE weather.telemetry_publication_clock SET published_through=0")
                )
                backfill = next(s for s in statements() if s.startswith("DO $$ DECLARE o record"))
                await connection.execute(text(backfill))
                assert await current(connection) == before
                assert await watermark(connection) == seq
                await connection.execute(text(backfill))
                assert await current(connection) == before
                assert await watermark(connection) == seq
                assert (
                    await connection.execute(
                        text("SELECT observation_id FROM weather.telemetry_publication")
                    )
                ).scalar_one() == original
        finally:
            await db.dispose()

    asyncio.run(check())


def test_real_upgrade_from_seeded_0002_preserves_rows_and_backfills_publication() -> None:
    async def migrate(url: str, *arguments: str) -> None:
        environment = dict(os.environ, WEATHER_CI_DATABASE_URL=url)
        process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--frozen",
            "python",
            "-m",
            "alembic",
            *arguments,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise AssertionError("Disposable predecessor migration exceeded its deadline") from None
        assert process.returncode == 0, (
            "Disposable predecessor migration failed; output SHA256="
            + hashlib.sha256(output).hexdigest()
        )

    async def check() -> None:
        controller = engine()
        name = "weather_ci_previous_" + uuid4().hex
        created = False
        target: AsyncEngine | None = None
        try:
            async with controller.connect() as connection:
                await guard(connection)
                await connection.commit()
                connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                # Only this test's generated name inside the already-guarded disposable server.
                await connection.execute(text(f'CREATE DATABASE "{name}" OWNER weather_migrator'))
                created = True
            admin_url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(database=name)
            target = create_async_engine(admin_url, echo=False)
            async with target.begin() as connection:
                await connection.execute(text("CREATE EXTENSION vector"))
            url = admin_url.set(username="weather_migrator").render_as_string(hide_password=False)
            await migrate(url, "upgrade", "0002_telemetry")
            async with target.begin() as connection:
                d = await device(connection)
                original, raw = await observation(connection, d)
                assert (
                    await connection.execute(text("SELECT to_regclass('weather.metric_current')"))
                ).scalar_one() is None
            await migrate(url, "upgrade", "head")
            await migrate(url, "check")
            async with target.begin() as connection:
                row = (await current(connection))["temperature"]
                assert row["value"] == 20 and row["observation_id"] == original
                assert await watermark(connection) == 1
                assert (
                    await connection.execute(
                        text("SELECT receipt_id FROM weather.obs_st WHERE id=:id"), {"id": original}
                    )
                ).scalar_one() == raw
        finally:
            if target is not None:
                await target.dispose()
            if created:
                assert name.startswith("weather_ci_previous_") and len(name) == 52
                async with controller.connect() as connection:
                    await guard(connection)
                    await connection.commit()
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            await controller.dispose()

    asyncio.run(check())
