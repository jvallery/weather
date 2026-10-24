# SPDX-License-Identifier: MIT
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from weather.products.aggregate import (
    Interval,
    Observation,
    Support,
    coverage,
    forward_holds,
    freshness,
    lightning,
    precipitation,
    pressure_trend,
    scalar,
    wind,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
VECTORS = {
    row["id"]: row
    for row in json.loads((ROOT / "docs/contracts/telemetry-vectors.json").read_text())["vectors"]
}


def span(a: int | float, b: int | float) -> Interval:
    return Interval(BASE + timedelta(seconds=a), BASE + timedelta(seconds=b))


def measure(
    value: int | float | Decimal | Fraction | None, a: int, b: int, identity: str, **kwargs: object
) -> Support:
    return Support(span(a, b), value, identity, **kwargs)  # type: ignore[arg-type]


def mean(samples: list[Support], window: Interval | None = None):
    return scalar(
        window or span(0, 60),
        samples,
        metric="temperature",
        unit="degC",
        statistic="mean",
        evidence_revision="r1",
    )


def test_t12_union_does_not_double_count_overlap() -> None:
    vector = VECTORS["T12"]
    result = coverage(
        span(*vector["input"]["window_seconds"]),
        [span(*bounds) for bounds in vector["input"]["valid_support_seconds"]],
    )
    assert result.support_seconds == vector["expect"]["covered_seconds"]
    assert result.fraction == Fraction(vector["expect"]["coverage_fraction"])
    summary = mean([measure(10, 0, 30, "a"), measure(10, 20, 40, "b")])
    assert summary.value == 10
    assert summary.coverage == result


def test_competing_values_are_excluded_without_averaging_conflicting_time() -> None:
    result = mean([measure(10, 0, 30, "a"), measure(20, 20, 40, "b")])
    assert result.coverage.support_seconds == 30
    assert result.value == Fraction(40, 3)
    assert result.coverage.gaps == (span(20, 30), span(40, 60))


def test_per_metric_hold_cap_and_known_invalid_sample_stop_support() -> None:
    rows = [
        Observation(BASE, 10, "a"),
        Observation(BASE + timedelta(seconds=20), None, "bad", False),
        Observation(BASE + timedelta(seconds=50), 20, "b"),
    ]
    result = mean(list(forward_holds(rows, cap_seconds=30)))
    assert result.coverage.support_seconds == 30
    assert result.coverage.gaps == (span(20, 50),)
    assert result.value == Fraction(40, 3)
    short = mean(list(forward_holds([rows[0]], cap_seconds=10)))
    long = mean(list(forward_holds([rows[0]], cap_seconds=20)))
    assert short.algorithm_revision != long.algorithm_revision


def test_missing_and_measured_zero_are_different() -> None:
    absent = mean([])
    zero = mean([measure(0, 0, 60, "zero")])
    assert absent.value is None and absent.reason == "no_qualified_support"
    assert zero.value == 0 and zero.reason is None and zero.coverage.fraction == 1


def test_exact_decimal_mean_and_duplicate_evidence() -> None:
    a = measure(Decimal("0.1"), 0, 30, "a")
    result = mean([a, a, measure(Decimal("0.2"), 30, 60, "b")])
    assert result.value == Fraction(3, 20)
    assert result.coverage.support_seconds == 60
    with pytest.raises(ValueError, match="identity conflict"):
        mean([a, measure(100, 0, 30, "a")])


def test_t13_rain_is_conserved_and_estimate_is_explicit() -> None:
    vector = VECTORS["T13"]
    data = vector["input"]
    rain = measure(data["amount_mm"], *data["qualified_support_seconds"], "rain")
    summaries = [
        precipitation(span(*bounds), [rain, rain], evidence_revision="r1")
        for bounds in data["windows_seconds"]
    ]
    assert [item.value for item in summaries] == [
        Fraction(str(v)) for v in vector["expect"]["allocated_mm"]
    ]
    assert sum(item.value for item in summaries) == vector["expect"]["total_mm"]
    assert all(item.estimate == vector["expect"]["estimate_label"] for item in summaries)


def test_t14_report_interval_cannot_invent_precipitation_support() -> None:
    data = VECTORS["T14"]["input"]
    unknown = precipitation(
        span(0, 300),
        [Support(None, data["rain_previous_minute_mm"], "rain")],
        evidence_revision="r1",
    )
    qualified = precipitation(span(0, 300), [measure(1, 0, 60, "rain")], evidence_revision="r2")
    assert unknown.value is None and unknown.coverage.fraction == 0
    assert qualified.value == 1 and qualified.coverage.support_seconds == 60
    with pytest.raises(ValueError, match="reconciliation"):
        precipitation(
            span(0, 60), [measure(1, 0, 60, "a"), measure(1, 0, 60, "b")], evidence_revision="r1"
        )


def test_t10_event_packets_do_not_replace_interval_count_authority() -> None:
    result = lightning(
        span(0, 60),
        [Support(None, 1, "event", authority="evt_strike")],
        authority="obs_st",
        evidence_revision="r1",
    )
    assert result.value is None and result.reason == "no_interval_authority"
    with pytest.raises(ValueError, match="interval-count authority"):
        lightning(span(0, 60), [], authority="evt_strike", evidence_revision="r1")


def test_t15_boundary_counts_never_become_fractional_or_complete_zeroes() -> None:
    vector = VECTORS["T15"]
    source = measure(
        vector["input"]["count"], *vector["input"]["support_seconds"], "count", authority="obs_st"
    )
    for bounds in vector["input"]["windows_seconds"]:
        result = lightning(span(*bounds), [source], authority="obs_st", evidence_revision="r1")
        assert result.observed_subtotal == vector["expect"]["qualified_count_in_either_window"]
        assert result.boundary_uncertain_count == vector["expect"]["boundary_uncertain_count"]
        assert result.value is None and result.coverage.fraction == 0
    whole = lightning(span(0, 60), [source], authority="obs_st", evidence_revision="r1")
    assert whole.value == 3 and whole.coverage.fraction == 1
    with pytest.raises(ValueError, match="integers"):
        lightning(
            span(0, 60),
            [measure(Fraction(1, 2), 0, 60, "bad", authority="obs_st")],
            authority="obs_st",
            evidence_revision="r1",
        )


def test_t11_replay_does_not_extend_source_expiry() -> None:
    data = VECTORS["T11"]["input"]
    source = datetime.fromtimestamp(data["source_epoch"], UTC)
    expires = source + timedelta(seconds=data["freshness_seconds"])
    assert int(expires.timestamp()) == VECTORS["T11"]["expect"]["expiry_epoch"]
    assert (
        freshness(source, expires, now=datetime.fromtimestamp(data["replay_epoch"], UTC)) == "stale"
    )
    assert freshness(source, expires, now=source) == "fresh"
    assert freshness(source, expires, now=expires) == "stale"
    assert freshness(source, expires, now=source - timedelta(seconds=1)) == "unavailable"


def test_revision_changes_identity_and_input_order_does_not() -> None:
    rows = [measure(10, 0, 30, "a"), measure(20, 30, 60, "b")]
    first = mean(rows)
    assert first.aggregate_id == mean(list(reversed(rows))).aggregate_id
    corrected = scalar(
        span(0, 60),
        [measure(11, 0, 30, "a"), rows[1]],
        metric="temperature",
        unit="degC",
        statistic="mean",
        evidence_revision="r8",
    )
    assert corrected.aggregate_id != first.aggregate_id
    assert corrected.evidence_revision == "r8"
    assert first.evidence_revision == "r1"
    assert first.algorithm_revision == corrected.algorithm_revision


def test_solar_energy_never_extrapolates_missing_time() -> None:
    result = scalar(
        span(0, 3600),
        [measure(1000, 0, 1800, "solar")],
        metric="solar_irradiance",
        unit="W/m2",
        statistic="solar_energy",
        evidence_revision="r1",
    )
    assert (
        result.value == 500
        and result.unit == "Wh/m2"
        and result.coverage.fraction == Fraction(1, 2)
    )


@pytest.mark.parametrize(
    "day",
    json.loads((ROOT / "docs/contracts/time-model-vectors.json").read_text())[
        "civil_calendar_days"
    ],
)
def test_calendar_days_use_actual_elapsed_golden_hours(day: dict[str, object]) -> None:
    slots = day["slots"]
    start = datetime.fromisoformat(slots[0]["start"])
    expected_hours = day["slot_count"]
    window = Interval(start, start + timedelta(hours=expected_hours))
    result = mean([Support(window, 10, "daily")], window)
    assert result.coverage.expected_seconds == expected_hours * 3600
    assert result.coverage.fraction == 1


def test_wind_wrap_and_opposition_do_not_average_angles() -> None:
    speeds = [measure(1, 0, 60, "speed")]
    north = wind(
        span(0, 60),
        speeds,
        [measure(359, 0, 30, "a"), measure(1, 30, 60, "b")],
        evidence_revision="r1",
    )
    assert north.direction_degrees == pytest.approx(0)
    assert north.direction_coverage.fraction == 1
    opposite = wind(
        span(0, 60),
        speeds,
        [measure(0, 0, 30, "a"), measure(180, 30, 60, "b")],
        evidence_revision="r1",
    )
    assert opposite.direction_degrees is None and opposite.direction_state == "variable"
    assert sum(b.duration_seconds for b in opposite.rose if b.category == "directional") == 60
    assert sum(b.duration_seconds for b in opposite.rose if b.category == "variable") == 0


def test_wind_uses_speed_duration_and_reported_gust() -> None:
    result = wind(
        span(0, 60),
        [measure(1, 0, 10, "a"), measure(3, 10, 60, "b")],
        [measure(0, 0, 10, "a"), measure(90, 10, 60, "b")],
        [measure(12, 0, 60, "gust")],
        evidence_revision="r1",
    )
    assert result.mean_speed == Fraction(8, 3) and result.maximum_gust == 12
    assert result.direction_degrees == pytest.approx(math.degrees(math.atan2(150, 10)))


def test_rose_partitions_calm_directional_variable_missing_against_full_window() -> None:
    result = wind(
        span(0, 60),
        [measure(0.2, 0, 15, "calm"), measure(2, 15, 60, "speed")],
        [measure(90, 15, 30, "angle"), measure(None, 30, 45, "variable", quality="variable")],
        evidence_revision="r1",
    )
    assert result.speed_coverage.fraction == 1
    assert result.direction_coverage.fraction == Fraction(1, 4)
    for category in ("calm", "directional", "variable", "missing"):
        assert sum(b.duration_seconds for b in result.rose if b.category == category) == 15
    assert sum(b.fraction for b in result.rose) == 1


@pytest.mark.parametrize(
    "angle,speed,sector,bin_index",
    [(0, 0.5, 0, 0), (11.25, 2, 1, 1), (348.75, 5, 0, 2), (360, 20, 0, 4), (348.7499, 10, 15, 3)],
)
def test_rose_half_open_sector_and_speed_boundaries(
    angle: float, speed: float, sector: int, bin_index: int
) -> None:
    result = wind(
        span(0, 60),
        [measure(speed, 0, 60, "speed")],
        [measure(angle, 0, 60, "direction")],
        evidence_revision="r1",
    )
    chosen = result.rose[sector * 5 + bin_index]
    assert chosen.duration_seconds == 60
    assert chosen.denominator_seconds == 60
    if sector == 0:
        assert chosen.angle_start == Fraction(1395, 4) and chosen.angle_end == Fraction(45, 4)
    if bin_index == 4:
        assert chosen.speed_max is None


def test_calm_does_not_need_direction_but_unknown_direction_is_not_variable() -> None:
    calm = wind(span(0, 60), [measure(0.1, 0, 60, "calm")], [], evidence_revision="r1")
    unknown = wind(span(0, 60), [measure(1, 0, 60, "speed")], [], evidence_revision="r1")
    assert calm.direction_state == "calm"
    assert sum(b.duration_seconds for b in unknown.rose if b.category == "missing") == 60
    assert sum(b.duration_seconds for b in unknown.rose if b.category == "variable") == 0


def test_pressure_uses_real_supported_endpoint_midpoints_and_station_units() -> None:
    samples = [measure(1000, 0, 60, "early"), measure(1002, 540, 600, "late")]
    result = pressure_trend(
        span(0, 600),
        samples,
        endpoint_seconds=120,
        minimum_endpoint_fraction=0.5,
        evidence_revision="r1",
    )
    assert result.midpoint_separation_seconds == 540
    assert result.aggregate.value == Fraction(40, 3) and result.aggregate.unit == "hPa/h"
    assert result.first_coverage.fraction == result.last_coverage.fraction == Fraction(1, 2)
    insufficient = pressure_trend(
        span(0, 600),
        samples,
        endpoint_seconds=120,
        minimum_endpoint_fraction=0.75,
        evidence_revision="r1",
    )
    assert insufficient.aggregate.value is None
    assert insufficient.aggregate.reason == "insufficient_endpoint_support"
    assert insufficient.aggregate.algorithm_revision != result.aggregate.algorithm_revision
