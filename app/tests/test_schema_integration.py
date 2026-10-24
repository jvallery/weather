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
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, make_url, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.db import jobs
from weather.db.days import ensure_days
from weather.db.models import metadata
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext, Registry, UnregisteredJob
from weather.scheduler.runner import _run_job

pytestmark = pytest.mark.integration


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
                tables = ",".join(
                    "weather." + t.name
                    for t in metadata.sorted_tables
                    if t.name != "camera"
                )
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


async def asset(
    connection: AsyncConnection,
    kind: str,
    *,
    embargo: bool = False,
    published: bool = True,
) -> UUID:
    digest = uuid4().hex * 2
    return (
        await connection.execute(
            text("""
        INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,object_key,
                                        bytes,content_type,public_not_before,published_at)
        VALUES(:kind,:bucket,:visibility,:digest,'jpg',:key,123,'image/jpeg',:eligible,:published)
        RETURNING id
    """),
            {
                "kind": kind,
                "bucket": "weather-originals"
                if kind == "original"
                else "weather-media",
                "visibility": "private" if kind == "original" else "public",
                "digest": digest,
                "key": f"{kind}/{digest}.jpg",
                "eligible": datetime.now(UTC)
                + (timedelta(hours=1) if embargo else -timedelta(hours=1)),
                "published": datetime.now(UTC)
                if published and kind != "original"
                else None,
            },
        )
    ).scalar_one()


async def product_graph(connection: AsyncConnection) -> dict[str, UUID]:
    await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 24))
    slots = (
        (
            await connection.execute(
                text("""
        SELECT d.id AS day_id,h.id AS slot_id,d.local_date FROM weather.weather_day d
        JOIN weather.archive_hour_slot h ON h.weather_day_id=d.id
        WHERE h.local_label='00:00' ORDER BY d.local_date
    """)
            )
        )
        .mappings()
        .all()
    )
    cycle = (
        await connection.execute(
            text("""
        INSERT INTO weather.solar_cycle(site,cycle_date,dawn,dusk,next_dawn,revision,
                                        daytime_frames,overnight_frames)
        VALUES('local','2026-10-23','2026-10-23T06:00Z','2026-10-23T18:00Z',
               '2026-10-24T06:00Z','synthetic-test',3600,3600) RETURNING id
    """)
        )
    ).scalar_one()
    segment = (
        await connection.execute(
            text("""
        INSERT INTO weather.segment(camera_id,solar_cycle_id,kind,starts_at,ends_at,frames)
        VALUES('frontyard_1',:cycle,'daytime','2026-10-23T06:00Z','2026-10-23T18:00Z',3600)
        RETURNING id
    """),
            {"cycle": cycle},
        )
    ).scalar_one()
    video = (
        await connection.execute(
            text("""
        INSERT INTO weather.video(segment_id,revision,state) VALUES(:segment,1,'processing')
        RETURNING id
    """),
            {"segment": segment},
        )
    ).scalar_one()
    chunk = (
        await connection.execute(
            text("""
        INSERT INTO weather.capture_chunk(camera_id,utc_hour,pod_uid,incarnation,sequence,
                                           sha256,path,bytes)
        VALUES('frontyard_1','2026-10-23T06:00Z','test-pod','test-incarnation',1,:sha,
               'sealed/frontyard_1/test.tar',512) RETURNING id
    """),
            {"sha": "a" * 64},
        )
    ).scalar_one()
    capture = (
        await connection.execute(
            text("""
        INSERT INTO weather.capture_slot(camera_id,chunk_id,slot_ts,source_ts,capture_upper,
                                         uncertainty_seconds,sha256,width,height,selection_revision)
        VALUES('frontyard_1',:chunk,'2026-10-23T06:00Z','2026-10-23T06:00Z',
               '2026-10-23T06:00:01Z',1,:sha,100,100,'test') RETURNING id
    """),
            {"chunk": chunk, "sha": "a" * 64},
        )
    ).scalar_one()
    return {
        "day": slots[0]["day_id"],
        "slot": slots[0]["slot_id"],
        "other_day": slots[1]["day_id"],
        "other_slot": slots[1]["slot_id"],
        "segment": segment,
        "video": video,
        "capture": capture,
        "original": await asset(connection, "original"),
    }


STILL_INSERT = """
    INSERT INTO weather.archival_still(camera_id,weather_day_id,hour_slot_id,capture_slot_id,
      source_segment_id,source_video_id,original_asset_id,revision,source_ts,selection_policy)
    VALUES('frontyard_1',:day,:slot,:capture,:segment,:video,:original,:revision,
           '2026-10-23T06:00Z','test') RETURNING id
"""


def test_day_helper_dst_and_idempotence() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        try:
            async with db.begin() as connection:
                for day, expected in [
                    (date(2026, 3, 8), 23),
                    (date(2026, 11, 1), 25),
                    (date(2026, 10, 23), 24),
                ]:
                    await ensure_days(connection, day, day)
                    first = (
                        (
                            await connection.execute(
                                text("""
                        SELECT h.id,h.starts_at,h.ends_at,h.local_label,h.fold,h.utc_offset_minutes
                        FROM weather.archive_hour_slot h JOIN weather.weather_day d
                        ON d.id=h.weather_day_id WHERE d.local_date=:day ORDER BY h.starts_at
                    """),
                                {"day": day},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    assert len(first) == expected
                    assert all(
                        r["ends_at"] - r["starts_at"] == timedelta(hours=1)
                        for r in first
                    )
                    await ensure_days(connection, day, day)
                    ids = (
                        (
                            await connection.execute(
                                text("""
                        SELECT h.id FROM weather.archive_hour_slot h JOIN weather.weather_day d
                        ON d.id=h.weather_day_id WHERE d.local_date=:day ORDER BY h.starts_at
                    """),
                                {"day": day},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert ids == [r["id"] for r in first]
                    if expected == 25:
                        repeated = [r for r in first if r["local_label"] == "01:00"]
                        assert [r["fold"] for r in repeated] == [0, 1]
                        assert [r["utc_offset_minutes"] for r in repeated] == [
                            -360,
                            -420,
                        ]
                        assert repeated[0]["id"] != repeated[1]["id"]
        finally:
            await db.dispose()

    asyncio.run(check())


def test_sql_hours_match_the_pinned_python_time_model() -> None:
    from weather.timemodel import calendar, site

    async def check() -> None:
        db = engine("weather_worker")
        try:
            async with db.begin() as connection:
                for day in (date(2026, 3, 8), date(2026, 11, 1), date(2026, 10, 23)):
                    await ensure_days(connection, day, day)
                    rows = (
                        await connection.execute(
                            text("""
                        SELECT h.starts_at,h.ends_at,h.local_label,h.utc_offset_minutes,h.fold
                        FROM weather.archive_hour_slot h JOIN weather.weather_day d
                        ON d.id=h.weather_day_id WHERE d.local_date=:day ORDER BY h.starts_at
                    """),
                            {"day": day},
                        )
                    ).all()
                    expected = calendar.hour_slots(day)
                    assert len(rows) == len(expected)
                    for row, slot in zip(rows, expected, strict=True):
                        local = slot.start.astimezone(site.site_timezone())
                        assert tuple(row) == (
                            slot.start,
                            slot.end,
                            local.strftime("%H:%M"),
                            int(local.utcoffset().total_seconds() / 60),
                            slot.fold,
                        )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_still_relationships_active_uniqueness_and_retention() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                graph = await product_graph(connection)
                await rejects(
                    connection,
                    STILL_INSERT,
                    {**graph, "day": graph["other_day"], "revision": 1},
                    "23503",
                )
                first = (
                    await connection.execute(
                        text(STILL_INSERT), {**graph, "revision": 1}
                    )
                ).scalar_one()
                retry = STILL_INSERT.replace(
                    "RETURNING id", "ON CONFLICT DO NOTHING RETURNING id"
                )
                assert (
                    await connection.execute(text(retry), {**graph, "revision": 1})
                ).scalar_one_or_none() is None
                assert (
                    await connection.execute(
                        text(
                            "SELECT id FROM weather.archival_still "
                            "WHERE camera_id='frontyard_1' AND hour_slot_id=:slot "
                            "AND state='active'"
                        ),
                        {"slot": graph["slot"]},
                    )
                ).scalar_one() == first
                await rejects(
                    connection, STILL_INSERT, {**graph, "revision": 2}, "23505"
                )
                await rejects(
                    connection,
                    "DELETE FROM weather.video WHERE id=:id",
                    {"id": graph["video"]},
                    "23503",
                )
                await rejects(
                    connection,
                    "DELETE FROM weather.segment WHERE id=:id",
                    {"id": graph["segment"]},
                    "23503",
                )
                await connection.execute(
                    text("UPDATE weather.video SET state='expired' WHERE id=:id"),
                    {"id": graph["video"]},
                )
                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM weather.archival_still WHERE id=:id"
                        ),
                        {"id": first},
                    )
                ).scalar_one() == 1
                await connection.execute(
                    text(
                        "UPDATE weather.archival_still SET state='retired' WHERE id=:id"
                    ),
                    {"id": first},
                )
                await connection.execute(text(STILL_INSERT), {**graph, "revision": 2})
                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM weather.archival_still "
                            "WHERE state='active'"
                        )
                    )
                ).scalar_one() == 1
        finally:
            await db.dispose()

    asyncio.run(check())


def test_asset_class_and_request_time_public_views() -> None:
    async def check() -> None:
        db = engine()
        api = engine("weather_api")
        try:
            async with db.begin() as connection:
                original = await asset(connection, "original")
                public = await asset(connection, "still")
                await asset(connection, "still", embargo=True)
                await asset(connection, "still", published=False)
                withdrawn = await asset(connection, "poster")
                await connection.execute(
                    text("""
                    UPDATE weather.media_asset SET withdrawn=true,withdrawn_at=clock_timestamp(),
                    withdraw_reason='test' WHERE id=:id
                """),
                    {"id": withdrawn},
                )
                await rejects(
                    connection,
                    "UPDATE weather.media_asset SET visibility='public' WHERE id=:id",
                    {"id": original},
                    "23514",
                )
                await rejects(
                    connection,
                    "UPDATE weather.media_asset SET bucket='weather-media' "
                    "WHERE id=:id",
                    {"id": original},
                    "23514",
                )
            async with api.begin() as connection:
                ids = (
                    (await connection.execute(text("SELECT id FROM public_api.assets")))
                    .scalars()
                    .all()
                )
                assert ids == [public]
                for table in (
                    "station",
                    "device",
                    "telemetry_receipt",
                    "media_asset",
                    "obs_st",
                ):
                    await rejects(
                        connection, "SELECT * FROM weather." + table, {}, "42501"
                    )
                await rejects(
                    connection, "DELETE FROM weather.telemetry_receipt", {}, "42501"
                )
                await rejects(
                    connection,
                    "SELECT public_api.append_editorial_revision"
                    "('test',0,'{}','not-allowed','test')",
                    {},
                    "42501",
                )
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_public_view_barriers_prevent_predicate_function_leaks() -> None:
    async def check() -> None:
        db = engine()
        api = engine("weather_api")
        try:
            async with db.begin() as connection:
                await asset(connection, "original")
                flags = (
                    (
                        await connection.execute(
                            text("""
                    SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public_api' AND c.relkind='v'
                """)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert flags and all(
                    "security_barrier=true" in options for options in flags
                )
            async with api.begin() as connection:
                await connection.execute(
                    text("CREATE TEMP TABLE view_probe(x integer)")
                )
                await connection.execute(
                    text("""
                    CREATE FUNCTION pg_temp.reject_if_called(text) RETURNS boolean
                    LANGUAGE plpgsql IMMUTABLE COST 0.000001 AS $$
                    BEGIN RAISE EXCEPTION 'view filter leak'; END $$
                """)
                )
                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM public_api.assets "
                            "WHERE pg_temp.reject_if_called(sha256)"
                        )
                    )
                ).scalar_one() == 0
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_editorial_append_only_and_concurrent_cas() -> None:
    async def check() -> None:
        db = engine()
        api = engine("weather_api")
        try:
            async with db.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO weather.operator_allowlist VALUES('test-operator')"
                    )
                )

            async def writer() -> bool:
                try:
                    async with api.begin() as connection:
                        await connection.execute(
                            text(
                                "SELECT public_api.append_editorial_revision"
                                "('video:test',0,'{}','test-operator','test')"
                            )
                        )
                    return True
                except DBAPIError as error:
                    assert getattr(error.orig, "sqlstate", None) == "40001"
                    return False

            assert sorted(await asyncio.gather(writer(), writer())) == [False, True]
            async with db.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.editorial_revision")
                    )
                ).scalar_one() == 1
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.outbox")
                    )
                ).scalar_one() == 1
                await rejects(
                    connection,
                    "UPDATE weather.editorial_revision SET reason='changed'",
                    {},
                    "55000",
                )
                await rejects(
                    connection, "DELETE FROM weather.editorial_revision", {}, "55000"
                )
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_withdrawal_cascades_through_public_views_without_deleting_originals() -> None:
    async def check() -> None:
        db = engine()
        api = engine("weather_api")
        try:
            async with db.begin() as connection:
                graph = await product_graph(connection)
                still = (
                    await connection.execute(
                        text(STILL_INSERT), {**graph, "revision": 1}
                    )
                ).scalar_one()
                assets = {
                    role: await asset(
                        connection, "still" if role == "derivative" else role
                    )
                    for role in (
                        "native",
                        "web",
                        "mobile",
                        "poster",
                        "sidecar",
                        "derivative",
                        "keogram",
                    )
                }
                await connection.execute(
                    text("""
                    UPDATE weather.video SET state='published',native_asset_id=:native,
                      web_asset_id=:web,mobile_asset_id=:mobile,poster_asset_id=:poster,
                      sidecar_asset_id=:sidecar WHERE id=:video
                """),
                    {**assets, "video": graph["video"]},
                )
                await connection.execute(
                    text("UPDATE weather.segment SET state='published' WHERE id=:id"),
                    {"id": graph["segment"]},
                )
                await connection.execute(
                    text("""
                    INSERT INTO weather.publication(video_id,revision,generation,public_not_before)
                    VALUES(:video,1,1,clock_timestamp()-interval '1 hour')
                """),
                    {"video": graph["video"]},
                )
                await connection.execute(
                    text("""
                    INSERT INTO weather.still_derivative(archival_still_id,asset_id,asset_class,
                      recipe_revision,state,committed_at)
                    VALUES(:still,:asset,'still','test','published',clock_timestamp())
                """),
                    {"still": still, "asset": assets["derivative"]},
                )
                await connection.execute(
                    text("""
                    INSERT INTO weather.keogram(segment_id,asset_id,revision,lineage_sha256,state)
                    VALUES(:segment,:asset,1,:hash,'published')
                """),
                    {
                        "segment": graph["segment"],
                        "asset": assets["keogram"],
                        "hash": "a" * 64,
                    },
                )
                await rejects(
                    connection,
                    """
                    INSERT INTO weather.still_derivative(archival_still_id,asset_id,asset_class,
                      recipe_revision,state) VALUES(:still,:asset,'original','test','published')
                """,
                    {"still": still, "asset": graph["original"]},
                    "23514",
                )
            async with api.begin() as connection:
                for view in ("videos", "stills", "keograms"):
                    assert (
                        await connection.execute(
                            text("SELECT count(*) FROM public_api." + view)
                        )
                    ).scalar_one() == 1
            async with db.begin() as connection:
                for role in ("native", "derivative", "keogram"):
                    await connection.execute(
                        text("""
                        UPDATE weather.media_asset SET withdrawn=true,
                          withdrawn_at=clock_timestamp(),
                          withdraw_reason='test' WHERE id=:id
                    """),
                        {"id": assets[role]},
                    )
            async with api.begin() as connection:
                for view in ("videos", "stills", "keograms"):
                    assert (
                        await connection.execute(
                            text("SELECT count(*) FROM public_api." + view)
                        )
                    ).scalar_one() == 0
            async with db.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.archival_still")
                    )
                ).scalar_one() == 1
                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM weather.media_asset "
                            "WHERE class='original'"
                        )
                    )
                ).scalar_one() == 1
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_preview_embargo_trust_and_expiry_are_enforced() -> None:
    async def check() -> None:
        db = engine()
        api = engine("weather_api")
        now = datetime.now(UTC)
        try:
            async with db.begin() as connection:
                values = {
                    "lower": now - timedelta(seconds=1001),
                    "upper": now - timedelta(seconds=1000),
                    "eligible": now - timedelta(seconds=370),
                    "expires": now + timedelta(minutes=10),
                    "first": now - timedelta(seconds=300),
                    "sha": "a" * 64,
                }
                statement = """
                    INSERT INTO weather.camera_preview(camera_id,generation,bytes,width,height,
                      captured_lower,captured_upper,path,sha256,public_not_before,expires_at,
                      first_published_at,timing_trusted)
                    VALUES('frontyard_1',CAST(:generation AS bigint),100,100,100,:lower,:upper,
                           'previews/frontyard_1/'||
                           CAST(CAST(:generation AS bigint) AS text)||'.jpg',
                           :sha,:eligible,:expires,:first,:trusted)
                    RETURNING id
                """
                await rejects(
                    connection,
                    statement,
                    {
                        **values,
                        "eligible": now - timedelta(seconds=401),
                        "generation": 1,
                        "trusted": True,
                    },
                    "23514",
                )
                await rejects(
                    connection,
                    statement,
                    {
                        **values,
                        "first": now - timedelta(seconds=500),
                        "generation": 1,
                        "trusted": True,
                    },
                    "23514",
                )
                good = (
                    await connection.execute(
                        text(statement), {**values, "generation": 1, "trusted": True}
                    )
                ).scalar_one()
                await connection.execute(
                    text(statement), {**values, "generation": 2, "trusted": False}
                )
                await rejects(
                    connection,
                    "UPDATE weather.camera_preview SET "
                    "path='previews/frontyard_1/../escape.jpg' WHERE id=:id",
                    {"id": good},
                    "23514",
                )
                await connection.execute(
                    text(statement),
                    {
                        **values,
                        "generation": 3,
                        "expires": now - timedelta(seconds=1),
                        "trusted": True,
                    },
                )
                await connection.execute(
                    text(statement),
                    {
                        **values,
                        "generation": 4,
                        "first": now + timedelta(minutes=1),
                        "trusted": True,
                    },
                )
            async with api.begin() as connection:
                ids = (
                    (
                        await connection.execute(
                            text("SELECT id FROM public_api.previews")
                        )
                    )
                    .scalars()
                    .all()
                )
                assert ids == [good]
                columns = (
                    await connection.execute(text("SELECT * FROM public_api.previews"))
                ).keys()
                assert "path" not in columns and "policy" not in columns
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_vector_dimension_and_sqlalchemy_roundtrip() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                graph = await product_graph(connection)
                await connection.execute(
                    text("""
                    INSERT INTO weather.embedding_profile(id,model,active)
                    VALUES('test','test-embedding',true)
                """)
                )
                await rejects(
                    connection,
                    "INSERT INTO weather.embedding_profile(id,model,active) "
                    "VALUES('conflict','test',true)",
                    {},
                    "23505",
                )
                document = metadata.tables["weather.search_document"]
                await connection.execute(
                    insert(document).values(
                        video_id=graph["video"],
                        profile_id="test",
                        revision=1,
                        text="synthetic test",
                        embedding=[1.0] + [0.0] * 2559,
                    )
                )
                vector = (
                    await connection.execute(select(document.c.embedding))
                ).scalar_one()
                assert len(vector) == 2560
                assert vector[0] == 1.0 and all(v == 0 for v in vector[1:])
                await rejects(
                    connection,
                    """
                    INSERT INTO weather.search_document(video_id,profile_id,revision,text,embedding)
                    VALUES(:video,'test',2,'test','[1,0]'::vector)
                """,
                    {"video": graph["video"]},
                    "22000",
                )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_concurrent_claims_never_share_work() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        now = datetime.now(UTC)
        try:
            async with db.begin() as connection:
                for number in range(8):
                    await jobs.enqueue(
                        connection,
                        kind="test",
                        key=f"job:{number}",
                        payload={},
                        not_before=now,
                        deadline=now + timedelta(minutes=10),
                    )

            async def runner(owner: str) -> jobs.Job | None:
                async with db.begin() as connection:
                    return await jobs.claim(connection, owner=owner)

            claimed = await asyncio.gather(*(runner(f"runner:{n}") for n in range(8)))
            assert all(job is not None for job in claimed)
            assert len({job.id for job in claimed if job}) == 8
            assert all(
                job.attempt == 1 and job.fencing_token == 1 for job in claimed if job
            )
        finally:
            await db.dispose()

    asyncio.run(check())


def test_reclaim_fences_old_owner_and_retry_keeps_deadline() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=10)
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
                old = await jobs.claim(connection, owner="old")
            assert old is not None
            async with db.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE weather.job SET lease_until=clock_timestamp()"
                        "-interval '1 second' WHERE id=:id"
                    ),
                    {"id": job_id},
                )
                new = await jobs.claim(connection, owner="new")
                assert new is not None and new.fencing_token == old.fencing_token + 1
                assert not await jobs.complete(connection, old)
                assert not await jobs.heartbeat(connection, old)
                assert await jobs.fail(
                    connection, new, reason="test", retry=True, delay_seconds=0
                )
                actual = (
                    await connection.execute(
                        text("SELECT deadline FROM weather.job WHERE id=:id"),
                        {"id": job_id},
                    )
                ).scalar_one()
                assert actual == deadline
                await rejects(
                    connection,
                    "UPDATE weather.job SET deadline=deadline+interval '1 day' "
                    "WHERE id=:id",
                    {"id": job_id},
                    "55000",
                )
                third = await jobs.claim(connection, owner="third")
                assert third is not None
                assert third.deadline == deadline
            assert third is not None

            # Failure after catalog writes rolls the writes and job completion back together.
            async def expired_write(connection: AsyncConnection) -> None:
                await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 23))
                await connection.execute(
                    text(
                        "UPDATE weather.job SET lease_until=clock_timestamp()"
                        "-interval '1 second' WHERE id=:id"
                    ),
                    {"id": job_id},
                )

            with pytest.raises(jobs.LeaseLost):
                await JobContext(db, third).commit(expired_write)
            async with db.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.weather_day")
                    )
                ).scalar_one() == 0
                assert (
                    await connection.execute(
                        text("SELECT status FROM weather.job WHERE id=:id"),
                        {"id": job_id},
                    )
                ).scalar_one() == "running"

            async def final_write(connection: AsyncConnection) -> None:
                await ensure_days(connection, date(2026, 10, 23), date(2026, 10, 23))

            await JobContext(db, third).commit(final_write)
            async with db.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.weather_day")
                    )
                ).scalar_one() == 1
                assert (
                    await connection.execute(
                        text("SELECT status FROM weather.job WHERE id=:id"),
                        {"id": job_id},
                    )
                ).scalar_one() == "succeeded"
        finally:
            await db.dispose()

    asyncio.run(check())


def test_outbox_rollback_idempotence_dispatch_and_unknown_types() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        registry = Registry()

        async def handler(context: JobContext) -> None:
            return None

        registry.register("aggregate", handler, topics=("metric.changed",))
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=10)
        try:
            with pytest.raises(RuntimeError, match="rollback"):
                async with db.begin() as connection:
                    await ensure_days(
                        connection, date(2026, 10, 23), date(2026, 10, 23)
                    )
                    await jobs.append_outbox(
                        connection,
                        topic="metric.changed",
                        key="rolled-back",
                        payload={},
                        not_before=now,
                        deadline=deadline,
                    )
                    raise RuntimeError("rollback")
            async with db.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.outbox")
                    )
                ).scalar_one() == 0
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.weather_day")
                    )
                ).scalar_one() == 0
                first = await jobs.append_outbox(
                    connection,
                    topic="metric.changed",
                    key="one",
                    payload={},
                    not_before=now,
                    deadline=deadline,
                )
                assert (
                    await jobs.append_outbox(
                        connection,
                        topic="metric.changed",
                        key="one",
                        payload={},
                        not_before=now,
                        deadline=deadline,
                    )
                    == first
                )
                assert await dispatch(connection, registry) == 1
                assert await dispatch(connection, registry) == 0
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.job"))
                ).scalar_one() == 1
                await jobs.append_outbox(
                    connection,
                    topic="unknown",
                    key="unknown",
                    payload={},
                    not_before=now,
                    deadline=deadline,
                )
            async with db.begin() as connection:
                with pytest.raises(UnregisteredJob):
                    await dispatch(connection, registry)
            with pytest.raises(UnregisteredJob):
                registry.handler("unknown")
        finally:
            await db.dispose()

    asyncio.run(check())


def test_unregistered_claimed_job_is_failed_loudly() -> None:
    async def check() -> None:
        db = engine("weather_worker")
        now = datetime.now(UTC)
        try:
            async with db.begin() as connection:
                await jobs.enqueue(
                    connection,
                    kind="unregistered",
                    key="unregistered",
                    payload={},
                    not_before=now,
                    deadline=now + timedelta(minutes=10),
                )
                job = await jobs.claim(connection, owner="test")
            assert job is not None
            await _run_job(db, Registry(), job)
            async with db.begin() as connection:
                row = (
                    await connection.execute(
                        text("SELECT status,last_error FROM weather.job WHERE id=:id"),
                        {"id": job.id},
                    )
                ).one()
                assert tuple(row) == ("failed", "unregistered")
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
                        VALUES(:now,:raw,:sha,'malformed','test','test') RETURNING id
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
                                            "serial": "private-test-id",
                                        }
                                    ),
                                },
                            )
                        ).scalar_one_or_none()
                    )
                assert observation_ids[0] is not None and observation_ids[1] is None
                assert (
                    await connection.execute(
                        text("SELECT receipt_id FROM weather.obs_st")
                    )
                ).scalar_one() == receipts[0]
                assert (
                    await connection.execute(
                        text(
                            "SELECT raw_bytes FROM weather.telemetry_receipt "
                            "WHERE id=:id"
                        ),
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
                    await rejects(
                        connection, "DELETE FROM weather." + table, {}, "42501"
                    )
            async with api.begin() as connection:
                rows = (
                    (
                        await connection.execute(
                            text("SELECT * FROM public_api.observations")
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(rows) == 1
                assert rows[0]["values"] == {"air_temperature": 0}
                assert rows[0]["quality"] == {"valid": True, "reason": "valid"}
                assert "private-test-id" not in json.dumps(
                    [dict(r) for r in rows], default=str
                )
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
                ).scalar_one() == 9
        finally:
            await api.dispose()
            await ingest.dispose()
            await admin.dispose()

    asyncio.run(check())
