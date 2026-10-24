# SPDX-License-Identifier: MIT
"""Code-first public weather responses and bounded query shapes, without database access."""

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .metric_registry import REGISTRY, PublicInputError, metric_definition, validate_selection
from .public_common import (
    Cursor,
    FractionValue,
    MetricID,
    Nonnegative,
    Number,
    OpaqueID,
    Positive,
    PublicModel,
    Reason,
    Unit,
    UTCInstant,
    utc_instant,
)

Quality = Literal["valid", "suspect", "missing", "invalid"]
Freshness = Literal["fresh", "stale", "unavailable"]


class Interval(PublicModel):
    """Inclusive start, exclusive end, preserving microsecond ordering."""

    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def ordered(self) -> Interval:
        if self.start >= self.end:
            raise ValueError("Source interval must be nonempty and half-open")
        return self

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class Metric(PublicModel):
    metric: MetricID
    value: Number | None
    unit: Unit
    observed_at: UTCInstant | None
    received_at: UTCInstant | None
    expected_interval_seconds: Positive | None
    valid_until: UTCInstant | None
    quality: Quality
    freshness: Freshness
    reason: Reason | None

    @model_validator(mode="after")
    def qualified_value(self) -> Metric:
        definition = metric_definition(self.metric)
        if self.unit != definition.canonical_unit:
            raise ValueError("Current values require the canonical public unit")
        if self.value is None and (not self.reason or self.freshness != "unavailable"):
            raise ValueError("Null values require an unavailable state and reason")
        if self.value is not None and (self.observed_at is None or self.received_at is None):
            raise ValueError("Measured values require original source and receipt times")
        if self.quality in {"invalid", "missing"} and self.value is not None:
            raise ValueError("Invalid/missing source values cannot become public measurements")
        if self.freshness != "fresh" and self.reason is None:
            raise ValueError("Stale/unavailable measurements require a reason")
        if self.freshness == "fresh" and (
            self.value is None
            or self.quality != "valid"
            or self.observed_at is None
            or self.received_at is None
            or self.valid_until is None
        ):
            raise ValueError("Fresh values require valid source timing and quality")
        if self.valid_until is not None and (
            self.observed_at is None or self.valid_until <= self.observed_at
        ):
            raise ValueError("Expiry must follow original source time")
        if self.metric == "lightning_count" and self.value is not None:
            if not isinstance(self.value, int) or self.value < 0:
                raise ValueError("Lightning counts must remain nonnegative integers")
        return self


class Current(PublicModel):
    schema_version: Literal[1] = 1
    generated_at: UTCInstant
    station_alias: Annotated[str, Field(min_length=1, max_length=128)]
    data_state: Literal["live", "cached", "unavailable"]
    metrics: Annotated[tuple[Metric, ...], Field(max_length=128)]

    @model_validator(mode="after")
    def source_freshness(self) -> Current:
        identities = [item.metric for item in self.metrics]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate public metric identity")
        if not self.metrics and self.data_state != "unavailable":
            raise ValueError("Empty current data must be unavailable")
        for item in self.metrics:
            if item.freshness == "fresh" and (
                item.observed_at is None
                or item.received_at is None
                or item.valid_until is None
                or item.observed_at > self.generated_at
                or item.received_at > self.generated_at
                or self.generated_at >= item.valid_until
            ):
                raise ValueError("Response time cannot freshen expired or future source evidence")
        return self


def age_current(current: Current, now: datetime, *, cached: bool = False) -> Current:
    """Reclassify source expiry without changing source/receipt times or numeric values."""
    now = utc_instant(now)
    metrics = []
    for item in current.metrics:
        update: dict[str, object] = {}
        if item.freshness == "fresh" and item.valid_until is not None and now >= item.valid_until:
            update = {"freshness": "stale", "reason": "Source observation expired."}
        metrics.append(Metric.model_validate({**item.model_dump(), **update}))
    return Current.model_validate(
        {
            **current.model_dump(),
            "generated_at": now,
            "data_state": "cached" if cached and current.metrics else current.data_state,
            "metrics": metrics,
        }
    )


class HistoryPoint(PublicModel):
    observed_at: UTCInstant
    received_at: UTCInstant
    metric: MetricID
    value: Number | None
    unit: Unit
    quality: Quality
    evidence_id: OpaqueID
    reason: Reason | None
    support_interval: Interval | None

    @model_validator(mode="after")
    def qualified_value(self) -> HistoryPoint:
        if self.unit != metric_definition(self.metric).canonical_unit:
            raise ValueError("History observations require canonical units")
        if self.value is None and self.reason is None:
            raise ValueError("Missing observations require a reason")
        if self.quality in {"invalid", "missing"} and self.value is not None:
            raise ValueError("Invalid/missing evidence cannot become a measured number")
        if self.metric == "lightning_count" and self.value is not None:
            if not isinstance(self.value, int) or self.value < 0:
                raise ValueError("Lightning counts must remain nonnegative integers")
        return self


class History(PublicModel):
    generated_at: UTCInstant
    interval: Interval
    resolution_seconds: Annotated[int, Field(ge=0, strict=True)]
    aggregation: Literal["observed"] = "observed"
    points: Annotated[tuple[HistoryPoint, ...], Field(max_length=5000)]
    gaps: Annotated[tuple[Interval, ...], Field(max_length=1000)]
    next_cursor: Cursor | None

    @model_validator(mode="after")
    def source_bounds(self) -> History:
        for point in self.points:
            if not self.interval.start <= point.observed_at < self.interval.end:
                raise ValueError("Observation is outside the requested half-open interval")
        for gap in self.gaps:
            if gap.start < self.interval.start or gap.end > self.interval.end:
                raise ValueError("Gap is outside the requested interval")
        if len({item.evidence_id for item in self.points}) != len(self.points):
            raise ValueError("Duplicate observation evidence")
        return self


class Coverage(PublicModel):
    fraction: FractionValue
    support_seconds: Nonnegative
    expected_seconds: Positive
    algorithm_revision: OpaqueID
    gaps: Annotated[tuple[Interval, ...], Field(max_length=1000)]

    @model_validator(mode="after")
    def consistent_fraction(self) -> Coverage:
        if self.support_seconds > self.expected_seconds or not math.isclose(
            self.fraction,
            self.support_seconds / self.expected_seconds,
            abs_tol=1e-9,
        ):
            raise ValueError("Coverage fraction must use the full requested duration")
        return self


class Aggregate(PublicModel):
    metric: MetricID
    statistic: MetricID
    value: Number | None
    unit: Unit
    interval: Interval
    coverage: Coverage
    evidence_ids: Annotated[tuple[OpaqueID, ...], Field(max_length=50000)]
    evidence_revision: OpaqueID
    algorithm_revision: OpaqueID
    reason: Reason | None

    @model_validator(mode="after")
    def computation_provenance(self) -> Aggregate:
        if self.metric in REGISTRY:
            validate_selection([self.metric], self.statistic)
            expected_unit = {"solar_energy": "Wh/m2", "trend": "hPa/h"}.get(
                self.statistic, metric_definition(self.metric).canonical_unit
            )
            if self.unit != expected_unit:
                raise ValueError("Aggregate unit must match the selected public statistic")
        if self.metric == "lightning_count" and self.value is not None:
            if not isinstance(self.value, int) or self.value < 0:
                raise ValueError("Lightning aggregates require nonnegative integers")
        if self.value is None and self.reason is None:
            raise ValueError("Unavailable aggregate requires a reason")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate aggregate evidence identity")
        if not math.isclose(self.coverage.expected_seconds, self.interval.seconds, abs_tol=1e-6):
            raise ValueError("Coverage denominator must equal actual elapsed interval duration")
        for gap in self.coverage.gaps:
            if gap.start < self.interval.start or gap.end > self.interval.end:
                raise ValueError("Coverage gap is outside aggregate source interval")
        return self


class WindRoseBin(PublicModel):
    """Angles are meteorological-from degrees; speed bounds are canonical m/s."""

    category: Literal["directional", "calm", "variable", "missing"]
    angle_start: Annotated[Number, Field(ge=0, lt=360)] | None
    angle_end: Annotated[Number, Field(ge=0, lt=360)] | None
    speed_min: Nonnegative | None
    speed_max: Positive | None
    duration_seconds: Nonnegative
    denominator_seconds: Positive

    @model_validator(mode="after")
    def category_bounds(self) -> WindRoseBin:
        if self.duration_seconds > self.denominator_seconds:
            raise ValueError("Wind category duration exceeds the full-window denominator")
        if self.category == "directional":
            if self.angle_start is None or self.angle_end is None or self.speed_min is None:
                raise ValueError("Directional bins require explicit angle/speed bounds")
            if self.angle_start == self.angle_end or (
                self.speed_max is not None and self.speed_max <= self.speed_min
            ):
                raise ValueError("Wind bin bounds must be nonempty; north wrap is explicit")
        elif any(
            value is not None
            for value in (
                self.angle_start,
                self.angle_end,
                self.speed_min,
                self.speed_max,
            )
        ):
            raise ValueError("Nondirectional categories cannot invent angle/speed bounds")
        return self


class Wind(PublicModel):
    """Canonical m/s speeds, from-direction degrees and distinct duration coverage."""

    mean_speed: Nonnegative | None
    maximum_gust: Nonnegative | None
    direction_degrees: Annotated[Number, Field(ge=0, lt=360)] | None
    direction_state: Literal["directional", "variable", "calm", "unavailable"]
    resultant: FractionValue | None
    speed_coverage: Coverage
    direction_coverage: Coverage
    rose: Annotated[tuple[WindRoseBin, ...], Field(min_length=1, max_length=83)]
    algorithm_revision: OpaqueID
    evidence_revision: OpaqueID
    evidence_ids: Annotated[tuple[OpaqueID, ...], Field(max_length=50000)]
    aggregate_id: OpaqueID

    @model_validator(mode="after")
    def distinct_support(self) -> Wind:
        if (self.direction_state == "directional") != (self.direction_degrees is not None):
            raise ValueError("Calm/variable/unavailable direction must not become an angle")
        denominator = self.speed_coverage.expected_seconds
        if (
            self.direction_coverage.expected_seconds != denominator
            or any(row.denominator_seconds != denominator for row in self.rose)
            or not math.isclose(
                sum(row.duration_seconds for row in self.rose), denominator, abs_tol=1e-6
            )
        ):
            raise ValueError("Wind categories must conserve the whole requested window")
        return self


class Lightning(PublicModel):
    value: Annotated[int, Field(ge=0, strict=True)] | None
    observed_subtotal: Annotated[int, Field(ge=0, strict=True)]
    boundary_uncertain_count: Annotated[int, Field(ge=0, strict=True)]
    coverage: Coverage
    reason: Reason | None
    authority: Literal["obs_st", "obs_air"]
    algorithm_revision: OpaqueID
    evidence_revision: OpaqueID
    evidence_ids: Annotated[tuple[OpaqueID, ...], Field(max_length=50000)]
    aggregate_id: OpaqueID

    @model_validator(mode="after")
    def no_fractional_or_missing_zero(self) -> Lightning:
        expected = self.observed_subtotal if self.coverage.support_seconds else None
        if self.value != expected or (self.value is None and self.reason is None):
            raise ValueError("Unsupported lightning cannot become a fictional zero")
        return self


class Aggregates(PublicModel):
    revision: OpaqueID
    generated_at: UTCInstant
    interval: Interval
    resolution_seconds: Annotated[int, Field(gt=0, strict=True)]
    statistic: MetricID
    items: Annotated[tuple[Aggregate, ...], Field(max_length=5000)]
    next_cursor: Cursor | None


class ObservationQuery(Interval):
    metrics: Annotated[list[MetricID], Field(min_length=1, max_length=32)]
    limit: Annotated[int, Field(ge=1, le=5000)] = 1000
    cursor: Cursor | None = None

    @model_validator(mode="after")
    def known_metrics(self) -> ObservationQuery:
        validate_selection(self.metrics)
        return self


class AggregateQuery(ObservationQuery):
    resolution_seconds: Annotated[int, Field(gt=0, le=2_147_483_647)]
    statistic: MetricID

    @model_validator(mode="after")
    def known_statistics(self) -> AggregateQuery:
        validate_selection(self.metrics, self.statistic)
        if self.statistic == "observed":
            raise PublicInputError("unsupported_statistic", "Use observations for raw source data.")
        return self


def enforce_raw_window(
    query: ObservationQuery, *, maximum_seconds: int, maximum_points: int = 5000
) -> None:
    if maximum_seconds <= 0 or not 1 <= maximum_points <= 5000:
        raise ValueError("A positive commissioned raw-window budget is required")
    if query.seconds > maximum_seconds:
        span = query.end - query.start
        micros = (span.days * 86400 + span.seconds) * 1_000_000 + span.microseconds
        denominator = maximum_points * 1_000_000
        resolution = max(1, (micros * len(query.metrics) + denominator - 1) // denominator)
        raise PublicInputError(
            "range_too_large",
            "Raw source window exceeds the configured limit; "
            "select a shorter interval or an explicit aggregate resolution.",
            suggested_resolution_seconds=resolution,
        )


class Problem(PublicModel):
    code: MetricID
    message: Reason
    request_id: OpaqueID | None
    suggested_resolution_seconds: Annotated[int, Field(gt=0, strict=True)] | None = None
