# SPDX-License-Identifier: MIT
"""Local almanac (#42) over #14's on-read aggregates, without a database or network."""

import asyncio
import json
import socket
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from weather.api import build_app
from weather.api.cursors import CursorCodec
from weather.api.models import AlmanacQuery, AstronomyItem, RecordItem
from weather.api.registry import PublicError
from weather.api.service import WeatherService
from weather.api.source import BinSpec
from weather.products.almanac import FORMULA_REVISION, LIBRARY_REVISION, moon_phase_name
from weather.timemodel import SiteConfig, solar_cycle, weather_day

NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)
HISTORY = datetime(2026, 1, 1, 7, 30, tzinfo=UTC)  # 00:30 MST on 2026-01-01
# A synthetic fixture site, not the station's position.
SITE = SiteConfig(latitude=12.5, longitude=-100.25)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Computation must never reach out: no connect and no name resolution."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("almanac computation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def day(value: str) -> tuple[datetime, datetime]:
    local = weather_day(date.fromisoformat(value))
    return local.start, local.end


def utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def row(start: datetime, *, support: float, digest: str = "a", **values: float | None) -> dict:
    base: dict[str, Any] = {
        "bin_start": start,
        "support_seconds": support,
        "mean": None,
        "minimum": None,
        "maximum": None,
        "total": None,
        "evidence_count": 1 if support else 0,
        "evidence_hash": digest * 64 if support else None,
        "calm_seconds": None,
        "directional_seconds": None,
        "east": None,
        "north": None,
        "weight": None,
    }
    base.update(values)
    return base


class Reader:
    """Rows keyed by bin start, as the shared SQL returns them; records every bin request."""

    def __init__(self, history: datetime | None = HISTORY) -> None:
        self.history = history
        self.rows: dict[datetime, dict[str, Any]] = {}
        self.rose: list[dict[str, Any]] = []
        self.bins: list[BinSpec] = []

    async def history_start(self, now: datetime) -> datetime | None:
        return self.history

    async def imported(self) -> None:
        return None

    async def aggregates(
        self, source: Any, bins: BinSpec, *, cap: timedelta, wind: bool, rose: bool
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.bins.append(bins)
        starts = set(bins.bounds[:-1])
        rows = [r for start, r in sorted(self.rows.items()) if start in starts]
        return rows, [r for r in self.rose if r["bin_start"] in starts] if rose else []


def weather(reader: Reader, site: SiteConfig | None = SITE) -> WeatherService:
    return WeatherService(
        reader,  # type: ignore[arg-type]
        CursorCodec(b"k" * 64),
        registry=CollectorRegistry(),
        clock=lambda: NOW,
        site=site,
    )


def read(reader: Reader, site: SiteConfig | None = SITE, **query: Any) -> Any:
    return asyncio.run(weather(reader, site).almanac(AlmanacQuery.model_validate(query)))


@pytest.mark.parametrize(
    ("local", "seconds"), [("2026-03-08", 23 * 3600), ("2026-11-01", 25 * 3600)]
)
def test_dst_days_use_real_duration_for_rain_and_wind_rose(local: str, seconds: int) -> None:
    start, end = day(local)
    reader = Reader()
    reader.rows[start] = row(start, support=seconds / 2, total=3.0, calm_seconds=600.0)
    rain = read(reader, product="rain_day", start=utc(start), end=utc(end))
    (item,) = rain.items
    assert item.value == 3  # half a day of reports is never extrapolated to a full day
    assert item.coverage.expected_seconds == seconds
    assert item.coverage.fraction == 0.5 and not item.complete
    assert item.missing_reasons == ["partial_coverage"]
    reader.rose = [{"bin_start": start, "sector": 0, "speed_bin": 1, "seconds": 1200.0}]
    rose = read(reader, product="wind_rose_bin", start=utc(start), end=utc(end))
    assert {b.denominator_seconds for b in rose.items} == {seconds}
    assert sum(b.duration_seconds for b in rose.items) == pytest.approx(seconds)
    missing = next(b for b in rose.items if b.category == "missing")
    assert missing.duration_seconds == pytest.approx(seconds - 1800)
    # The rose is one bin over the whole interval, so the denominator is its elapsed time.
    assert reader.bins[-1].bounds == [start, end]


def test_wind_rose_is_the_full_structured_grid_and_never_renormalized() -> None:
    start, end = day("2026-07-01")
    reader = Reader()
    reader.rows[start] = row(start, support=3000.0, calm_seconds=600.0)
    reader.rose = [
        {"bin_start": start, "sector": 0, "speed_bin": 1, "seconds": 900.0},  # N, [0.5, 2)
        {"bin_start": start, "sector": 1, "speed_bin": 5, "seconds": 1500.0},  # NNE, [20, inf)
    ]
    result = read(reader, product="wind_rose_bin", start=utc(start), end=utc(end))
    assert len(result.items) == 16 * 5 + 3
    cells = {(b.category, b.sector_start, b.speed_min): b for b in result.items}
    north = cells[("directional", 348.75, 0.5)]
    assert (north.sector_end, north.speed_max, north.duration_seconds) == (11.25, 2.0, 900)
    fast = cells[("directional", 11.25, 20.0)]
    assert fast.speed_max is None and fast.duration_seconds == 1500
    assert cells[("calm", None, None)].duration_seconds == 600
    assert cells[("variable", None, None)].duration_seconds == 0
    assert cells[("missing", None, None)].duration_seconds == 86400 - 3000
    assert sum(b.duration_seconds for b in result.items) == 86400
    assert result.coverage.fraction == round(3000 / 86400, 6)
    assert result.metric == "wind_direction" and result.unit == "m/s"


def record_reader(coverages: dict[str, tuple[float, float]]) -> Reader:
    reader = Reader()
    for local, (fraction, value) in coverages.items():
        start, end = day(local)
        seconds = (end - start).total_seconds()
        reader.rows[start] = row(start, support=seconds * fraction, maximum=value, minimum=value)
    return reader


def test_records_rank_only_qualifying_days_and_follow_corrections() -> None:
    reader = record_reader(
        {
            "2026-01-01": (0.90, 99.0),  # the highest value, but on a 90%-coverage day
            "2026-01-02": (0.95, 30.0),
            "2026-01-03": (1.00, 29.0),
            "2026-01-04": (1.00, 30.0),
        }
    )
    start, _ = day("2026-01-01")
    _, end = day("2026-01-04")
    query = {
        "product": "record",
        "metric": "temperature",
        "statistic": "max",
        "start": utc(start),
        "end": utc(end),
    }
    result = read(reader, **query)
    items: list[RecordItem] = result.items
    assert [i.rank for i in items] == [None, 1, 3, 1]
    thin = items[0]
    assert thin.value == 99 and not thin.ranking_eligible
    assert "below_record_coverage" in thin.missing_reasons
    top = items[1]
    assert top.coverage.fraction == 0.95 and top.ranking_eligible
    assert result.history_interval.start == HISTORY and result.history_interval.end == NOW
    assert result.label == "Observed extremes within station history since 2026-01-01"
    assert "normal" not in result.label.lower()
    assert result.formula_revision == FORMULA_REVISION and result.library_revision
    first_hash = result.evidence_hash
    # A corrected row changes the next read: nothing is stored between reads (C5).
    corrected = reader.rows[day("2026-01-03")[0]]
    corrected.update(maximum=31.0, evidence_hash="b" * 64)
    again = read(reader, **query)
    assert [i.rank for i in again.items] == [None, 2, 1, 2]
    assert again.evidence_hash != first_hash
    lows = read(reader, **{**query, "statistic": "min"})
    assert [i.rank for i in lows.items] == [None, 2, 1, 2]


def test_no_history_is_honest() -> None:
    start, end = day("2026-01-01")
    result = read(
        Reader(history=None),
        product="record",
        metric="temperature",
        statistic="max",
        start=utc(start),
        end=utc(end),
    )
    assert result.history_interval is None
    assert result.label.startswith("No station history yet")
    assert result.items[0].rank is None and result.evidence_hash is None
    assert result.items[0].missing_reasons == ["before_history", "no_observations"]
    assert result.missing_reasons == ["no_observations"]


def test_comparisons_keep_actual_durations_and_missing_days() -> None:
    reader = Reader()
    spring, spring_end = day("2026-03-08")
    ordinary, ordinary_end = day("2026-03-09")
    reader.rows[spring] = row(spring, support=82800.0, total=3.0)
    reader.rows[ordinary] = row(ordinary, support=86400.0, total=6.0)
    query = {
        "product": "comparison",
        "metric": "precipitation",
        "statistic": "sum",
        "start": utc(spring),
        "end": utc(spring_end),
        "comparison_start": utc(ordinary),
        "comparison_end": utc(ordinary_end),
    }
    (item,) = read(reader, **query).items
    assert item.first.duration_seconds == 23 * 3600 and item.second.duration_seconds == 86400
    assert item.difference == 3 and item.reason is None
    reader.rows[ordinary] = row(ordinary, support=86400.0 * 0.9, total=6.0)
    (thin,) = read(reader, **query).items
    assert thin.difference is None and thin.reason == "inadequate_coverage"
    assert not thin.second.adequate and thin.second.value == 6
    assert thin.second.partial_days == [date(2026, 3, 9)] and thin.second.missing_days == []
    del reader.rows[ordinary]
    (empty,) = read(reader, **query).items
    assert empty.second.missing_days == [date(2026, 3, 9)]
    assert empty.second.missing_reasons == ["no_observations"]


def test_astronomy_matches_the_shared_wrapper_and_never_exposes_the_site() -> None:
    start, _ = day("2026-06-20")
    _, end = day("2026-06-22")
    result = read(Reader(), product="astronomy", start=utc(start), end=utc(end))
    assert result.derived and result.metric is None and len(result.items) == 3
    items: list[AstronomyItem] = result.items
    for item in items:
        cycle = solar_cycle(item.local_date, SITE)
        assert (item.civil_dawn, item.sunrise, item.sunset, item.civil_dusk) == (
            cycle.dawn,
            cycle.sunrise,
            cycle.sunset,
            cycle.dusk,
        )
        assert item.sunrise and item.solar_noon and item.sunset
        assert item.sunrise < item.solar_noon < item.sunset
        assert item.day_length_seconds == (cycle.sunset - cycle.sunrise).total_seconds()
        assert item.moon_phase is not None and 0 <= item.moon_phase < 28
    payload = json.dumps(result.model_dump(mode="json"))
    for private in ("latitude", "longitude", "12.5", "100.25", SITE.config_hash):
        assert private not in payload
    unconfigured = read(Reader(), site=None, product="astronomy", start=utc(start), end=utc(end))
    assert unconfigured.missing_reasons == ["site_not_configured"]
    assert unconfigured.items[0].sunrise is None
    assert unconfigured.items[0].moon_phase == items[0].moon_phase
    assert "astral" in LIBRARY_REVISION and "tzdata" in LIBRARY_REVISION


def test_moon_phase_names_cover_the_cycle() -> None:
    assert [moon_phase_name(p) for p in (0, 1.7, 1.8, 7, 14, 21, 26.2, 27.9)] == [
        "new",
        "new",
        "waxing_crescent",
        "first_quarter",
        "full",
        "last_quarter",
        "waning_crescent",
        "new",
    ]


def test_pages_use_the_signed_cursor_and_bind_the_filters() -> None:
    start, _ = day("2026-01-01")
    _, end = day("2026-01-05")
    reader = Reader()
    query = {"product": "rain_day", "start": utc(start), "end": utc(end), "limit": 2}
    service = weather(reader)
    first = asyncio.run(service.almanac(AlmanacQuery.model_validate(query)))
    dates = [i.local_date for i in first.items]
    cursor = first.next_cursor
    while cursor:
        page = asyncio.run(
            service.almanac(AlmanacQuery.model_validate({**query, "cursor": cursor}))
        )
        dates += [i.local_date for i in page.items]
        cursor = page.next_cursor
    assert dates == [date(2026, 1, n) for n in range(1, 6)]
    assert first.next_cursor
    for altered in ({**query, "limit": 3}, {**query, "product": "astronomy"}):
        with pytest.raises(PublicError) as error:
            asyncio.run(
                service.almanac(
                    AlmanacQuery.model_validate({**altered, "cursor": first.next_cursor})
                )
            )
        assert error.value.code == "invalid_cursor"


def test_invalid_almanac_requests_are_400s() -> None:
    client = TestClient(build_app(weather(Reader())))
    start, end = day("2026-01-01")
    window = f"start={utc(start)}&end={utc(end)}"
    other = f"comparison_start={utc(end)}&comparison_end={utc(end + timedelta(days=1))}"
    cases = {
        # comparison bounds on a non-comparison product
        f"product=rain_day&{window}&{other}": "invalid_request",
        # a missing comparison bound
        f"product=comparison&metric=temperature&statistic=max&{window}"
        f"&comparison_start={utc(end)}": "invalid_request",
        # non-half-open and non-UTC intervals
        f"product=rain_day&start={utc(start)}&end={utc(start)}": "invalid_request",
        f"product=rain_day&start={utc(end)}&end={utc(start)}": "invalid_request",
        "product=rain_day&start=2026-01-01T00:00:00-07:00&end=2026-01-02T00:00:00-07:00": (
            "invalid_request"
        ),
        f"product=comparison&metric=temperature&statistic=max&{window}"
        f"&comparison_start={utc(end)}&comparison_end={utc(end)}": "invalid_request",
        # not on local midnight, too long, or a product with a fixed metric
        "product=rain_day&start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z": "invalid_request",
        "product=rain_day&start=2025-01-01T07:00:00Z&end=2026-01-03T07:00:00Z": (
            "range_too_large"
        ),
        f"product=rain_day&metric=temperature&statistic=max&{window}": "invalid_request",
        f"product=record&{window}": "invalid_request",
        f"product=record&metric=temperature&statistic=mean&{window}": "unsupported_statistic",
        f"product=record&metric=wind_speed&statistic=max&{window}": "unsupported_statistic",
        f"product=normals&{window}": "invalid_request",
    }
    for parameters, code in cases.items():
        response = client.get("/api/v1/weather/almanac?" + parameters)
        assert response.status_code == 400, parameters
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == code, parameters
    ok = client.get(f"/api/v1/weather/almanac?product=rain_day&{window}")
    assert ok.status_code == 200 and ok.headers["cache-control"] == "public, max-age=60"
    body = ok.json()
    assert body["timezone"] == "America/Denver" and body["interval"]["start"] == utc(start)
    assert body["items"][0]["product"] == "rain_day"


def test_web_almanac_fixtures_match_the_server_models() -> None:
    from pathlib import Path

    from weather.api.models import Almanac

    fixtures = Path(__file__).resolve().parents[2] / "web/app/fixtures"
    for path in sorted(fixtures.glob("almanac-*.json")):
        Almanac.model_validate_json(path.read_text())
    assert len(list(fixtures.glob("almanac-*.json"))) >= 2
