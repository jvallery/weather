# SPDX-License-Identifier: MIT
"""The public registry and evidence engine share one calculation boundary.

Inputs are already qualified support, never a page of raw observations. The caller
owns a consistent publication snapshot and effective support/authority policy.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from weather.products import aggregate as engine

from .metric_registry import metric_definition, validate_selection
from .public_models import Aggregate, Coverage, Interval


def _number(value: Fraction | float | int | None) -> int | float | None:
    if value is None or isinstance(value, (float, int)):
        return value
    return value.numerator if value.denominator == 1 else float(value)


def _interval(value: engine.Interval) -> Interval:
    return Interval(start=value.start, end=value.end)


def public_coverage(value: engine.Coverage) -> Coverage:
    return Coverage(
        fraction=float(value.fraction),
        support_seconds=float(value.support_seconds),
        expected_seconds=float(value.expected_seconds),
        algorithm_revision=value.algorithm_revision,
        gaps=tuple(_interval(gap) for gap in value.gaps),
    )


@dataclass(frozen=True)
class Computation:
    aggregate: Aggregate
    aggregate_id: str
    exact: engine.Scalar
    # Retain the complete wind/strike/endpoint evidence for downstream summaries.
    details: engine.Wind | engine.Lightning | engine.PressureTrend | None = None


def compute(
    window: engine.Interval,
    metric: str,
    statistic: str,
    supports: Mapping[str, Sequence[engine.Support]],
    *,
    evidence_revision: str,
    lightning_authority: Literal["obs_st", "obs_air"] | None = None,
    pressure_endpoint_seconds: int | None = None,
    pressure_minimum_fraction: Fraction | None = None,
) -> Computation:
    """Calculate one explicitly selected public statistic without inventing policy."""
    validate_selection([metric], statistic)
    definition = metric_definition(metric)
    samples = supports.get(metric, ())
    details: engine.Wind | engine.Lightning | engine.PressureTrend | None = None
    result: engine.Scalar
    if statistic in {"mean", "min", "max", "solar_energy"}:
        scalar_statistic: Literal["mean", "min", "max", "solar_energy"]
        # Explicit branches keep the reviewed engine vocabulary independent of request text.
        match statistic:
            case "mean":
                scalar_statistic = "mean"
            case "min":
                scalar_statistic = "min"
            case "max":
                scalar_statistic = "max"
            case _:
                scalar_statistic = "solar_energy"
        result = engine.scalar(
            window,
            samples,
            metric=metric,
            unit=definition.canonical_unit,
            statistic=scalar_statistic,
            evidence_revision=evidence_revision,
        )
    elif metric == "precipitation" and statistic == "observed_accumulation":
        result = engine.precipitation(window, samples, evidence_revision=evidence_revision)
    elif metric == "lightning_count" and statistic == "count":
        if lightning_authority is None:
            raise ValueError("Commissioned lightning authority is required")
        strike = engine.lightning(
            window,
            samples,
            authority=lightning_authority,
            evidence_revision=evidence_revision,
        )
        details = strike
        result = engine.Scalar(
            None if strike.value is None else Fraction(strike.value),
            "1",
            strike.coverage,
            strike.reason,
            strike.algorithm_revision,
            strike.evidence_revision,
            strike.evidence_ids,
            strike.aggregate_id,
        )
    elif metric == "station_pressure" and statistic == "trend":
        if pressure_endpoint_seconds is None or pressure_minimum_fraction is None:
            raise ValueError("Commissioned pressure endpoint policy is required")
        details = engine.pressure_trend(
            window,
            samples,
            endpoint_seconds=pressure_endpoint_seconds,
            minimum_endpoint_fraction=pressure_minimum_fraction,
            evidence_revision=evidence_revision,
        )
        result = details.aggregate
    elif metric == "wind_direction" and statistic == "vector_mean":
        # Rapid speed cannot silently replace reported mean speed in this recipe.
        direction = engine.wind(
            window,
            supports.get("wind_avg", ()),
            samples,
            supports.get("wind_gust", ()),
            evidence_revision=evidence_revision,
        )
        details = direction
        result = engine.Scalar(
            None
            if direction.direction_degrees is None
            else Fraction(str(direction.direction_degrees)),
            "deg",
            direction.direction_coverage,
            None
            if direction.direction_degrees is not None
            else "Wind direction is " + direction.direction_state + ".",
            direction.algorithm_revision,
            direction.evidence_revision,
            direction.evidence_ids,
            direction.aggregate_id,
        )
    else:
        raise ValueError("Selected statistic has no aggregate implementation")
    reason = result.reason
    if result.estimate is not None:
        reason = "Estimated using " + result.estimate + "."
    # A public direction projection is distinct from the engine's full wind summary.
    # Bind every public result to its own metric/statistic/unit and projection recipe.
    recipe = json.dumps(
        ["public_projection_v1", metric, statistic, result.unit, result.algorithm_revision],
        separators=(",", ":"),
    )
    algorithm = "public_aggregate_v1_" + hashlib.sha256(recipe.encode()).hexdigest()
    identity = "aggregate_" + hashlib.sha256(
        json.dumps([result.aggregate_id, algorithm], separators=(",", ":")).encode()
    ).hexdigest()
    return Computation(
        aggregate=Aggregate(
            metric=metric,
            statistic=statistic,
            value=_number(result.value),
            unit=result.unit,
            interval=_interval(window),
            coverage=public_coverage(result.coverage),
            evidence_ids=result.evidence_ids,
            evidence_revision=result.evidence_revision,
            algorithm_revision=algorithm,
            reason=reason,
        ),
        aggregate_id=identity,
        exact=result,
        details=details,
    )
