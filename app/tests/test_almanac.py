# SPDX-License-Identifier: MIT
"""Synthetic almanac vectors over the real WX-13 boundary, without DB/network."""

import json
import socket
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from weather.api.aggregate_adapter import compute
from weather.api.public_models import Interval as PublicInterval
from weather.products.aggregate import Interval, Support
from weather.products.almanac import (
    FORMULA_REVISION,
    compare_periods,
    period_interval,
    rain_calendar,
    rank_records,
    wind_rose_bins,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/contracts/almanac-vectors.json").read_text()
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Pure almanac computation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def day_computation(day, value, fraction="1", *, rain=False, revision="snapshot_1"):
    public_window = period_interval(day, "day")
    window = Interval(public_window.start, public_window.end)
    seconds = int(window.seconds * Fraction(fraction))
    support = Support(
        Interval(window.start, window.start + timedelta(seconds=seconds)),
        value,
        "synthetic_" + day.isoformat().replace("-", ""),
    )
    metric, statistic = (
        ("precipitation", "observed_accumulation") if rain else ("temperature", "max")
    )
    return compute(window, metric, statistic, {metric: [support]}, evidence_revision=revision)


@pytest.mark.parametrize("case", VECTORS["calendar_days"])
def test_dst_observed_totals_and_denominators(case):
    day = date.fromisoformat(case["date"])
    evidence = day_computation(day, 3, "0.5", rain=True)
    row = rain_calendar([evidence])[0]
    assert row.aggregate.value == 3  # Never extrapolate a half-day into six mm.
    assert row.aggregate.coverage.expected_seconds == case["seconds"]
    assert row.aggregate.coverage.support_seconds == case["seconds"] / 2
    assert row.aggregate.coverage.fraction == 0.5
    assert not row.complete and "partial_weather_day" in row.missing_reasons
    window = Interval(evidence.aggregate.interval.start, evidence.aggregate.interval.end)
    wind = compute(window, "wind_direction", "vector_mean", {}, evidence_revision="snapshot_1")
    bins = wind_rose_bins(wind)
    assert sum(item.duration_seconds for item in bins) == case["seconds"]
    assert all(item.denominator_seconds == case["seconds"] for item in bins)
    assert (
        next(item for item in bins if item.category == "missing").duration_seconds
        == case["seconds"]
    )


def test_records_exclude_thin_extreme_but_preserve_browseable_evidence_and_ties():
    rows = [
        day_computation(date.fromisoformat(c["date"]), c["maximum"], c["coverage"])
        for c in VECTORS["records"]
    ]
    history = PublicInterval(
        start=rows[0].aggregate.interval.start, end=rows[-1].aggregate.interval.end
    )
    result = rank_records([*rows, rows[0]], history_interval=history)
    assert [row.rank for row in result.observations] == [c["rank"] for c in VECTORS["records"]]
    assert result.observations[0].aggregate.value == 99
    assert not result.observations[0].ranking_eligible
    assert result.observations[1].aggregate.coverage.fraction == 0.95
    assert result.history_interval == history
    assert result.label == "observed extremes within station history since 2026-01-01"
    assert result.formula_revision == VECTORS["formula_revision"] == FORMULA_REVISION
    assert "normal" not in result.label


def test_first_partial_station_day_and_no_history_are_honest():
    window = period_interval(date(2026, 1, 1), "day")
    history = PublicInterval(start=window.start + timedelta(hours=12), end=window.end)
    evidence = compute(
        Interval(window.start, window.end),
        "temperature",
        "max",
        {"temperature": [Support(Interval(history.start, history.end), 10, "first_observation")]},
        evidence_revision="snapshot_1",
    )
    assert rank_records([evidence], history_interval=history).observations[0].rank is None
    empty = rank_records([], history_interval=None)
    assert empty.history_interval is None and not empty.observations
    assert empty.label == "no station history available"
    with pytest.raises(ValueError):
        rank_records([evidence], history_interval=None)


def test_correction_changes_product_evidence_without_mutating_previous_result():
    day = date(2026, 1, 1)
    original = day_computation(day, 3, rain=True)
    corrected = day_computation(day, 4, rain=True, revision="snapshot_2")
    old = rain_calendar([original])[0]
    new = rain_calendar([corrected])[0]
    assert old.aggregate.value == 3 and new.aggregate.value == 4
    assert old.aggregate_id != new.aggregate_id
    assert old.aggregate.evidence_revision == "snapshot_1"
    assert new.aggregate.evidence_revision == "snapshot_2"
    with pytest.raises(ValueError, match="One effective"):
        rain_calendar([original, corrected])
    # Durable old-revision retention/outbox execution is a later DB acceptance test.


def test_unknown_rain_is_excluded_with_reason_not_zero():
    interval = period_interval(date(2026, 1, 1), "day")
    result = compute(
        Interval(interval.start, interval.end),
        "precipitation",
        "observed_accumulation",
        {"precipitation": [Support(None, 99, "unknown_support")]},
        evidence_revision="snapshot_1",
    )
    row = rain_calendar([result])[0]
    assert row.aggregate.value is None
    assert "unknown_or_missing_accumulation_support" in row.missing_reasons


@pytest.mark.parametrize("case", VECTORS["directional_edges"])
def test_wind_projection_preserves_shared_sector_and_speed_edges(case):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = Interval(start, start + timedelta(seconds=60))
    speed = Support(window, case["speed"], "speed")
    direction = Support(window, case["direction"], "direction")
    result = compute(
        window,
        "wind_direction",
        "vector_mean",
        {"wind_avg": [speed, speed], "wind_direction": [direction, direction]},
        evidence_revision="snapshot_1",
    )
    bins = wind_rose_bins(result)
    occupied = [row for row in bins if row.category == "directional" and row.duration_seconds]
    assert len(occupied) == 1
    row = occupied[0]
    assert (row.angle_start, row.angle_end, row.speed_min, row.speed_max) == (
        case["angle_start"],
        case["angle_end"],
        case["speed_min"],
        case["speed_max"],
    )
    assert row.duration_seconds == row.denominator_seconds == 60


def test_calm_variable_missing_and_directional_partition_full_interval():
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def piece(a, b):
        return Interval(start + timedelta(seconds=a), start + timedelta(seconds=b))

    result = compute(
        piece(0, 60),
        "wind_direction",
        "vector_mean",
        {
            "wind_avg": [Support(piece(0, 10), 0.49, "calm"), Support(piece(10, 50), 2, "speed")],
            "wind_direction": [
                Support(piece(10, 20), None, "variable", quality="variable"),
                Support(piece(20, 50), 0, "north"),
            ],
        },
        evidence_revision="snapshot_1",
    )
    bins = wind_rose_bins(result)
    totals = {
        category: sum(row.duration_seconds for row in bins if row.category == category)
        for category in ("calm", "variable", "directional", "missing")
    }
    assert totals == {"calm": 10, "variable": 10, "directional": 30, "missing": 10}
    assert sum(totals.values()) == 60
    assert all(row.denominator_seconds == 60 for row in bins)


def test_comparison_preserves_unequal_durations_and_missing_days():
    spring = day_computation(date(2026, 3, 8), 3, rain=True)
    ordinary = day_computation(date(2026, 3, 9), 6, rain=True)
    result = compare_periods(spring, ordinary)
    assert result.first.duration_seconds == 23 * 3600
    assert result.second.duration_seconds == 24 * 3600
    assert result.first.aggregate.value == 3 and result.second.aggregate.value == 6
    assert result.difference == 3 and result.reason is None
    thin = day_computation(date(2026, 3, 9), 6, "0.9", rain=True)
    result = compare_periods(spring, thin)
    assert result.difference is None and not result.second.adequate
    assert result.second.missing_days == (date(2026, 3, 9),)
    with pytest.raises(ValueError, match="same metric"):
        compare_periods(spring, day_computation(date(2026, 3, 9), 6))


def test_month_and_year_use_shared_calendar_and_no_private_fields():
    assert period_interval(date(2026, 3, 1), "month").seconds == (31 * 24 - 1) * 3600
    assert period_interval(date(2026, 11, 1), "month").seconds == (30 * 24 + 1) * 3600
    assert period_interval(date(2026, 1, 1), "year").seconds == 365 * 24 * 3600
    with pytest.raises(ValueError):
        period_interval(date(2026, 3, 2), "month")
    row = day_computation(date(2026, 1, 1), 1)
    payload = rank_records([row], history_interval=row.aggregate.interval).model_dump_json()
    assert not any(word in payload for word in ("latitude", "longitude", "serial", "device_id"))


def test_distinct_exact_extremes_do_not_become_a_false_float_tie():
    first = day_computation(date(2026, 1, 1), Fraction(10**20 + 1, 10**20))
    second = day_computation(date(2026, 1, 2), Fraction(10**20 + 2, 10**20))
    assert first.aggregate.value == second.aggregate.value
    assert first.exact.value < second.exact.value
    history = PublicInterval(
        start=first.aggregate.interval.start, end=second.aggregate.interval.end
    )
    assert [
        r.rank for r in rank_records([first, second], history_interval=history).observations
    ] == [2, 1]
    assert compare_periods(first, second).difference == float(Fraction(1, 10**20))
