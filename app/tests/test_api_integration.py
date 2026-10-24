# SPDX-License-Identifier: MIT
"""API reads against the ephemeral PostgreSQL in fleet CI, as the weather_api role.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.api.cursors import CursorCodec
from weather.api.models import AggregateQuery, AlmanacQuery, ObservationQuery
from weather.api.registry import PublicError
from weather.api.service import WeatherService
from weather.api.source import SourceUnavailable, WeatherReader, latest_sql, observations_sql
from weather.db.models import metadata

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]
ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/aggregation-vectors.json").read_text())["cases"]
VALID = '{"valid":true,"reason":"valid","invalid":[]}'
INVALID = '{"valid":false,"reason":"unknown","invalid":[]}'


def engine(role: str = "postgres", pool_size: int = 4) -> AsyncEngine:
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or socket.name != "socket"
        or not socket.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=pool_size, max_overflow=0)


def database(test: Callable[[AsyncEngine, AsyncEngine, UUID], Awaitable[None]]) -> None:
    """Fresh telemetry tables, a seeded station/device and an API-role engine for one test."""

    async def run() -> None:
        admin, api = engine(), engine("weather_api")
        try:
            async with admin.begin() as c:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
                station = (
                    await c.execute(
                        text(
                            "INSERT INTO weather.station(alias,site) VALUES('home','local')"
                            " RETURNING id"
                        )
                    )
                ).scalar_one()
                device = (
                    await c.execute(
                        text("""INSERT INTO weather.device(station_id,alias,private_id,kind,
                                  valid_from) VALUES(:s,'st','SYNTHETIC-ST','ST',
                                  '2020-01-01T00:00:00Z') RETURNING id"""),
                        {"s": station},
                    )
                ).scalar_one()
            await test(admin, api, device)
        finally:
            await admin.dispose()
            await api.dispose()

    asyncio.run(run())


async def insert(
    c: AsyncConnection,
    device: UUID,
    family: str,
    rows: list[tuple[datetime, dict[str, Any]]],
    *,
    received: datetime | None = None,
    inserted: datetime | None = None,
    quality: str = VALID,
    row_idx: int = 0,
) -> None:
    """Family rows; inserted_at defaults to the database clock, like real ingest."""
    ids = [uuid4() for _ in rows]
    await c.execute(
        text("""INSERT INTO weather.telemetry_receipt(id,received_at,raw_bytes,sha256,
                  parse_state,parser_revision,receiver_incarnation)
                SELECT id,received,convert_to(id::text,'UTF8'),
                  encode(sha256(convert_to(id::text,'UTF8')),'hex'),'parsed','test','test'
                FROM unnest(CAST(:ids AS uuid[]),CAST(:received AS timestamptz[]))
                  AS t(id,received)"""),
        {"ids": ids, "received": [received or at + timedelta(seconds=1) for at, _ in rows]},
    )
    await c.execute(
        text(f"""INSERT INTO weather.{family}(device_id,receipt_id,source_ts,row_idx,
                  "values",quality,inserted_at)
                SELECT :device,rid,ts,:row_idx,CAST(v AS jsonb),CAST(:quality AS jsonb),
                  coalesce(CAST(:inserted AS timestamptz),clock_timestamp())
                FROM unnest(CAST(:ids AS uuid[]),CAST(:ts AS timestamptz[]),
                  CAST(:vals AS text[])) AS t(rid,ts,v)"""),
        {
            "device": device,
            "ids": ids,
            "ts": [at for at, _ in rows],
            "vals": [json.dumps(values) for _, values in rows],
            "quality": quality,
            "inserted": inserted,
            "row_idx": row_idx,
        },
    )


def service(api: AsyncEngine, **kwargs: Any) -> WeatherService:
    return WeatherService(
        WeatherReader(api), CursorCodec(b"k" * 64), registry=CollectorRegistry(), **kwargs
    )


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_current_reads_newest_valid_rows_rain_and_station() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        async with admin.begin() as c:
            await insert(
                c,
                device,
                "obs_st",
                [
                    (now - timedelta(seconds=s), {"air_temperature": t, "precip_accumulated": 0.1})
                    for s, t in ((180, 20), (120, 21))
                ],
            )
            # The newest valid row has one field the parser flagged invalid.
            await insert(
                c,
                device,
                "obs_st",
                [
                    (
                        now - timedelta(seconds=60),
                        {"air_temperature": 22, "precip_accumulated": 0.1, "uv": 99},
                    )
                ],
                quality='{"valid":true,"reason":"valid","invalid":["uv"]}',
            )
            await insert(c, device, "obs_st", [(now - timedelta(seconds=30), {})], quality=INVALID)
            await insert(c, device, "obs_st", [(now + timedelta(minutes=5), {"uv": 9})])
            await insert(
                c, device, "rapid_wind", [(now - timedelta(seconds=2), {"wind_speed": 0})]
            )
        weather = service(api, clock=lambda: now)
        await weather.refresh()
        current = weather.current()
        values = {m.metric: m for m in current.metrics}
        assert values["uv"].value is None and values["uv"].quality == "invalid"
        assert current.station_alias == "home" and current.data_state == "live"
        assert values["temperature"].value == 22
        assert values["temperature"].observed_at == now - timedelta(seconds=60)
        assert values["wind_speed"].value == 0 and values["wind_speed"].freshness == "fresh"
        assert values["rain_last_hour"].value == pytest.approx(0.3)
        assert values["rain_last_hour"].coverage == pytest.approx(180 / 3600, abs=1e-3)
        assert weather.ready()
        # Current rain and the history sum apply the same support rule to the same window.
        hour = await weather.aggregates(
            AggregateQuery.model_validate(
                {
                    "start": now - timedelta(hours=1),
                    "end": now,
                    "metrics": ["precipitation"],
                    "statistics": ["sum"],
                    "resolution": "600",
                }
            )
        )
        assert sum(item.value or 0 for item in hour.items) == pytest.approx(0.3)
        assert sum(item.coverage.support_seconds for item in hour.items) == pytest.approx(180)

    database(check)


def test_current_and_keyset_queries_use_the_source_ts_btree() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        async with admin.begin() as c:
            await insert(
                c,
                device,
                "obs_st",
                [(start + timedelta(minutes=n), {"air_temperature": 1}) for n in range(30000)],
            )
            await insert(
                c,
                device,
                "rapid_wind",
                [(start + timedelta(seconds=3 * n), {"wind_speed": 1}) for n in range(30000)],
            )
        async with admin.begin() as c:
            await c.execute(text("ANALYZE weather.obs_st, weather.rapid_wind"))
            await c.execute(text("ANALYZE weather.telemetry_receipt"))
        plans = []
        async with api.connect() as c:
            for family in ("obs_st", "rapid_wind"):
                for sql, params in (
                    (latest_sql(family), {"now": start + timedelta(days=60)}),
                    (
                        observations_sql(family),
                        {
                            "end": start + timedelta(days=30),
                            "snapshot": start + timedelta(days=60),
                            "after_time": start + timedelta(days=10),
                            "after_id": UUID(int=0),
                            "rows": 1001,
                        },
                    ),
                ):
                    plan = (await c.execute(text("EXPLAIN " + sql), params)).scalars().all()
                    plans.append((family, "\n".join(plan)))
        for family, plan in plans:
            assert f"ix_{family}_source_ts" in plan, plan
            assert f"Seq Scan on {family}" not in plan, plan

    database(check)


def test_thirty_day_export_is_stable_while_late_rows_land() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        start = datetime(2026, 6, 1, tzinfo=UTC)
        async with admin.begin() as c:
            await insert(
                c,
                device,
                "obs_st",
                [(start + timedelta(minutes=10 * n), {"uv": n}) for n in range(4320)],
                received=datetime.now(UTC) - timedelta(hours=1),
                inserted=datetime.now(UTC) - timedelta(hours=1),
            )
        weather = service(api)
        query = ObservationQuery.model_validate(
            {
                "start": "2026-06-01T00:00:00Z",
                "end": "2026-07-01T00:00:00Z",
                "metrics": ["uv"],
                "limit": 1000,
            }
        )
        seen: list[str] = []
        values: list[float | None] = []
        page = await weather.observations(query)
        pages = 0
        while True:
            seen.extend(point.id for point in page.points)
            values.extend(point.value for point in page.points)
            # Rows land mid-export, one behind and one ahead of the current position. Like a
            # spool drain or reproject, their received_at is old (before the snapshot).
            async with admin.begin() as c:
                for index in (400 * pages, (1000 * pages + 1500) % 4320):
                    at = start + timedelta(minutes=10 * index + 5)
                    await insert(
                        c, device, "obs_st", [(at, {"uv": -1})], received=at + timedelta(seconds=1)
                    )
            pages += 1
            if page.next_cursor is None:
                break
            page = await weather.observations(query.model_copy(update={"cursor": page.next_cursor}))
        assert len(seen) == len(set(seen)) == 4320
        assert -1 not in values
        async with admin.connect() as c:
            landed = (
                await c.execute(
                    text("""SELECT count(*) FROM weather.obs_st WHERE "values"->>'uv'='-1'""")
                )
            ).scalar_one()
        assert landed == 2 * pages == 10
        with pytest.raises(PublicError) as error:
            await weather.observations(query.model_copy(update={"cursor": "AAAA.BBBB"}))
        assert error.value.code == "invalid_cursor"

    database(check)


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_aggregation_vectors(case: dict[str, Any]) -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        async with admin.begin() as c:
            for family in ("obs_st", "rapid_wind"):
                # row_idx > 0 repeats a source time, like a duplicated packet row.
                for row_idx in sorted({r.get("row_idx", 0) for r in case["rows"]}):
                    rows = [
                        (utc(r["at"]), r["values"])
                        for r in case["rows"]
                        if r["family"] == family and r.get("row_idx", 0) == row_idx
                    ]
                    if rows:
                        await insert(c, device, family, rows, row_idx=row_idx)
        weather = service(api)
        query = AggregateQuery.model_validate(case["query"])
        if "expected_error" in case:
            with pytest.raises(PublicError) as error:
                await weather.aggregates(query)
            assert error.value.code == case["expected_error"]
            return
        result = await weather.aggregates(query)
        items = {(item.start, item.statistic): item for item in result.items}
        for expected in case["expected"]:
            item = items[(utc(expected["start"]), expected["statistic"])]
            if "end" in expected:
                assert item.end == utc(expected["end"])
            if "value" in expected and expected["value"] is None:
                assert item.value is None and item.reason, expected
            elif "value" in expected:
                assert item.value == pytest.approx(expected["value"], abs=1e-6), expected
            if "support_seconds" in expected:
                assert item.coverage.support_seconds == pytest.approx(expected["support_seconds"])
            if "evidence_count" in expected:
                assert item.evidence_count == expected["evidence_count"]
                assert (item.evidence_hash is None) == (expected["evidence_count"] == 0)
            if "direction_state" in expected:
                assert item.direction_state == expected["direction_state"]
            if "rose" in expected:
                assert item.rose is not None
                durations: dict[str, float] = {}
                for b in item.rose:
                    key = (
                        f"directional:{b.sector_start}:{b.speed_min}"
                        if b.category == "directional"
                        else b.category
                    )
                    durations[key] = b.duration_seconds
                assert durations == pytest.approx(expected["rose"])
                assert sum(durations.values()) == pytest.approx(item.coverage.expected_seconds)
        if "conserved_total" in case:
            hourly = await weather.aggregates(
                query.model_copy(update={"resolution": "3600"})
            )
            total = sum(item.value or 0 for item in hourly.items)
            daily = sum(item.value or 0 for item in result.items)
            assert total == pytest.approx(case["conserved_total"])
            assert daily == pytest.approx(case["conserved_total"])

    database(check)


def test_almanac_records_history_and_corrections_are_read_from_the_database() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        first = utc("2026-01-01T07:00:00Z")  # local midnight, MST
        second = first + timedelta(days=1)
        async with admin.begin() as c:
            # Day 1: every minute, highest 15. Day 2: only 90% covered, but reaching 20.
            await insert(
                c,
                device,
                "obs_st",
                [
                    (first + timedelta(minutes=n), {"air_temperature": 15 if n == 600 else 10})
                    for n in range(1440)
                ]
                + [
                    (second + timedelta(minutes=n), {"air_temperature": 20 if n == 60 else 5})
                    for n in range(1296)
                ],
            )
        weather = service(api)
        query = AlmanacQuery.model_validate(
            {
                "product": "record",
                "metric": "temperature",
                "statistic": "max",
                "start": "2026-01-01T07:00:00Z",
                "end": "2026-01-03T07:00:00Z",
            }
        )
        result = await weather.almanac(query)
        assert result.history_interval is not None
        assert result.history_interval.start == first
        assert result.label == "Observed extremes within station history since 2026-01-01"
        top, thin = result.items
        assert (top.value, top.rank, top.coverage.fraction) == (15, 1, 1)
        assert thin.value == 20 and thin.rank is None and not thin.ranking_eligible
        assert thin.coverage.fraction == pytest.approx((1295 * 60 + 120) / 86400, abs=1e-6)
        assert "below_record_coverage" in thin.missing_reasons
        # A corrected value changes the next read; a late row changes the evidence hash.
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.obs_st SET "values"='{"air_temperature":12}'::jsonb
                        WHERE source_ts=:at"""),
                {"at": first + timedelta(minutes=600)},
            )
        corrected = await weather.almanac(query)
        assert corrected.items[0].value == 12 and corrected.items[0].rank == 1
        async with admin.begin() as c:
            late_row = (second + timedelta(hours=23), {"air_temperature": 5})
            await insert(c, device, "obs_st", [late_row])
        late = await weather.almanac(query)
        assert late.items[1].evidence_count == thin.evidence_count + 1
        assert late.evidence_hash != corrected.evidence_hash
        rain = await weather.almanac(
            AlmanacQuery.model_validate(
                {"product": "rain_day", "start": "2025-12-31T07:00:00Z", "end": query.end}
            )
        )
        assert [item.missing_reasons for item in rain.items] == [
            ["before_history", "no_observations"],
            ["no_observations"],
            ["no_observations"],
        ]

    database(check)


def test_db_down_serves_cache_until_stale_and_cold_is_503() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        now = datetime.now(UTC)
        async with admin.begin() as c:
            await insert(c, device, "obs_st", [(now - timedelta(seconds=10), {"uv": 0})])
        clock = {"at": now}
        weather = service(api, clock=lambda: clock["at"])
        await weather.refresh()
        assert weather.current().data_state == "live"
        down = create_async_engine(
            "postgresql+asyncpg://weather_api@/weather_ci?host=/nonexistent-weather-socket"
        )
        try:
            weather.reader = WeatherReader(down)
            await weather.refresh()
            cached = weather.current()
            uv = next(m for m in cached.metrics if m.metric == "uv")
            assert cached.data_state == "cached" and uv.value == 0
            assert uv.observed_at == now - timedelta(seconds=10)
            clock["at"] = now + timedelta(seconds=150)
            with pytest.raises(PublicError) as error:
                weather.current()
            assert error.value.code == "unavailable"
            cold = service(api)
            cold.reader = WeatherReader(down)
            await cold.refresh()
            with pytest.raises(PublicError):
                cold.current()
            assert not cold.ready()
        finally:
            await down.dispose()

    database(check)


def test_slow_query_is_sanitized_and_ingest_commits_while_api_pool_is_saturated() -> None:
    async def check(admin: AsyncEngine, api: AsyncEngine, device: UUID) -> None:
        reader = WeatherReader(api)
        with pytest.raises(SourceUnavailable) as error:
            await reader._run([("SELECT pg_sleep(2)", {})], 100)
        assert "sleep" not in str(error.value) and error.value.__cause__ is None
        holds = [
            asyncio.create_task(reader._run([("SELECT pg_sleep(3)", {})], 5000))
            for _ in range(5)  # four fill the pool, one waits
        ]
        await asyncio.sleep(0.5)
        ingest = engine("weather_ingest", pool_size=1)
        try:
            started = time.monotonic()
            async with ingest.begin() as c:
                await insert(c, device, "obs_st", [(datetime.now(UTC), {"uv": 1})])
            assert time.monotonic() - started < 1.5
        finally:
            await ingest.dispose()
        await asyncio.gather(*holds, return_exceptions=True)

    database(check)
