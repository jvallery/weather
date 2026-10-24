# SPDX-License-Identifier: MIT
"""UNRUN SQL-reader proofs; only the guarded fleet PostgreSQL socket fixture."""

import asyncio
import json
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from test_current_publication_integration import (
    SOURCE,
    device,
    engine,
    incident,
    observation,
)
from test_current_publication_integration import disposable_database as disposable_database
from weather.api.cursors import Snapshot
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import Interval, ObservationQuery
from weather.db.public_connection import PublicDatabaseBudget
from weather.db.public_evidence import read_evidence_window
from weather.db.public_mapping import FreshnessPolicy
from weather.db.public_source import PublicReadUnavailable, PublicSqlSource

pytestmark = pytest.mark.integration


async def api_engine(budget: PublicDatabaseBudget) -> AsyncEngine:
    # The shared helper validates in-cluster flag, socket path and database name
    # before constructing even a lazy engine. No production URL override exists.
    template = engine("weather_api")
    url = template.url
    await template.dispose()
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=budget.pool_size,
        max_overflow=0,
        pool_timeout=budget.pool_timeout_seconds,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )


async def station(connection: AsyncConnection) -> tuple[UUID, str]:
    d = await device(connection)
    alias = (
        await connection.execute(
            text("""SELECT s.alias FROM weather.station s
        JOIN weather.device d ON d.station_id=s.id WHERE d.id=:id"""),
            {"id": d},
        )
    ).scalar_one()
    return d, str(alias)


def source(
    api: AsyncEngine, alias: str, budget: PublicDatabaseBudget, *, seconds: int = 120
) -> Any:
    return PublicSqlSource(
        api,
        station_alias=alias,
        maximum_raw_seconds=30 * 86400,
        policies={"temperature": FreshnessPolicy(60, 90), "humidity": FreshnessPolicy(60, 90)},
        budget=budget,
        clock=lambda: SOURCE + timedelta(seconds=seconds),
    )


def test_api_role_reader_preserves_zero_integer_lightning_and_explicit_obs_st_age() -> None:
    async def check() -> None:
        db = engine()
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                await observation(
                    connection,
                    d,
                    values={
                        "air_temperature": 0,
                        "relative_humidity": 50,
                        "lightning_strike_count": 9007199254740993,
                    },
                )
            reader = source(api, alias, budget, seconds=10)
            reading = await reader.current()
            metrics = {m.metric: m for m in reading.current.metrics}
            assert metrics["temperature"].value == 0
            assert type(metrics["lightning_count"].value) is int
            assert metrics["lightning_count"].value == 9007199254740993
            assert reading.newest_valid_obs_st_at == SOURCE
            page = await reader.observations(
                ObservationQuery(
                    start=SOURCE - timedelta(seconds=1),
                    end=SOURCE + timedelta(seconds=1),
                    metrics=["lightning_count"],
                    limit=10,
                ),
                None,
            )
            assert type(page.data.points[0].value) is int
            assert "fixture-private" not in page.data.model_dump_json()
            async with db.begin() as connection:
                await observation(
                    connection,
                    d,
                    family="rapid_wind",
                    source=SOURCE + timedelta(seconds=5),
                    values={"wind_speed": 3},
                )
                await observation(
                    connection,
                    d,
                    family="obs_air",
                    source=SOURCE + timedelta(seconds=6),
                    values={"air_temperature": 2},
                )
            assert (await reader.current()).newest_valid_obs_st_at == SOURCE
            async with db.begin() as connection:
                await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(minutes=1),
                    values={"air_temperature": None, "relative_humidity": 55},
                )
            reader = source(api, alias, budget, seconds=70)
            updated = {m.metric: m for m in (await reader.current()).current.metrics}
            assert updated["temperature"].value is None
            assert updated["humidity"].value == 55
            assert updated["humidity"].observed_at == SOURCE + timedelta(minutes=1)
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_real_flattened_export_excludes_late_rows_keeps_disputed_member_and_empty_watermark() -> (
    None
):
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                first, raw = await observation(connection, d)
                conflict = await incident(connection, first, raw)
            reader = source(api, alias, budget)
            query = ObservationQuery(
                start=SOURCE - timedelta(days=29),
                end=SOURCE + timedelta(days=1),
                metrics=["temperature", "humidity"],
                limit=1,
            )
            page1 = await reader.observations(query, None)
            assert page1.data.points[0].metric == "humidity" and page1.next_position is not None
            async with ingest.begin() as connection:
                await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(days=1),
                    received=SOURCE - timedelta(days=1),
                    identity=UUID(int=1),
                )
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            page2 = await reader.observations(
                query,
                Snapshot(
                    watermark=page1.watermark,
                    position=page1.next_position,
                ),
            )
            assert page2.watermark == page1.watermark
            assert page2.next_position is None and len(page2.data.points) == 1
            point = page2.data.points[0]
            assert point.metric == "temperature" and point.value is None and point.reason
            assert point.observed_at == SOURCE
            assert point.evidence_id != page1.data.points[0].evidence_id
            empty = await reader.observations(
                ObservationQuery(
                    start=SOURCE + timedelta(hours=1),
                    end=SOURCE + timedelta(hours=2),
                    metrics=["temperature"],
                    limit=1,
                ),
                None,
            )
            assert empty.watermark and empty.data.points == () and empty.next_position is None
            assert int(empty.watermark) > int(page1.watermark)
        finally:
            await ingest.dispose()
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_api_pool_saturation_and_timeout_do_not_block_ingest_or_leak_driver_text() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        budget = PublicDatabaseBudget(
            pool_size=2, pool_timeout_seconds=0.05, statement_timeout_ms=500
        )
        api = await api_engine(budget)
        held = []
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
            reader = source(api, alias, budget)
            for _ in range(budget.pool_size):
                held.append(await api.connect())
            started = time.monotonic()
            with pytest.raises(PublicReadUnavailable) as failure:
                await reader.current()
            assert time.monotonic() - started < 2
            assert "postgresql" not in str(failure.value)
            async with asyncio.timeout(5), ingest.begin() as connection:
                await observation(connection, d)
            for connection in held:
                await connection.close()
            held.clear()
            with pytest.raises(PublicReadUnavailable):
                await reader._rows(text("SELECT pg_sleep(2)"), {})
            # A timed-out read rolls back and returns its bounded pool connection.
            rows = await reader._rows(
                text("""SELECT current_user AS role,
                current_setting('transaction_read_only') AS read_only"""),
                {},
            )
            assert rows == [{"role": "weather_api", "read_only": "on"}]
            assert (await reader.current()).newest_valid_obs_st_at == SOURCE
        finally:
            for connection in held:
                await connection.close()
            await api.dispose()
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())


def test_full_window_keeps_invalid_preceding_ties_and_following_report_support() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                await observation(connection, d, source=SOURCE - timedelta(seconds=120))
                first, _ = await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(seconds=30),
                    values={"air_temperature": None, "wind_avg": None},
                )
                tied, _ = await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(seconds=30),
                    values={"air_temperature": 17, "wind_avg": None},
                    row_idx=1,
                )
                await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(seconds=10),
                    values={"air_temperature": 20, "wind_avg": 2},
                )
                reported, reported_raw = await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(seconds=90),
                    values={"air_temperature": 21, "wind_avg": 3},
                )
                await connection.execute(
                    text("""UPDATE weather.obs_st SET quality=jsonb_set(
                    quality,'{fields,wind_avg,support}',CAST(:support AS jsonb)) WHERE id=:id"""),
                    {
                        "id": reported,
                        "support": json.dumps(
                            {
                                "kind": "reported_interval",
                                "qualified": True,
                                "reported_seconds": 60,
                                "policy": "v171-wind-report-interval-1",
                            }
                        ),
                    },
                )
                await observation(
                    connection,
                    d,
                    source=SOURCE - timedelta(seconds=5),
                    values={"air_temperature": 999},
                    source_eligible=False,
                )
            reader = source(api, alias, budget, seconds=300)
            window = Interval(start=SOURCE, end=SOURCE + timedelta(seconds=60))
            result = await read_evidence_window(
                reader,
                window,
                ["temperature", "wind_avg"],
                preceding_hold_seconds=90,
                following_report_seconds=60,
            )
            previous = [
                r
                for r in result.rows
                if r.point.observed_at < window.start and r.point.metric == "temperature"
            ]
            assert {r.observation_id for r in previous} == {first, tied}
            assert any(r.point.value is None and r.source_time_eligible for r in previous)
            following = next(
                r
                for r in result.rows
                if r.observation_id == reported and r.point.metric == "wind_avg"
            )
            assert following.point.support_interval.start == SOURCE + timedelta(seconds=30)
            assert following.point.support_interval.end == SOURCE + timedelta(seconds=90)
            async with ingest.begin() as connection:
                late, _ = await observation(connection, d, source=SOURCE + timedelta(seconds=20))
                conflict = await incident(
                    connection, reported, reported_raw, source=SOURCE + timedelta(seconds=90)
                )
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            repeated = await read_evidence_window(
                reader,
                window,
                ["temperature", "wind_avg"],
                preceding_hold_seconds=90,
                following_report_seconds=60,
                snapshot_watermark=result.watermark,
            )
            assert all(r.observation_id != late for r in repeated.rows)
            old_temperature = next(
                r
                for r in result.rows
                if r.observation_id == reported and r.point.metric == "temperature"
            )
            frozen_temperature = next(
                r
                for r in repeated.rows
                if r.observation_id == reported and r.point.metric == "temperature"
            )
            assert frozen_temperature.exact_value == old_temperature.exact_value == 21
            assert frozen_temperature.quality_revision == old_temperature.quality_revision
            fresh = await read_evidence_window(
                reader,
                window,
                ["temperature", "wind_avg"],
                preceding_hold_seconds=90,
                following_report_seconds=60,
            )
            current_temperature = next(
                r
                for r in fresh.rows
                if r.observation_id == reported and r.point.metric == "temperature"
            )
            assert (
                current_temperature.exact_value is None and current_temperature.point.value is None
            )
            assert current_temperature.quality_revision != old_temperature.quality_revision
            with pytest.raises(PublicInputError) as failure:
                await read_evidence_window(
                    reader,
                    window,
                    ["temperature", "wind_avg"],
                    preceding_hold_seconds=90,
                    following_report_seconds=60,
                    maximum_points=1,
                )
            assert failure.value.code == "range_too_large"
        finally:
            await api.dispose()
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())
