# SPDX-License-Identifier: MIT
"""Exercise the real engine-to-public boundary, including evidence and policy semantics."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from weather.api.aggregate_adapter import compute
from weather.api.metric_registry import PublicInputError
from weather.products.aggregate import Interval, Support

START = datetime(2026, 1, 1, tzinfo=UTC)


def span(start: int, end: int) -> Interval:
    return Interval(START + timedelta(seconds=start), START + timedelta(seconds=end))


def sample(value: int | None, start: int, end: int, identity: str, **kwargs: object) -> Support:
    return Support(span(start, end), value, identity, **kwargs)  # type: ignore[arg-type]


def test_public_mean_retains_full_denominator_null_and_revisions() -> None:
    window = span(0, 60)
    supports = {"temperature": [sample(0, 0, 30, "zero")]}
    first = compute(window, "temperature", "mean", supports, evidence_revision="snapshot_1")
    repeated = compute(window, "temperature", "mean", supports, evidence_revision="snapshot_1")
    late = compute(window, "temperature", "mean", supports, evidence_revision="snapshot_2")
    assert first.aggregate.value == 0
    assert first.aggregate.coverage.fraction == 0.5
    assert first.aggregate.coverage.expected_seconds == 60
    assert first.aggregate.evidence_ids == ("zero",)
    assert first.aggregate_id == repeated.aggregate_id != late.aggregate_id
    assert first.aggregate.algorithm_revision == late.aggregate.algorithm_revision
    empty = compute(window, "temperature", "mean", {}, evidence_revision="snapshot_1")
    assert empty.aggregate.value is None
    assert empty.aggregate.reason == "no_qualified_support"
    assert empty.aggregate.coverage.fraction == 0


def test_clipped_rain_is_conserved_and_explicitly_labelled_estimate() -> None:
    result = compute(
        span(0, 30),
        "precipitation",
        "observed_accumulation",
        {"precipitation": [sample(4, 0, 60, "rain")]},
        evidence_revision="snapshot_1",
    )
    assert result.aggregate.value == 2
    assert result.aggregate.unit == "mm"
    assert result.aggregate.reason == "Estimated using uniform intensity over support."


def test_lightning_uses_integer_authority_and_retains_boundary_uncertainty() -> None:
    inputs = {
        "lightning_count": [
            sample(3, 0, 60, "contained", authority="obs_st"),
            sample(2, 60, 120, "boundary", authority="obs_st"),
            sample(200, 0, 60, "other", authority="obs_air"),
        ]
    }
    with pytest.raises(ValueError, match="authority"):
        compute(span(0, 90), "lightning_count", "count", inputs, evidence_revision="r1")
    result = compute(
        span(0, 90),
        "lightning_count",
        "count",
        inputs,
        evidence_revision="r1",
        lightning_authority="obs_st",
    )
    assert result.aggregate.value == 3 and type(result.aggregate.value) is int
    assert result.aggregate.unit == "1"
    assert result.aggregate.reason == "boundary_uncertain"
    assert result.details is not None
    assert result.details.boundary_uncertain_count == 2  # type: ignore[union-attr]


def test_vector_mean_keeps_speed_and_direction_evidence_without_rapid_substitution() -> None:
    inputs = {
        "wind_avg": [sample(4, 0, 60, "speed")],
        "wind_direction": [sample(359, 0, 30, "west"), sample(1, 30, 60, "east")],
    }
    result = compute(span(0, 60), "wind_direction", "vector_mean", inputs, evidence_revision="r1")
    assert result.aggregate.value == 0
    assert result.aggregate.coverage.fraction == 1
    assert result.aggregate.evidence_ids == ("east", "speed", "west")
    inputs["wind_speed"] = inputs.pop("wind_avg")
    unavailable = compute(
        span(0, 60),
        "wind_direction",
        "vector_mean",
        inputs,
        evidence_revision="r1",
    )
    assert unavailable.aggregate.value is None
    assert unavailable.aggregate.coverage.fraction == 0
    assert unavailable.aggregate.reason == "Wind direction is unavailable."


def test_pressure_requires_effective_endpoint_policy() -> None:
    inputs = {"station_pressure": [sample(1000, 0, 600, "first"), sample(1002, 3000, 3600, "last")]}
    with pytest.raises(ValueError, match="endpoint"):
        compute(span(0, 3600), "station_pressure", "trend", inputs, evidence_revision="r1")
    result = compute(
        span(0, 3600),
        "station_pressure",
        "trend",
        inputs,
        evidence_revision="r1",
        pressure_endpoint_seconds=600,
        pressure_minimum_fraction=Fraction(1),
    )
    assert result.aggregate.value == 2.4
    assert result.aggregate.unit == "hPa/h"
    assert result.aggregate.coverage.fraction == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    "metric,statistic",
    [
        ("unknown", "mean"),
        ("temperature", "sum"),
        ("wind_direction", "mean"),
    ],
)
def test_unregistered_recipes_are_rejected(metric: str, statistic: str) -> None:
    with pytest.raises(PublicInputError):
        compute(span(0, 60), metric, statistic, {}, evidence_revision="r1")
