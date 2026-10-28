# SPDX-License-Identifier: MIT
"""Real PostgreSQL constraints, privacy grants and concurrent transaction behavior.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.db import jobs
from weather.db.days import ensure_days
from weather.db.models import metadata
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext, Registry, UnregisteredJob
from weather.scheduler.runner import _run_job, installed_registry, tick
from weather.timemodel import weather_day

pytestmark = pytest.mark.integration

ROLES = ("weather_migrator", "weather_ingest", "weather_worker", "weather_api", "weather_editor")
API_VIEWS = (
    "public_api.obs_st",
    "public_api.rapid_wind",
    "public_api.station",
    "public_api.search_documents",
    "public_api.search_vectors",
    "public_api.video_analyses",
)
ANALYSIS = ("analysis", "analysis_revision", "ai_raw_response")
SEARCH = ("embedding_profile", "search_document", "search_embedding")
TELEMETRY = (
    "station",
    "device",
    "telemetry_receipt",
    "obs_st",
    "rapid_wind",
    "evt_precip",
    "evt_strike",
    "device_status",
    "hub_status",
    "obs_air",
    "obs_sky",
)


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Integration requires fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or socket.name != "socket"
        or not socket.is_absolute()
        or not socket.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=10, max_overflow=0)


async def guard(connection: AsyncConnection) -> None:
    row = (
        await connection.execute(
            text("""
        SELECT current_database(),current_setting('server_version_num')::integer,
          current_setting('weather.integration_test',true),inet_server_addr(),
          (SELECT extversion FROM pg_extension WHERE extname='vector')
    """)
        )
    ).one()
    assert tuple(row) == ("weather_ci", 160015, "on", None, "0.8.6")


@pytest.fixture(autouse=True)
def clean_test_database() -> None:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                await guard(connection)
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await connection.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
        finally:
            await db.dispose()

    asyncio.run(clean())


async def rejects(
    connection: AsyncConnection, statement: str, params: dict[str, Any], sqlstate: str
) -> None:
    with pytest.raises(DBAPIError) as failure:
        async with connection.begin_nested():
            await connection.execute(text(statement), params)
    assert getattr(failure.value.orig, "sqlstate", None) == sqlstate


async def scalar(connection: AsyncConnection, sql: str, **params: Any) -> Any:
    return (await connection.execute(text(sql), params)).scalar_one()


async def queue(connection: AsyncConnection, kind: str, key: str) -> None:
    now = datetime.now(UTC)
    await jobs.enqueue(
        connection,
        kind=kind,
        key=key,
        payload={},
        not_before=now,
        deadline=now + timedelta(minutes=10),
    )


def test_baseline_head_roles_and_least_privilege() -> None:
    async def check() -> None:
        admin = engine()
        try:
            async with admin.begin() as connection:
                assert await scalar(
                    connection, "SELECT version_num FROM public.alembic_version"
                ) == ("0017_obs_import_rapid")
                # Slice-1 relations, the observations view, the API's views (0002), the
                # publication catalog (0003), search (0004), events (0005), the render
                # schedule (0006), stills (0009), previews (0007), cleanup (0010), keograms
                # (0008, after 0011), AI descriptions (0012), the editor (0013), posters (0014)
                # and imported history (0016, rapid wind 0017).
                relations = (
                    await connection.execute(
                        text("""
                    SELECT n.nspname||'.'||c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname IN ('weather','public_api') AND c.relkind IN ('r','v')
                """)
                    )
                ).scalars()
                assert set(relations) == {
                    *(f"weather.{name}" for name in TELEMETRY),
                    *(f"weather.{name}" for name in SEARCH),
                    "weather.weather_day",
                    "weather.job",
                    "weather.outbox",
                    "public_api.observations",
                    *API_VIEWS,
                    *(
                        f"weather.{name}"
                        for name in (
                            "media_asset",
                            "upload_intent",
                            "publication",
                            "publication_asset",
                            "video",
                            "withdrawal",
                            "product_tombstone",
                        )
                    ),
                    "public_api.assets",
                    "public_api.videos",
                    # 0005_events (#41)
                    "weather.weather_event",
                    "weather.weather_event_revision",
                    "weather.weather_event_day",
                    "public_api.event_revisions",
                    "public_api.event_revision_markers",
                    "public_api.event_days",
                    # 0006_render (#15)
                    "weather.solar_cycle",
                    "weather.segment",
                    # 0009_stills (#38)
                    "weather.hour_slot",
                    "weather.archival_still",
                    "weather.still_derivative",
                    "public_api.stills",
                    # 0007_previews (#39)
                    "weather.camera_preview",
                    "public_api.previews",
                    # 0010_cleanup (#19)
                    "weather.staging_chunk",
                    "weather.staging_deletion",
                    "weather.object_event",
                    # 0008_keograms (#37)
                    "weather.keogram",
                    "weather.keogram_bin",
                    "public_api.keograms",
                    # 0012_analysis (#23)
                    *(f"weather.{name}" for name in ANALYSIS),
                    # 0013_editorial (#44)
                    "weather.editorial_operator",
                    "weather.editorial_subject",
                    "weather.editorial_revision",
                    "weather.editorial_audit",
                    "weather.video_editorial_state",
                    "public_api.video_editorial",
                    # 0014_editorial_poster (#78)
                    "weather.editorial_poster_applied",
                    # 0016_obs_import (#167)
                    "weather.import_batch",
                    "weather.obs_import",
                    "public_api.obs_st_history",
                    "public_api.rapid_wind_history",
                    "public_api.imported_history",
                }
                options = await scalar(
                    connection,
                    "SELECT reloptions FROM pg_class WHERE oid='public_api.observations'::regclass",
                )
                assert "security_barrier=true" in options

                async def table(role: str, name: str, privilege: str) -> bool:
                    return bool(
                        await scalar(
                            connection,
                            "SELECT has_table_privilege(:role,:name,:privilege)",
                            role=role,
                            name=name,
                            privilege=privilege,
                        )
                    )

                for name in TELEMETRY:
                    relation = f"weather.{name}"
                    assert await table("weather_ingest", relation, "INSERT")
                    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                        assert not await table("weather_ingest", relation, privilege)
                    for role in ("weather_worker", "weather_api", "weather_editor"):
                        assert not await table(role, relation, "SELECT")
                # Ingest resolves station/device ids but cannot read private configuration.
                for relation in ("weather.station", "weather.device"):
                    assert not await table("weather_ingest", relation, "SELECT")
                    for column, allowed in (("alias", True), ("private_configuration", False)):
                        assert (
                            await scalar(
                                connection,
                                "SELECT has_column_privilege(:role,:name,:column,'SELECT')",
                                role="weather_ingest",
                                name=relation,
                                column=column,
                            )
                            is allowed
                        )
                for relation in ("weather.job", "weather.outbox"):
                    assert await table("weather_worker", relation, "UPDATE")
                    for role in ("weather_ingest", "weather_api"):
                        assert not await table(role, relation, "SELECT")
                    # The editor appends its outbox rows and embedding jobs, never claims them.
                    assert await table("weather_editor", relation, "INSERT")
                    for privilege in ("UPDATE", "DELETE"):
                        assert not await table("weather_editor", relation, privilege)
                assert await table("weather_worker", "weather.weather_day", "INSERT")
                assert not await table("weather_worker", "weather.weather_day", "UPDATE")
                # The renderer's sidecar weather block (#15) reads the two public telemetry
                # views through #14's aggregates as the worker; nothing else widens.
                summary_views = {"public_api.obs_st", "public_api.rapid_wind"}
                for view in ("public_api.observations", *API_VIEWS):
                    assert await table("weather_api", view, "SELECT")
                    assert not await table("weather_api", view, "INSERT")
                    assert await table("weather_worker", view, "SELECT") is (view in summary_views)
                    assert not await table("weather_worker", view, "INSERT")
                    for role in ("weather_ingest", "weather_editor"):
                        assert not await table(role, view, "SELECT")
                for relation in ("weather.solar_cycle", "weather.segment"):
                    assert await table("weather_worker", relation, "INSERT")
                    assert not await table("weather_worker", relation, "DELETE")
                    for role in ("weather_ingest", "weather_api", "weather_editor"):
                        assert not await table(role, relation, "SELECT")
                # Hourly stills (#38): the worker records selections, the API reads the view.
                for relation in (
                    "weather.hour_slot",
                    "weather.archival_still",
                    "weather.still_derivative",
                ):
                    assert await table("weather_worker", relation, "INSERT")
                    assert not await table("weather_worker", relation, "DELETE")
                    for role in ("weather_ingest", "weather_api"):
                        assert not await table(role, relation, "SELECT")
                    # Manual events (#44) ensure their calendar rows, nothing else.
                    assert await table("weather_editor", relation, "SELECT") is (
                        relation == "weather.hour_slot"
                    )
                    assert not await table("weather_editor", relation, "UPDATE")
                assert not await table("weather_worker", "weather.hour_slot", "UPDATE")
                assert await table("weather_api", "public_api.stills", "SELECT")
                assert not await table("weather_worker", "public_api.stills", "SELECT")
                # Previews (#39): the pipeline writes and expires rows; the API reads the view.
                assert await table("weather_worker", "weather.camera_preview", "DELETE")
                assert not await table("weather_worker", "weather.camera_preview", "UPDATE")
                assert await table("weather_api", "public_api.previews", "SELECT")
                for role in ("weather_ingest", "weather_api", "weather_editor"):
                    assert not await table(role, "weather.camera_preview", "SELECT")
                for role in ("weather_ingest", "weather_worker", "weather_editor"):
                    assert not await table(role, "public_api.previews", "SELECT")
                # Keogram bins are the worker's private working state; only the view is public.
                assert await table("weather_worker", "weather.keogram_bin", "DELETE")
                assert not await table("weather_worker", "weather.keogram", "DELETE")
                for relation in ("weather.keogram", "weather.keogram_bin"):
                    for role in ("weather_ingest", "weather_api", "weather_editor"):
                        assert not await table(role, relation, "SELECT")
                assert await table("weather_api", "public_api.keograms", "SELECT")
                assert not await table("weather_worker", "public_api.keograms", "SELECT")
                # AI descriptions (#23): the worker writes; only the filtered view is public, and
                # raw model replies are deletable diagnostics no other role can read.
                async def column(name: str, privilege: str) -> bool:
                    return bool(
                        await scalar(
                            connection,
                            "SELECT has_column_privilege('weather_worker',"
                            "'weather.analysis_revision',:column,:privilege)",
                            column=name,
                            privilege=privilege,
                        )
                    )

                for name in ANALYSIS:
                    relation = f"weather.{name}"
                    # Revisions: column-level INSERT that excludes author_kind (C18, #44).
                    assert await table("weather_worker", relation, "INSERT") is (
                        name != "analysis_revision"
                    )
                    assert await table("weather_worker", relation, "DELETE") is (
                        name == "ai_raw_response"
                    )
                    assert not await table("weather_worker", relation, "TRUNCATE")
                    for role in ("weather_ingest", "weather_api"):
                        assert not await table(role, relation, "SELECT")
                    # The editor reads the automated text it shows and re-projects (#44), never
                    # the raw replies, and writes none of it.
                    assert await table("weather_editor", relation, "SELECT") is (
                        name != "ai_raw_response"
                    )
                    assert not await table("weather_editor", relation, "INSERT")
                assert await column("summary", "INSERT")
                assert not await column("author_kind", "INSERT")
                # weather_editor uses the weather schema for editorial writes (#44), never the
                # public views; weather_api never touches the weather schema.
                for role, schema in (
                    ("weather_api", "weather"),
                    ("weather_editor", "public_api"),
                    ("weather_ingest", "public_api"),
                ):
                    assert not await scalar(
                        connection,
                        "SELECT has_schema_privilege(:role,:schema,'USAGE')",
                        role=role,
                        schema=schema,
                    )
                assert not await scalar(
                    connection,
                    "SELECT has_function_privilege('weather_editor',"
                    "'public_api.public_metric_values(jsonb,jsonb)','EXECUTE')",
                )
            for role in ROLES:
                login = engine(role)
                try:
                    async with login.connect() as connection:
                        assert await scalar(connection, "SELECT 1") == 1
                        assert not await scalar(
                            connection, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
                        )
                finally:
                    await login.dispose()
        finally:
            await admin.dispose()

    asyncio.run(check())


def test_weather_days_follow_the_time_model_through_dst_and_never_change() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        cases = [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25), (date(2026, 10, 23), 24)]
        try:
            async with db.begin() as connection:
                for day, hours in cases:
                    assert await ensure_days(connection, day, day) == 1
                    assert await ensure_days(connection, day, day) == 0
                    row = (
                        await connection.execute(
                            text(
                                "SELECT starts_at,ends_at FROM weather.weather_day "
                                "WHERE local_date=:day"
                            ),
                            {"day": day},
                        )
                    ).one()
                    expected = weather_day(day)
                    assert tuple(row) == (expected.start, expected.end)
                    assert row.ends_at - row.starts_at == timedelta(hours=hours)
                await rejects(
                    connection,
                    "INSERT INTO weather.weather_day VALUES('2026-10-25',"
                    "'2026-10-25T06:00Z','2026-10-25T07:00Z')",
                    {},
                    "23514",
                )
                await rejects(connection, "DELETE FROM weather.weather_day", {}, "42501")
        finally:
            await db.dispose()

    asyncio.run(check())


def test_concurrent_claims_never_share_work() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        try:
            async with db.begin() as connection:
                for number in range(8):
                    await queue(connection, "test", f"job:{number}")

            async def runner() -> jobs.Job | None:
                async with db.begin() as connection:
                    return await jobs.claim(connection, kinds={"test"})

            claimed = await asyncio.gather(*(runner() for _ in range(8)))
            assert all(job is not None for job in claimed)
            assert len({job.id for job in claimed if job}) == 8
            assert all(job.attempt == 1 for job in claimed if job)
        finally:
            await db.dispose()

    asyncio.run(check())


def test_runners_with_disjoint_kinds_and_unregistered_kinds_stay_queued() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        try:
            async with db.begin() as connection:
                for kind in ("alpha", "beta", "unregistered"):
                    await queue(connection, kind, kind)
            async with db.begin() as connection:
                first = await jobs.claim(connection, kinds={"alpha"})
                second = await jobs.claim(connection, kinds={"beta"})
                assert first is not None and first.kind == "alpha"
                assert second is not None and second.kind == "beta"
                assert await jobs.claim(connection, kinds={"alpha", "beta"}) is None
                assert await jobs.claim(connection, kinds=()) is None
                assert (
                    await scalar(
                        connection,
                        "SELECT status||':'||attempt FROM weather.job WHERE kind='unregistered'",
                    )
                    == "queued:0"
                )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_reclaim_fences_old_attempt_and_retry_keeps_deadline() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=10)
        expire_lease = (
            "UPDATE weather.job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
        )
        try:
            async with db.begin() as connection:
                job_id = await jobs.enqueue(
                    connection,
                    kind="test",
                    key="reclaim",
                    payload={},
                    not_before=now,
                    deadline=deadline,
                )
                old = await jobs.claim(connection, kinds={"test"})
            assert old is not None
            async with db.begin() as connection:
                await connection.execute(text(expire_lease), {"id": job_id})
                new = await jobs.claim(connection, kinds={"test"})
                assert new is not None and new.attempt == old.attempt + 1
                assert not await jobs.complete(connection, old)
                assert not await jobs.heartbeat(connection, old)
                assert await jobs.fail(connection, new, reason="test", retry=True, delay_seconds=0)
                assert (
                    await scalar(
                        connection, "SELECT deadline FROM weather.job WHERE id=:id", id=job_id
                    )
                    == deadline
                )
                await rejects(
                    connection,
                    "UPDATE weather.job SET deadline=deadline+interval '1 day' WHERE id=:id",
                    {"id": job_id},
                    "55000",
                )
                third = await jobs.claim(connection, kinds={"test"})
                assert third is not None and third.deadline == deadline
                await rejects(
                    connection,
                    "UPDATE weather.job SET lease_until=deadline+interval '1 second' WHERE id=:id",
                    {"id": job_id},
                    "23514",
                )

            # An expired lease at completion rolls the catalog writes back with it.
            async def expired_write(connection: AsyncConnection) -> None:
                await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 23))
                await connection.execute(text(expire_lease), {"id": job_id})

            with pytest.raises(jobs.LeaseLost):
                await JobContext(db, third).commit(expired_write)
            async with db.begin() as connection:
                assert await scalar(connection, "SELECT count(*) FROM weather.weather_day") == 0
                assert (
                    await scalar(
                        connection, "SELECT status FROM weather.job WHERE id=:id", id=job_id
                    )
                    == "running"
                )

            async def final_write(connection: AsyncConnection) -> None:
                await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 23))

            await JobContext(db, third).commit(final_write)
            async with db.begin() as connection:
                assert await scalar(connection, "SELECT count(*) FROM weather.weather_day") == 1
                assert (
                    await scalar(
                        connection, "SELECT status FROM weather.job WHERE id=:id", id=job_id
                    )
                    == "succeeded"
                )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_unknown_outbox_topic_quarantines_only_its_row() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        registry = Registry()

        async def handler(context: JobContext) -> None:
            return None

        registry.register("aggregate", handler, topics=("metric.changed",))
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=10)

        async def append(connection: AsyncConnection, topic: str, key: str) -> Any:
            return await jobs.append_outbox(
                connection, topic=topic, key=key, payload={}, not_before=now, deadline=deadline
            )

        try:
            with pytest.raises(RuntimeError, match="rollback"):
                async with db.begin() as connection:
                    await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 23))
                    await append(connection, "metric.changed", "rolled-back")
                    raise RuntimeError("rollback")
            async with db.begin() as connection:
                assert await scalar(connection, "SELECT count(*) FROM weather.outbox") == 0
                assert await scalar(connection, "SELECT count(*) FROM weather.weather_day") == 0
                # Committed first, so the unknown topic is ahead of the known one.
                unknown = await append(connection, "unknown", "unknown")
            async with db.begin() as connection:
                known = await append(connection, "metric.changed", "known")
                assert await append(connection, "metric.changed", "known") == known
                with pytest.raises(jobs.IdempotencyConflict):
                    await append(connection, "other.topic", "known")
            async with db.begin() as connection:
                # A young unknown row waits for a newer scheduler; the known row behind it routes.
                assert await dispatch(connection, registry) == 1
                assert await scalar(
                    connection, "SELECT dispatched_at IS NULL FROM weather.outbox WHERE id=:id",
                    id=unknown,
                )
                await connection.execute(
                    text("""UPDATE weather.outbox SET created_at=created_at-interval '11 minutes'
                            WHERE id=:id"""),
                    {"id": unknown},
                )
            async with db.begin() as connection:
                assert await dispatch(connection, registry) == 1
            async with db.begin() as connection:
                assert await dispatch(connection, registry) == 0
                rows = {
                    row.id: row
                    for row in (
                        await connection.execute(
                            text("""
                        SELECT o.id,o.dispatched_at IS NOT NULL AS dispatched,o.error,j.kind
                        FROM weather.outbox o LEFT JOIN weather.job j ON j.id=o.job_id
                    """)
                        )
                    ).all()
                }
                assert tuple(rows[unknown])[1:] == (True, "unregistered_topic", None)
                assert tuple(rows[known])[1:] == (True, None, "aggregate")
                assert await scalar(connection, "SELECT count(*) FROM weather.job") == 1
            with pytest.raises(UnregisteredJob):
                registry.handler("unknown")
        finally:
            await db.dispose()

    asyncio.run(check())


def test_tick_is_idempotent_fills_days_ahead_and_prunes_finished_work() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        registry = installed_registry()
        now = datetime.now(UTC)
        old = now - timedelta(days=40)
        try:
            async with db.begin() as connection:
                await tick(connection, now)
                await tick(connection, now)
                # One days job, one events job (#41) for the current slot (not yet due) and
                # one `schedule:<date>` job (#15); no segments exist yet.
                assert await scalar(connection, "SELECT count(*) FROM weather.job") == 3
                job = await jobs.claim(connection, kinds={"days"})
            assert job is not None and job.kind == "days"
            await _run_job(db, registry, job)
            async with db.begin() as connection:
                assert (
                    await scalar(connection, "SELECT status FROM weather.job WHERE kind='days'")
                    == "succeeded"
                )
                assert await scalar(connection, "SELECT count(*) FROM weather.weather_day") == 8
                finished = await scalar(
                    connection,
                    """INSERT INTO weather.job(kind,idempotency_key,status,not_before,deadline)
                       VALUES('days','days:old','succeeded',:old,:old) RETURNING id""",
                    old=old,
                )
                for key, job_id, error in (
                    ("routed", finished, None),
                    ("quarantined", None, "unregistered_topic"),
                ):
                    await connection.execute(
                        text("""
                        INSERT INTO weather.outbox(topic,idempotency_key,not_before,deadline,
                                                   dispatched_at,job_id,error)
                        VALUES('test',:key,:old,:old,:old,:job,:error)
                    """),
                        {"key": key, "old": old, "job": job_id, "error": error},
                    )
                assert await jobs.prune(connection) == 2
                assert (
                    await scalar(
                        connection, "SELECT string_agg(idempotency_key,',') FROM weather.outbox"
                    )
                    == "quarantined"
                )
                assert await scalar(connection, "SELECT count(*) FROM weather.job") == 3
                await tick(connection, now)
                assert await scalar(connection, "SELECT count(*) FROM weather.job") == 3
        finally:
            await db.dispose()

    asyncio.run(check())


def test_concurrent_ticks_enqueue_one_days_job() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        now = datetime.now(UTC)
        try:
            async with db.connect() as first, db.connect() as second:
                await first.begin()
                await second.begin()
                await tick(first, now)
                # The second tick waits on the first's uncommitted key, then becomes a no-op.
                waiting = asyncio.create_task(tick(second, now))
                await asyncio.sleep(0.5)
                assert not waiting.done()
                await first.commit()
                await asyncio.wait_for(waiting, 10)
                await second.commit()
            async with db.begin() as connection:
                assert (
                    await scalar(connection, "SELECT count(*) FROM weather.job WHERE kind='days'")
                    == 1
                )
                assert (
                    await scalar(
                        connection, "SELECT count(*) FROM weather.job WHERE kind='segment.schedule'"
                    )
                    == 1
                )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_telemetry_exact_receipts_dedupe_privacy_and_no_retention() -> None:
    async def check() -> None:
        admin = engine()
        ingest = engine("weather_ingest")
        api = engine("weather_api")
        try:
            async with ingest.begin() as connection:
                station = (
                    await connection.execute(
                        text(
                            "INSERT INTO weather.station(alias,site) "
                            "VALUES('test-station','local') RETURNING id"
                        )
                    )
                ).scalar_one()
                device = (
                    await connection.execute(
                        text("""
                    INSERT INTO weather.device(station_id,alias,private_id,kind,valid_from)
                    VALUES(:station,'test-device','private-test-id','tempest',:now) RETURNING id
                """),
                        {"station": station, "now": datetime.now(UTC)},
                    )
                ).scalar_one()
                raw = b'{"type":"obs_st","obs":[]}\x00'
                receipts = []
                for _ in range(2):
                    receipts.append(
                        (
                            await connection.execute(
                                text("""
                        INSERT INTO weather.telemetry_receipt(received_at,raw_bytes,sha256,
                          parse_state,parser_revision,receiver_incarnation)
                        VALUES(:now,:raw,:sha,'quarantined','test','test') RETURNING id
                    """),
                                {
                                    "now": datetime.now(UTC),
                                    "raw": raw,
                                    "sha": hashlib.sha256(raw).hexdigest(),
                                },
                            )
                        ).scalar_one()
                    )
                observed = datetime.now(UTC)
                observation_ids = []
                for receipt in receipts:
                    observation_ids.append(
                        (
                            await connection.execute(
                                text("""
                        INSERT INTO weather.obs_st(device_id,receipt_id,source_ts,row_idx,
                                                   "values",quality)
                        VALUES(:device,:receipt,:ts,0,CAST(:values AS jsonb),
                               CAST(:quality AS jsonb))
                        ON CONFLICT(device_id,message_type,source_ts,row_idx) DO NOTHING
                        RETURNING id
                    """),
                                {
                                    "device": device,
                                    "receipt": receipt,
                                    "ts": observed,
                                    "values": json.dumps(
                                        {
                                            "air_temperature": 0,
                                            "private_id": "private-test-id",
                                        }
                                    ),
                                    "quality": json.dumps(
                                        {
                                            "valid": True,
                                            "reason": "valid",
                                            "invalid": ["private_id"],
                                            "serial": "private-test-id",
                                        }
                                    ),
                                },
                            )
                        ).scalar_one_or_none()
                    )
                assert observation_ids[0] is not None and observation_ids[1] is None
                assert (
                    await connection.execute(text("SELECT receipt_id FROM weather.obs_st"))
                ).scalar_one() == receipts[0]
                assert (
                    await connection.execute(
                        text("SELECT raw_bytes FROM weather.telemetry_receipt WHERE id=:id"),
                        {"id": receipts[0]},
                    )
                ).scalar_one() == raw
                await connection.execute(
                    text("""
                    INSERT INTO weather.hub_status(device_id,receipt_id,source_ts,row_idx,"values")
                    VALUES(:device,:receipt,:ts,0,'{"private_id":"private-test-id"}')
                """),
                    {"device": device, "receipt": receipts[0], "ts": observed},
                )
                for table in ("telemetry_receipt", "obs_st", "hub_status"):
                    await rejects(connection, "DELETE FROM weather." + table, {}, "42501")
            async with api.begin() as connection:
                rows = (
                    (await connection.execute(text("SELECT * FROM public_api.observations")))
                    .mappings()
                    .all()
                )
                assert len(rows) == 1
                assert rows[0]["values"] == {"air_temperature": 0}
                assert rows[0]["quality"] == {
                    "valid": True,
                    "reason": "valid",
                    "invalid": [],
                }
                assert "private-test-id" not in json.dumps([dict(r) for r in rows], default=str)
                assert rows[0]["station_alias"] == "test-station"
                assert rows[0]["device_alias"] == "test-device"
            async with admin.begin() as connection:
                assert (
                    await connection.execute(
                        text("""
                    SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='weather' AND c.relname IN
                    ('telemetry_receipt','obs_st','rapid_wind','evt_precip','evt_strike',
                     'device_status','hub_status','obs_air','obs_sky') AND NOT t.tgisinternal
                """)
                    )
                ).scalar_one() == 0
                assert (
                    await connection.execute(
                        text("""
                    SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_am a ON a.oid=c.relam
                    WHERE n.nspname='weather' AND a.amname='brin'
                """)
                    )
                ).scalar_one() == 6
                # Current conditions read the newest obs_st/rapid_wind row by an ordered btree.
                for family in ("obs_st", "rapid_wind"):
                    assert (
                        (
                            await connection.execute(
                                text(
                                    "SELECT indexdef FROM pg_indexes WHERE schemaname='weather' "
                                    "AND indexname=:name"
                                ),
                                {"name": f"ix_{family}_source_ts"},
                            )
                        )
                        .scalar_one()
                        .endswith("USING btree (source_ts)")
                    )
                # Receipts use a (received_at, id) btree so reproject keyset pages stay bounded.
                assert (
                    (
                        await connection.execute(
                            text("""
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname='weather' AND indexname='ix_telemetry_receipt_received_id'
                """)
                        )
                    )
                    .scalar_one()
                    .endswith("USING btree (received_at, id)")
                )
        finally:
            await api.dispose()
            await ingest.dispose()
            await admin.dispose()

    asyncio.run(check())
