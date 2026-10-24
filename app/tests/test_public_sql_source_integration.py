# SPDX-License-Identifier: MIT
"""SQL-reader proofs; only the guarded fleet PostgreSQL socket fixture."""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from test_current_publication_integration import (
    SOURCE,
    device,
    engine,
    guard,
    incident,
    observation,
    receipt,
)
from test_current_publication_integration import disposable_database as disposable_database
from weather.api.cursors import CursorCodec, Snapshot
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import Interval, ObservationQuery
from weather.api.public_service import PublicWeatherService
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


def test_real_backend_outage_ages_cache_and_cold_process_recovers_persisted_values() -> None:
    async def check() -> None:
        db = engine()
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        elapsed = [1.0]
        def clock() -> datetime:
            return SOURCE + timedelta(seconds=elapsed[0])

        services = []
        login_disabled = False
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                await observation(connection, d, values={"air_temperature": 21})
            reader = PublicSqlSource(
                api,
                station_alias=alias,
                maximum_raw_seconds=30 * 86400,
                policies={"temperature": FreshnessPolicy(60, 90)},
                budget=budget,
                clock=clock,
            )

            def service() -> PublicWeatherService:
                value = PublicWeatherService(
                    reader,
                    CursorCodec(b"synthetic-cursor-key" * 4, policy_revision="synthetic_v1"),
                    maximum_raw_seconds=30 * 86400,
                    clock=clock,
                    monotonic=lambda: elapsed[0],
                )
                services.append(value)
                return value

            warm = service()
            initial = await warm.current()
            temperature = next(m for m in initial.metrics if m.metric == "temperature")
            assert initial.data_state == "live" and temperature.freshness == "fresh"
            async with db.begin() as connection:
                # The guard proves this process owns an isolated socket-only PG
                # instance before changing its test role or terminating backends.
                await guard(connection)
                await connection.execute(text("ALTER ROLE weather_api NOLOGIN"))
                login_disabled = True
                terminated = (
                    await connection.execute(
                        text("""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                        WHERE usename='weather_api' AND datname=current_database()""")
                    )
                ).scalars().all()
                assert terminated and all(terminated)
            elapsed[0] = 121
            cached = await warm.current()
            stale = next(m for m in cached.metrics if m.metric == "temperature")
            assert cached.data_state == "cached" and stale.freshness == "stale"
            assert stale.value == temperature.value == 21
            assert stale.observed_at == temperature.observed_at == SOURCE
            assert stale.received_at == temperature.received_at
            assert warm.metrics.registry.get_sample_value(
                "weather_current_conditions_age_seconds"
            ) == 121
            cold = service()
            with pytest.raises(PublicInputError) as failure:
                await cold.current()
            assert failure.value.code == "unavailable"
            assert str(failure.value) == "Weather data is temporarily unavailable."
            async with db.begin() as connection:
                await connection.execute(text("ALTER ROLE weather_api LOGIN"))
                login_disabled = False
                await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(seconds=120),
                    received=SOURCE + timedelta(seconds=120),
                    values={"air_temperature": 22},
                )
            elapsed[0] = 127
            for value in services:
                recovered = await value.current()
                metric = next(m for m in recovered.metrics if m.metric == "temperature")
                assert recovered.data_state == "live" and metric.freshness == "fresh"
                assert metric.value == 22 and metric.observed_at == SOURCE + timedelta(seconds=120)
        finally:
            if login_disabled:
                async with db.begin() as connection:
                    await guard(connection)
                    await connection.execute(text("ALTER ROLE weather_api LOGIN"))
            for value in services:
                await value.aclose()
            await api.dispose()
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


def test_evidence_snapshot_evaluation_keeps_tolerated_future_sample_ineligible() -> None:
    async def check() -> None:
        db = engine()
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                await observation(connection, d, source=SOURCE - timedelta(seconds=10))
                future, _ = await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(seconds=5),
                    values={"air_temperature": 22},
                )
            window = Interval(
                start=SOURCE + timedelta(seconds=10), end=SOURCE + timedelta(seconds=30)
            )
            first = await read_evidence_window(
                source(api, alias, budget, seconds=0),
                window,
                ["temperature"],
                preceding_hold_seconds=30,
                following_report_seconds=0,
                evaluated_at=SOURCE,
            )
            repeated = await read_evidence_window(
                source(api, alias, budget, seconds=60),
                window,
                ["temperature"],
                preceding_hold_seconds=30,
                following_report_seconds=0,
                snapshot_watermark=first.watermark,
                evaluated_at=SOURCE,
            )
            assert repeated == first
            assert all(r.observation_id != future for r in repeated.rows)
            assert repeated.rows[0].point.observed_at == SOURCE - timedelta(seconds=10)
            advanced = await read_evidence_window(
                source(api, alias, budget, seconds=60),
                window,
                ["temperature"],
                preceding_hold_seconds=30,
                following_report_seconds=0,
                snapshot_watermark=first.watermark,
                evaluated_at=SOURCE + timedelta(seconds=60),
            )
            eligible = next(r for r in advanced.rows if r.observation_id == future)
            assert eligible.source_time_eligible and eligible.exact_value == 22
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_coarse_aggregate_evidence_read_keeps_empty_watermark_and_exact_overflow() -> None:
    async def check() -> None:
        db = engine()
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
            reader = source(api, alias, budget)
            window = Interval(start=SOURCE - timedelta(days=180), end=SOURCE + timedelta(days=185))
            kwargs = {"preceding_hold_seconds": 0, "following_report_seconds": 0}
            with pytest.raises(PublicInputError):
                await read_evidence_window(reader, window, ["temperature"], **kwargs)
            empty = await read_evidence_window(
                reader, window, ["temperature"], enforce_raw_range=False, **kwargs
            )
            assert empty.watermark == "0" and empty.rows == ()
            async with db.begin() as connection:
                await observation(connection, d)
                await observation(connection, d, source=SOURCE + timedelta(seconds=1))
            with pytest.raises(PublicInputError) as failure:
                await read_evidence_window(
                    reader,
                    window,
                    ["temperature"],
                    enforce_raw_range=False,
                    maximum_points=1,
                    **kwargs,
                )
            assert failure.value.code == "range_too_large"
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_lightning_distance_correlates_same_family_row_and_safe_revision_companion() -> None:
    async def check() -> None:
        db, ingest = engine(), engine("weather_ingest")
        budget = PublicDatabaseBudget(pool_size=2)
        api = await api_engine(budget)
        try:
            async with db.begin() as connection:
                d, alias = await station(connection)
                zero, _ = await observation(
                    connection,
                    d,
                    values={"lightning_strike_count": 0, "lightning_strike_avg_distance": 0},
                )
                # IDs are family-scoped. A different family with the same UUID
                # and qualified strikes must never qualify this newer zero row.
                await observation(
                    connection,
                    d,
                    family="obs_air",
                    identity=zero,
                    source=SOURCE - timedelta(seconds=10),
                    values={"lightning_strike_count": 9, "lightning_strike_avg_distance": 33},
                )
            reader = source(api, alias, budget)
            metrics = {m.metric: m for m in (await reader.current()).current.metrics}
            assert metrics["lightning_count"].value == 0
            assert metrics["lightning_distance"].value is None
            query = ObservationQuery(
                start=SOURCE - timedelta(seconds=20),
                end=SOURCE + timedelta(seconds=20),
                metrics=["lightning_distance"],
                limit=10,
            )
            points = (await reader.observations(query, None)).data.points
            assert [p.value for p in points] == [33, None]
            async with db.begin() as connection:
                positive, raw = await observation(
                    connection,
                    d,
                    source=SOURCE + timedelta(seconds=10),
                    values={"lightning_strike_count": 2, "lightning_strike_avg_distance": 0},
                )
            metrics = {m.metric: m for m in (await reader.current()).current.metrics}
            assert metrics["lightning_distance"].value == 0
            window = Interval(start=query.start, end=query.end)
            frozen = await read_evidence_window(
                reader,
                window,
                ["lightning_distance"],
                preceding_hold_seconds=0,
                following_report_seconds=0,
            )
            assert next(r for r in frozen.rows if r.observation_id == positive).exact_value == 0
            async with ingest.begin() as connection:
                alternate = await receipt(connection)
                conflict = (
                    await connection.execute(
                        text("""INSERT INTO weather.observation_conflict
                    (family,observation_id,first_receipt_id,conflicting_receipt_id,fields,
                     source_ts,received_at)
                    VALUES('obs_st',:id,:first,:alternate,ARRAY['lightning_strike_count'],:ts,:ts)
                    RETURNING id"""),
                        {
                            "id": positive,
                            "first": raw,
                            "alternate": alternate,
                            "ts": SOURCE + timedelta(seconds=10),
                        },
                    )
                ).scalar_one()
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            metrics = {m.metric: m for m in (await reader.current()).current.metrics}
            assert metrics["lightning_count"].value is None
            assert metrics["lightning_distance"].value is None
            assert (await reader.observations(query, None)).data.points[-1].value is None
            old = await read_evidence_window(
                reader,
                window,
                ["lightning_distance"],
                preceding_hold_seconds=0,
                following_report_seconds=0,
                snapshot_watermark=frozen.watermark,
            )
            assert next(r for r in old.rows if r.observation_id == positive).exact_value == 0
            fresh = await read_evidence_window(
                reader,
                window,
                ["lightning_distance"],
                preceding_hold_seconds=0,
                following_report_seconds=0,
            )
            invalid = next(r for r in fresh.rows if r.observation_id == positive)
            assert invalid.point.value is None and invalid.exact_value is None
            assert (
                invalid.quality_revision
                != next(r for r in frozen.rows if r.observation_id == positive).quality_revision
            )
        finally:
            await api.dispose()
            await ingest.dispose()
            await db.dispose()

    asyncio.run(check())
