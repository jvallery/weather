# SPDX-License-Identifier: MIT
"""Code-first public shapes. OpenAPI and the web's TypeScript types are generated from these."""

import math
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)

UTC_PATTERN = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$"


def utc_instant(value: Any) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(UTC_PATTERN, value):
            raise ValueError("An explicit UTC Z instant is required")
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("An explicit UTC instant is required")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("An explicit UTC instant is required")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UTCInstant = Annotated[
    datetime,
    BeforeValidator(utc_instant),
    PlainSerializer(utc_text, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": UTC_PATTERN}),
]
MetricID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Quality = Literal["valid", "missing", "invalid"]
Freshness = Literal["fresh", "stale", "unavailable"]
DirectionState = Literal["directional", "variable", "calm", "missing"]


class PublicModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        json_schema_serialization_defaults_required=True,
    )


class Conversion(PublicModel):
    """display = canonical * scale_numerator / scale_denominator + offset (as a ratio)."""

    unit: str
    scale_numerator: int
    scale_denominator: Annotated[int, Field(gt=0)]
    offset_numerator: int
    offset_denominator: Annotated[int, Field(gt=0)]


class MetricDefinition(PublicModel):
    metric: MetricID
    label: str
    canonical_unit: str
    conversions: list[Conversion]
    statistics: list[str]
    expected_interval_seconds: float | None


class MetricRegistry(PublicModel):
    metrics: list[MetricDefinition]


class Metric(PublicModel):
    metric: MetricID
    value: float | None
    unit: str
    observed_at: UTCInstant | None
    received_at: UTCInstant | None
    expected_interval_seconds: float | None
    valid_until: UTCInstant | None
    quality: Quality
    freshness: Freshness
    reason: Reason | None
    # Fraction of the window with valid support; derived rain totals only.
    coverage: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def honest(self) -> Metric:
        if self.value is not None and (self.observed_at is None or self.quality != "valid"):
            raise ValueError("A value needs its source time and valid quality")
        if self.value is None and (self.freshness != "unavailable" or not self.reason):
            raise ValueError("A missing value needs a reason")
        if self.freshness == "stale" and not self.reason:
            raise ValueError("A stale value needs a reason")
        return self


class Current(PublicModel):
    schema_version: Literal[1] = 1
    generated_at: UTCInstant
    station_alias: str
    data_state: Literal["live", "cached", "unavailable"]
    metrics: list[Metric]


class ObservationPoint(PublicModel):
    id: str
    observed_at: UTCInstant
    received_at: UTCInstant
    metric: MetricID
    value: float | None
    unit: str
    quality: Quality


class Observations(PublicModel):
    generated_at: UTCInstant
    start: UTCInstant
    end: UTCInstant
    metrics: list[MetricID]
    points: Annotated[list[ObservationPoint], Field(max_length=5000)]
    next_cursor: str | None


class Coverage(PublicModel):
    fraction: Annotated[float, Field(ge=0, le=1)]
    support_seconds: Annotated[float, Field(ge=0)]
    expected_seconds: Annotated[float, Field(gt=0)]


class RoseBin(PublicModel):
    """Duration in one category; directional sectors are half-open, from-direction degrees."""

    category: Literal["directional", "calm", "variable", "missing"]
    sector_start: float | None
    sector_end: float | None
    speed_min: float | None
    speed_max: float | None
    duration_seconds: Annotated[float, Field(ge=0)]


class Aggregate(PublicModel):
    metric: MetricID
    statistic: str
    start: UTCInstant
    end: UTCInstant
    value: float | None
    unit: str
    coverage: Coverage
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None
    reason: Reason | None
    direction_state: DirectionState | None = None
    rose: list[RoseBin] | None = None


class Aggregates(PublicModel):
    generated_at: UTCInstant
    start: UTCInstant
    end: UTCInstant
    resolution: str
    items: list[Aggregate]


class Problem(PublicModel):
    code: str
    message: str
    suggested_resolution_seconds: int | None = None


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def ordered(self) -> Window:
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class ObservationQuery(Window):
    metrics: Annotated[list[MetricID], Field(min_length=1, max_length=16)]
    limit: Annotated[int, Field(ge=1, le=5000)] = 1000
    cursor: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,2048}$")] | None = None

    @field_validator("metrics")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate metric")
        return value


class AggregateQuery(Window):
    metrics: Annotated[list[MetricID], Field(min_length=1, max_length=8)]
    statistics: Annotated[list[MetricID], Field(min_length=1, max_length=4)]
    # Whole seconds (>= 60) aligned to the Unix epoch, or "day" for America/Denver days.
    resolution: Annotated[str, StringConstraints(pattern=r"^(day|[1-9][0-9]{1,7})$")]

    @field_validator("metrics", "statistics")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate selection")
        return value


def rounded(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)
