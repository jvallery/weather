# SPDX-License-Identifier: MIT
"""Public API behaviour without a database: cache, freshness, cursors, routes and summaries."""

import importlib.util
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy import text
from weather.api import build_app
from weather.api.cursors import CursorCodec
from weather.api.models import AggregateQuery, ObservationQuery
from weather.api.registry import COLUMNS, DEFAULT_CADENCES, REGISTRY, PublicError
from weather.api.service import WeatherService, bin_list, bin_spec, build_current, summarize
from weather.api.source import Reading, SourceUnavailable

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
KEY = b"k" * 64


def st_row(at: datetime, **values: float | None) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(COLUMNS["obs_st"])
    row.update(
        id=uuid4(),
        observed_at=at,
        received_at=at + timedelta(seconds=1),
        inserted_at=at + timedelta(seconds=2),
        valid=True,
        invalid_fields=[],
        air_temperature=0.0,
        lightning_strike_count=0.0,
        lightning_strike_avg_distance=0.0,
    )
    row.update(values)
    return row


def reading(at: datetime = T0, **values: float | None) -> Reading:
    rain = {
        "hour": {
            "total": 0.0,
            "support_seconds": 3600.0,
            "observed_at": at,
            "received_at": at + timedelta(seconds=1),
        },
        "day": {"total": None, "support_seconds": 0, "observed_at": None, "received_at": None},
    }
    return Reading("home", st_row(at, **values), None, rain, at)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at, self.mono = at, 0.0

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.mono += seconds


class FakeReader:
    def __init__(self) -> None:
        self.reading: Reading | None = reading()
        self.down = False
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.snapshot = T0

    async def current(self, now: datetime, hour: datetime, day: datetime) -> Reading:
        if self.down or self.reading is None:
            raise SourceUnavailable()
        return self.reading

    async def ping(self) -> bool:
        return not self.down

    async def observations(
        self, family: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], datetime]:
        self.calls.append(kwargs)
        snapshot = kwargs["snapshot"] or self.snapshot
        rows = [
            r
            for r in self.rows
            if (r["observed_at"], r["id"]) > (kwargs["after_time"], kwargs["after_id"])
            and r["observed_at"] < kwargs["end"]
            and r["inserted_at"] <= snapshot
        ]
        return sorted(rows, key=lambda r: (r["observed_at"], r["id"]))[: kwargs["rows"]], snapshot

    async def aggregates(self, *args: Any, **kwargs: Any) -> tuple[list[Any], list[Any]]:
        return [], []


def service(clock: Clock, reader: FakeReader) -> WeatherService:
    return WeatherService(
        reader,  # type: ignore[arg-type]
        CursorCodec(KEY),
        registry=CollectorRegistry(),
        clock=clock.now,
        monotonic=clock.monotonic,
    )


def run(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)


def test_registry_columns_match_the_migration_views() -> None:
    spec = importlib.util.spec_from_file_location(
        "migration_0002", ROOT / "app/migrations/versions/0002_api.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {k: tuple(v) for k, v in module.COLUMNS.items()} == COLUMNS
    for statement in module.statements():
        assert not text(statement)._bindparams


def test_current_keeps_zero_and_ages_from_source_time() -> None:
    current = build_current(reading(), T0 + timedelta(seconds=30), DEFAULT_CADENCES)
    temperature = next(m for m in current.metrics if m.metric == "temperature")
    assert temperature.value == 0 and temperature.freshness == "fresh"
    assert temperature.valid_until == T0 + timedelta(seconds=150)
    assert temperature.expected_interval_seconds == 60
    later = build_current(reading(), T0 + timedelta(seconds=151), DEFAULT_CADENCES)
    aged = next(m for m in later.metrics if m.metric == "temperature")
    assert aged.freshness == "stale" and aged.observed_at == T0 and aged.reason
    humidity = next(m for m in current.metrics if m.metric == "humidity")
    assert humidity.value is None and humidity.quality == "missing" and humidity.reason
    distance = next(m for m in current.metrics if m.metric == "lightning_distance")
    assert distance.value is None and distance.reason == "No lightning in the newest report."
    hour = next(m for m in current.metrics if m.metric == "rain_last_hour")
    assert hour.value == 0 and hour.coverage == 1
    today = next(m for m in current.metrics if m.metric == "rain_today")
    assert today.value is None and today.freshness == "unavailable"
    wind = next(m for m in current.metrics if m.metric == "wind_speed")
    assert wind.value is None and wind.expected_interval_seconds == 3
    assert {m.metric for m in current.metrics} == {e for e in REGISTRY if e != "precipitation"}


def test_empty_station_is_unavailable_not_zero() -> None:
    current = build_current(Reading(None, None, None, {}, T0), T0, DEFAULT_CADENCES)
    assert current.data_state == "unavailable"
    assert all(m.value is None and m.reason for m in current.metrics)


def test_db_down_serves_cache_with_original_times_until_stale_then_503() -> None:
    clock, reader = Clock(T0), FakeReader()
    weather = service(clock, reader)
    with pytest.raises(PublicError):
        weather.current()  # cold: nothing cached
    assert not weather.ready()
    run(weather.refresh())
    assert weather.current().data_state == "live" and weather.ready()
    reader.down = True
    clock.advance(60)
    run(weather.refresh())
    cached = weather.current()
    assert cached.data_state == "cached"
    assert next(m for m in cached.metrics if m.metric == "temperature").observed_at == T0
    clock.advance(31)
    assert weather.ready()  # DB silent past 30 s, but the cache is still fresh
    clock.advance(60)  # past observed_at + 150 s
    with pytest.raises(PublicError) as error:
        weather.current()
    assert error.value.code == "unavailable" and not weather.ready()
    reader.down = False
    run(weather.refresh())
    assert weather.current().data_state == "live"


def test_current_route_etag_and_cache_headers() -> None:
    clock, reader = Clock(T0 + timedelta(seconds=10)), FakeReader()
    weather = service(clock, reader)
    run(weather.refresh())
    client = TestClient(build_app(weather))
    response = client.get("/api/v1/weather/current")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=5"
    tag = response.headers["etag"]
    assert tag.startswith('W/"')
    assert response.json()["metrics"][0]["observed_at"] == "2026-07-01T18:00:00Z"
    clock.advance(1)
    unchanged = client.get("/api/v1/weather/current", headers={"If-None-Match": tag})
    assert unchanged.status_code == 304 and unchanged.headers["etag"] == tag
    clock.advance(200)  # every value is now stale, so the representation changed
    changed = client.get("/api/v1/weather/current", headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag
    assert client.get("/readyz").status_code == 200


def test_metrics_endpoint_and_prometheus_ages() -> None:
    clock, reader = Clock(T0 + timedelta(seconds=10)), FakeReader()
    registry = CollectorRegistry()
    weather = WeatherService(
        reader,  # type: ignore[arg-type]
        CursorCodec(KEY),
        registry=registry,
        clock=clock.now,
        monotonic=clock.monotonic,
    )
    assert b"weather_current_conditions_age_seconds" not in generate_latest(registry)
    run(weather.refresh())
    text = generate_latest(registry).decode()
    assert "weather_current_conditions_age_seconds 10.0" in text
    assert 'weather_metric_source_age_seconds{metric="temperature"} 10.0' in text
    body = TestClient(build_app(weather)).get("/api/v1/weather/metrics").json()
    temperature = next(m for m in body["metrics"] if m["metric"] == "temperature")
    assert temperature["statistics"] == ["observed", "mean", "min", "max"]
    assert temperature["conversions"][1] == {
        "unit": "degF",
        "scale_numerator": 9,
        "scale_denominator": 5,
        "offset_numerator": 32,
        "offset_denominator": 1,
    }


@pytest.mark.parametrize(
    "query,code",
    [
        ("metrics=nope", "unknown_metric"),
        ("metrics=rain_today", "unsupported_statistic"),
        ("metrics=temperature&metrics=wind_speed", "invalid_request"),
        ("metrics=temperature&limit=5001", "invalid_request"),
        ("metrics=temperature&cursor=abc.def", "invalid_cursor"),
    ],
)
def test_observation_requests_are_validated(query: str, code: str) -> None:
    clock = Clock(T0)
    client = TestClient(build_app(service(clock, FakeReader())))
    response = client.get(
        f"/api/v1/weather/observations?start=2026-07-01T00:00:00Z&end=2026-07-01T06:00:00Z&{query}"
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == code


def test_over_range_suggests_a_resolution() -> None:
    client = TestClient(build_app(service(Clock(T0), FakeReader())))
    response = client.get(
        "/api/v1/weather/observations?start=2026-06-01T00:00:00Z&end=2026-07-03T00:00:00Z"
        "&metrics=temperature"
    )
    body = response.json()
    assert response.status_code == 400 and body["code"] == "range_too_large"
    assert body["suggested_resolution_seconds"] == math.ceil(32 * 86400 / 5000)
    aggregates = client.get(
        "/api/v1/weather/aggregates?start=2026-06-01T00:00:00Z&end=2026-07-01T00:00:00Z"
        "&metrics=temperature&statistics=mean&resolution=60"
    )
    assert aggregates.status_code == 400 and aggregates.json()["code"] == "range_too_large"
    assert aggregates.json()["suggested_resolution_seconds"] == 519
    day = "/api/v1/weather/aggregates?start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"
    for bad in (
        "metrics=temperature&statistics=median&resolution=3600",
        "metrics=temperature&statistics=sum&resolution=3600",
        "metrics=temperature&statistics=observed&resolution=3600",
        "metrics=temperature&statistics=mean&resolution=59",
        "metrics=rain_today&statistics=sum&resolution=3600",
    ):
        assert client.get(day + "&" + bad).status_code == 400, bad


def test_cursor_paging_is_stable_while_rows_land() -> None:
    clock, reader = Clock(T0), FakeReader()
    base = T0 - timedelta(hours=2)
    reader.rows = [st_row(base + timedelta(minutes=n), air_temperature=float(n)) for n in range(10)]
    reader.rows[3]["observed_at"] = reader.rows[4]["observed_at"]  # a timestamp tie
    client = TestClient(build_app(service(clock, reader)))
    url = (
        "/api/v1/weather/observations?start=2026-07-01T16:00:00Z&end=2026-07-01T18:00:00Z"
        "&metrics=temperature&limit=4"
    )
    seen: list[str] = []
    page = client.get(url).json()
    while True:
        seen.extend(point["id"] for point in page["points"])
        # A late row lands inside the window after the export started; like a spool drain or
        # reproject, its received_at predates the snapshot but its insert time does not.
        late = st_row(base + timedelta(seconds=30))
        late["received_at"] = base
        late["inserted_at"] = reader.snapshot + timedelta(seconds=1)
        reader.rows.append(late)
        clock.advance(600)
        if not page["next_cursor"]:
            break
        page = client.get(url + "&cursor=" + page["next_cursor"]).json()
    assert len(seen) == len(set(seen)) == 10
    assert reader.calls[0]["snapshot"] is None
    assert all(call["snapshot"] == T0 for call in reader.calls[1:])
    first = client.get(url).json()
    tampered = first["next_cursor"][:-2] + ("A" if first["next_cursor"][-2] != "A" else "B") + "x"
    assert client.get(url + "&cursor=" + tampered).json()["code"] == "invalid_cursor"
    other = url.replace("limit=4", "limit=5")
    assert client.get(other + "&cursor=" + first["next_cursor"]).json()["code"] == (
        "invalid_cursor"
    )


def test_cursor_ttl_slides_but_has_an_absolute_cap() -> None:
    codec = CursorCodec(KEY, sliding_seconds=3600, absolute_seconds=86400)
    query = ObservationQuery.model_validate(
        {"start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z", "metrics": ["uv"]}
    )
    at = T0
    token = codec.encode(query, snapshot=T0, after_time=query.start, after_id=UUID(int=1), now=at)
    position = codec.decode(token, query, now=at)
    for _ in range(23):  # keep paging every 59 minutes for almost a day
        at += timedelta(minutes=59)
        position = codec.decode(token, query, now=at)
        token = codec.encode(
            query,
            snapshot=T0,
            after_time=query.start,
            after_id=UUID(int=1),
            now=at,
            previous=position,
        )
    with pytest.raises(PublicError):
        codec.decode(token, query, now=T0 + timedelta(hours=24))
    idle = codec.encode(query, snapshot=T0, after_time=query.start, after_id=UUID(int=1), now=T0)
    with pytest.raises(PublicError):
        codec.decode(idle, query, now=T0 + timedelta(minutes=61))


def aggregate_query(start: str, end: str, resolution: str) -> AggregateQuery:
    return AggregateQuery.model_validate(
        {
            "start": start,
            "end": end,
            "metrics": ["temperature"],
            "statistics": ["mean"],
            "resolution": resolution,
        }
    )


@pytest.mark.parametrize(
    "start,end,hours",
    [
        ("2026-03-08T07:00:00Z", "2026-03-09T06:00:00Z", [23]),
        ("2026-11-01T06:00:00Z", "2026-11-02T07:00:00Z", [25]),
        ("2026-07-01T18:00:00Z", "2026-07-03T12:00:00Z", [12, 24, 6]),
    ],
)
def test_day_bins_follow_denver_days(start: str, end: str, hours: list[int]) -> None:
    bins = bin_list(bin_spec(aggregate_query(start, end, "day")))
    assert [(hi - lo).total_seconds() / 3600 for lo, hi in bins] == hours


def test_uniform_bins_align_to_the_epoch_and_clip_to_the_window() -> None:
    query = aggregate_query("2026-07-01T00:10:00Z", "2026-07-01T02:00:00Z", "3600")
    bins = bin_list(bin_spec(query))
    assert [(lo.minute, hi.hour) for lo, hi in bins] == [(10, 1), (0, 2)]


def wind_row(start: datetime, **values: float) -> dict[str, Any]:
    row: dict[str, Any] = {
        "bin_start": start,
        "support_seconds": 3600.0,
        "evidence_count": 60,
        "evidence_hash": "0" * 64,
        "calm_seconds": 0.0,
        "directional_seconds": 3600.0,
        "east": 0.0,
        "north": 0.0,
        "weight": 0.0,
    }
    row.update(values)
    return row


def summary(row: dict[str, Any], statistic: str, rose: list[dict[str, Any]] | None = None) -> Any:
    query = AggregateQuery.model_validate(
        {
            "start": "2026-07-01T00:00:00Z",
            "end": "2026-07-01T01:00:00Z",
            "metrics": ["wind_direction"],
            "statistics": [statistic],
            "resolution": "3600",
        }
    )
    (item,) = summarize(
        REGISTRY["wind_direction"], [statistic], bin_spec(query), [row], rose or []
    )
    return item


def directional_sums(*samples: tuple[float, float, float]) -> dict[str, float]:
    """(speed, degrees, seconds) -> the SQL's E/N/weight sums."""
    return {
        "east": sum(v * s * math.sin(math.radians(d)) for v, d, s in samples),
        "north": sum(v * s * math.cos(math.radians(d)) for v, d, s in samples),
        "weight": sum(v * s for v, d, s in samples),
    }


def test_wind_vector_mean_wraps_north_and_marks_calm_and_variable() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    wrap = summary(wind_row(start, **directional_sums((5, 359, 1800), (5, 1, 1800))), "vector_mean")
    assert wrap.value in (0.0, 360.0) or abs(wrap.value) < 1e-6
    assert wrap.direction_state == "directional"
    opposed = summary(
        wind_row(start, **directional_sums((5, 90, 1800), (5, 270, 1800))), "vector_mean"
    )
    assert opposed.value is None and opposed.direction_state == "variable"
    calm = summary(wind_row(start, calm_seconds=3600.0, directional_seconds=0.0), "vector_mean")
    assert calm.value is None and calm.direction_state == "calm"
    assert calm.coverage.fraction == 1
    weighted = summary(
        wind_row(start, **directional_sums((10, 0, 600), (2, 90, 3000))), "vector_mean"
    )
    assert weighted.value == round(math.degrees(math.atan2(6000, 6000)), 1)


def test_wind_rose_categories_sum_to_the_interval() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rose = [
        {"bin_start": start, "sector": 0, "speed_bin": 1, "seconds": 600.0},
        {"bin_start": start, "sector": 4, "speed_bin": 5, "seconds": 1200.0},
    ]
    item = summary(wind_row(start, calm_seconds=900.0), "rose", rose)
    bins = {(b.category, b.sector_start): b for b in item.rose}
    assert bins[("directional", 348.75)].sector_end == 11.25
    assert bins[("directional", 78.75)].speed_max is None
    assert bins[("calm", None)].duration_seconds == 900
    assert bins[("missing", None)].duration_seconds == 900
    assert sum(b.duration_seconds for b in item.rose) == 3600


def test_lightning_sums_stay_integers_and_empty_bins_have_reasons() -> None:
    query = AggregateQuery.model_validate(
        {
            "start": "2026-07-01T00:00:00Z",
            "end": "2026-07-01T02:00:00Z",
            "metrics": ["lightning_count"],
            "statistics": ["sum"],
            "resolution": "3600",
        }
    )
    row = {
        "bin_start": datetime(2026, 7, 1, tzinfo=UTC),
        "support_seconds": 1800.0,
        "total": 3.0000000001,
        "evidence_count": 30,
        "evidence_hash": "a" * 64,
    }
    first, second = summarize(REGISTRY["lightning_count"], ["sum"], bin_spec(query), [row], [])
    assert first.value == 3 and first.coverage.fraction == 0.5 and first.evidence_count == 30
    assert second.value is None and second.reason and second.evidence_hash is None


def test_source_failures_are_sanitized_503s() -> None:
    class Broken(FakeReader):
        async def observations(
            self, family: str, **kwargs: Any
        ) -> tuple[list[dict[str, Any]], datetime]:
            raise SourceUnavailable()

    client = TestClient(build_app(service(Clock(T0), Broken())))
    response = client.get(
        "/api/v1/weather/observations?start=2026-07-01T00:00:00Z&end=2026-07-01T06:00:00Z"
        "&metrics=temperature"
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "code": "unavailable",
        "message": "Weather data is temporarily unavailable.",
        "suggested_resolution_seconds": None,
    }


def test_rain_windows_come_from_the_reading_not_the_response_time() -> None:
    # 23:59:58 MDT: a full day total, then a request just after the weather day rolls over.
    at = datetime(2026, 7, 2, 5, 59, 58, tzinfo=UTC)
    held = reading(at)
    day = {"total": 4.0, "support_seconds": 600.0, "observed_at": at, "received_at": at}
    held = Reading("home", held.obs_st, None, {**held.rain, "day": day}, at)
    before = {m.metric: m for m in build_current(held, at, DEFAULT_CADENCES).metrics}
    assert before["rain_today"].value == 4
    assert before["rain_today"].coverage == pytest.approx(600 / (86400 - 2))
    after = {
        m.metric: m
        for m in build_current(held, at + timedelta(seconds=3), DEFAULT_CADENCES).metrics
    }
    assert after["rain_today"].value is None and after["rain_today"].reason
    assert after["rain_last_hour"].coverage == 1


def test_fields_flagged_invalid_are_invalid_not_missing() -> None:
    flagged = reading(relative_humidity=None)
    assert flagged.obs_st is not None
    flagged = Reading(
        "home", {**flagged.obs_st, "invalid_fields": ["relative_humidity"]}, None, {}, T0
    )
    humidity = next(
        m for m in build_current(flagged, T0, DEFAULT_CADENCES).metrics if m.metric == "humidity"
    )
    assert humidity.value is None and humidity.quality == "invalid" and humidity.reason
    reader = FakeReader()
    reader.rows = [
        {**st_row(T0 - timedelta(minutes=2)), "invalid_fields": ["relative_humidity"]},
        st_row(T0 - timedelta(minutes=1)),
        {**st_row(T0 - timedelta(seconds=30)), "valid": False},
    ]
    query = ObservationQuery.model_validate(
        {"start": "2026-07-01T17:00:00Z", "end": "2026-07-01T18:00:00Z", "metrics": ["humidity"]}
    )
    page = run(service(Clock(T0), reader).observations(query))
    assert [p.quality for p in page.points] == ["invalid", "missing", "invalid"]


def test_wide_or_edge_windows_are_400s_before_any_bins_are_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weather.api.service as module

    def no_days(*args: Any) -> Any:
        raise AssertionError("bins were built")

    monkeypatch.setattr(module, "weather_day", no_days)
    client = TestClient(build_app(service(Clock(T0), FakeReader())))
    base = "/api/v1/weather/aggregates?metrics=temperature&statistics=mean&resolution=day"
    for window, code in (
        ("start=2000-01-01T00:00:00Z&end=2099-12-31T00:00:00Z", "range_too_large"),
        ("start=0001-01-01T00:00:00Z&end=0001-01-02T00:00:00Z", "invalid_request"),
        ("start=9999-12-30T00:00:00Z&end=9999-12-31T23:59:59Z", "invalid_request"),
    ):
        response = client.get(base + "&" + window)
        assert response.status_code == 400, window
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == code


def test_web_fixtures_match_the_server_models() -> None:
    from weather.api.models import Aggregates, Current, Observations
    from weather.api.service import metric_registry

    fixtures = ROOT / "web/app/fixtures"
    for name, model in (
        ("current", Current),
        ("aggregates", Aggregates),
        ("observations", Observations),
    ):
        model.model_validate_json((fixtures / f"{name}.json").read_text())
    registry = (fixtures / "metric-registry.json").read_text()
    assert metric_registry(DEFAULT_CADENCES).model_dump(mode="json") == json.loads(registry)
