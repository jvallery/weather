# SPDX-License-Identifier: MIT
"""Code-first public shapes. OpenAPI and the web's TypeScript types are generated from these."""

import math
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
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

MAX_POINTS = 5000
# Almanac pages hold a full 366-day window; the cursor pages anything longer than a request limit.
MAX_ALMANAC_ITEMS = 400
# Accepted instants; keeps time-model and bin arithmetic far from datetime's limits.
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)
LATEST = datetime(2100, 1, 1, tzinfo=UTC)
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
    points: Annotated[list[ObservationPoint], Field(max_length=MAX_POINTS)]
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


class ImportedHistory(PublicModel):
    """History imported from another record (#167): readings before the first live report."""

    source: Literal["verdify-platform"]
    start: UTCInstant
    # The last imported reading (inclusive); live reports follow it.
    last: UTCInstant


class Aggregates(PublicModel):
    generated_at: UTCInstant
    start: UTCInstant
    end: UTCInstant
    resolution: str
    items: list[Aggregate]
    # Present when the window overlaps imported history (its intervals summarize imported readings).
    imported: ImportedHistory | None = None


class Problem(PublicModel):
    code: str
    message: str
    suggested_resolution_seconds: int | None = None


class EventInterval(PublicModel):
    """Half-open [start, end) source-time interval; never empty."""

    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def nonempty(self) -> EventInterval:
        if self.start >= self.end:
            raise ValueError("An interval must be non-empty")
        return self


Camera = Literal["skycam_east", "skycam_west", "frontyard_1"]
EventOrigin = Literal["measured", "visual", "synthesis", "manual"]
EventKind = Literal[
    "precipitation_onset",
    "daily_gust_max",
    "lightning_strike",
    "lightning_interval",
    "visual",
    "synthesis",
    "manual",
]
EVENT_BASIS = {"measured": "instrument", "visual": "visual", "synthesis": "inference"}


class EventSeek(PublicModel):
    """Player seconds for the event's overlap with one published video, via its source map."""

    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(ge=0)]
    source_interval: EventInterval


class EventVideo(PublicModel):
    """Navigation context only: not a claim that this camera visibly recorded the event."""

    camera: Camera
    state: Literal["available", "unavailable"]
    video_id: str | None
    cycle_date: date | None
    segment: Literal["daytime", "overnight"] | None
    seek: EventSeek | None

    @model_validator(mode="after")
    def honest(self) -> EventVideo:
        linked = (self.video_id, self.cycle_date, self.segment, self.seek)
        if (self.state == "available") != all(item is not None for item in linked) or (
            self.state == "unavailable" and any(item is not None for item in linked)
        ):
            raise ValueError("Only an available video carries a link and seek")
        return self


class Event(PublicModel):
    id: str
    revision: Annotated[int, Field(ge=1)]
    kind: EventKind
    label: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    origin: EventOrigin
    basis: Literal["instrument", "visual", "inference"]
    human_edited: bool
    rule_revision: str
    precision: Literal["second", "interval", "clip"]
    source_interval: EventInterval
    # Null camera scope means station-wide.
    camera: Camera | None
    local_dates: Annotated[list[date], Field(min_length=1, max_length=400)]
    evidence_ids: Annotated[list[str], Field(max_length=256)]
    gust_mps: float | None = None
    strikes: Annotated[int, Field(ge=1)] | None = None
    distance_km: float | None = None
    coverage: Coverage | None = None
    videos: list[EventVideo]
    updated_at: UTCInstant

    @model_validator(mode="after")
    def provenance(self) -> Event:
        if self.origin != "manual" and self.basis != EVENT_BASIS[self.origin]:
            raise ValueError("Origin and basis disagree")
        if self.origin == "manual" and not self.human_edited:
            raise ValueError("Manual events carry human attribution")
        if self.origin != "manual" and (
            not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids)
        ):
            raise ValueError("Automatic events need unique nonempty evidence")
        if len(set(self.local_dates)) != len(self.local_dates):
            raise ValueError("Calendar days must be unique")
        if self.origin in ("visual", "synthesis") and self.precision != "clip":
            raise ValueError("A visual or synthesis event keeps its clip interval")
        return self


class Events(PublicModel):
    generated_at: UTCInstant
    items: Annotated[list[Event], Field(max_length=100)]
    next_cursor: str | None


class EventQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    day: date | None = None
    # Events scoped to this camera plus station-wide events.
    camera: Camera | None = None
    origin: EventOrigin | None = None
    order: Literal["asc", "desc"] = "asc"
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    cursor: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,4096}$")] | None = None


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def ordered(self) -> Window:
        if self.start >= self.end:
            raise ValueError("start must precede end")
        if self.start < EARLIEST or self.end > LATEST:
            raise ValueError("instants must fall between 2000 and 2100")
        return self

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class ObservationQuery(Window):
    metrics: Annotated[list[MetricID], Field(min_length=1, max_length=16)]
    limit: Annotated[int, Field(ge=1, le=MAX_POINTS)] = 1000
    cursor: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,2048}$")] | None = None

    @field_validator("metrics")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate metric")
        return value

    def cursor_filters(self) -> dict[str, object]:
        return {
            "start": utc_text(self.start),
            "end": utc_text(self.end),
            "metrics": self.metrics,
            "limit": self.limit,
        }


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


AlmanacProduct = Literal["record", "rain_day", "wind_rose_bin", "astronomy", "comparison"]
# Structured reasons; clients branch on these codes, never on label text.
AlmanacReason = Literal[
    "no_observations",
    "partial_coverage",
    "below_record_coverage",
    "before_history",
    "inadequate_coverage",
    "site_not_configured",
    "solar_events_unavailable",
    "no_moonrise",
    "no_moonset",
]
MoonPhase = Literal[
    "new",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
]


class Interval(PublicModel):
    """Half-open UTC interval [start, end)."""

    start: UTCInstant
    end: UTCInstant


class RecordItem(PublicModel):
    """One local day; ranked only with at least 95% metric-duration coverage."""

    product: Literal["record"] = "record"
    local_date: date
    start: UTCInstant
    end: UTCInstant
    value: float | None
    coverage: Coverage
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None
    ranking_eligible: bool
    # Competition rank among eligible days (ties share a rank); null when not ranked.
    rank: Annotated[int, Field(ge=1)] | None
    missing_reasons: list[AlmanacReason]


class RainDayItem(PublicModel):
    """Observed accumulation for one local day, never scaled up for missing time."""

    product: Literal["rain_day"] = "rain_day"
    local_date: date
    start: UTCInstant
    end: UTCInstant
    value: float | None
    coverage: Coverage
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None
    complete: bool
    missing_reasons: list[AlmanacReason]


class WindRoseBinItem(PublicModel):
    """Duration in one category over the whole interval; frequency is duration / denominator."""

    product: Literal["wind_rose_bin"] = "wind_rose_bin"
    category: Literal["directional", "calm", "variable", "missing"]
    sector_start: float | None
    sector_end: float | None
    speed_min: float | None
    speed_max: float | None
    duration_seconds: Annotated[float, Field(ge=0)]
    denominator_seconds: Annotated[float, Field(gt=0)]


class AstronomyItem(PublicModel):
    """Derived sun and moon events for one local day from the private site; no coordinates."""

    product: Literal["astronomy"] = "astronomy"
    local_date: date
    start: UTCInstant
    end: UTCInstant
    civil_dawn: UTCInstant | None
    sunrise: UTCInstant | None
    solar_noon: UTCInstant | None
    sunset: UTCInstant | None
    civil_dusk: UTCInstant | None
    day_length_seconds: Annotated[float, Field(ge=0)] | None
    # Astral's phase on its 0-28 scale (0 new, 7 first quarter, 14 full, 21 last quarter).
    moon_phase: Annotated[float, Field(ge=0, lt=28)] | None
    moon_phase_name: MoonPhase | None
    moonrise: UTCInstant | None
    moonset: UTCInstant | None
    missing_reasons: list[AlmanacReason]


class ComparisonWindow(PublicModel):
    start: UTCInstant
    end: UTCInstant
    duration_seconds: Annotated[float, Field(gt=0)]
    value: float | None
    coverage: Coverage
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None
    adequate: bool
    # Local days with no support, and local days with some but not full support.
    missing_days: list[date]
    partial_days: list[date]
    missing_reasons: list[AlmanacReason]


class ComparisonItem(PublicModel):
    product: Literal["comparison"] = "comparison"
    first: ComparisonWindow
    second: ComparisonWindow
    # second.value - first.value with no duration normalization; null unless both are adequate.
    difference: float | None
    reason: AlmanacReason | None


AlmanacItem = Annotated[
    RecordItem | RainDayItem | WindRoseBinItem | AstronomyItem | ComparisonItem,
    Field(discriminator="product"),
]


class Almanac(PublicModel):
    generated_at: UTCInstant
    product: AlmanacProduct
    interval: Interval
    comparison_interval: Interval | None
    # First valid station observation to the read time; null when there is no history.
    history_interval: Interval | None
    # The imported part of that history (#167), when there is one.
    imported: ImportedHistory | None = None
    timezone: Literal["America/Denver"]
    label: str
    derived: bool
    metric: MetricID | None
    statistic: str | None
    unit: str | None
    formula_revision: str
    library_revision: str
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None
    coverage: Coverage | None
    missing_reasons: list[AlmanacReason]
    items: Annotated[list[AlmanacItem], Field(max_length=MAX_ALMANAC_ITEMS)]
    next_cursor: str | None


class AlmanacQuery(Window):
    product: AlmanacProduct
    metric: MetricID | None = None
    statistic: MetricID | None = None
    comparison_start: UTCInstant | None = None
    comparison_end: UTCInstant | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_ALMANAC_ITEMS)] = MAX_ALMANAC_ITEMS
    cursor: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,2048}$")] | None = None

    @model_validator(mode="after")
    def product_parameters(self) -> AlmanacQuery:
        first, second = self.comparison_start, self.comparison_end
        if self.product == "comparison":
            if first is None or second is None:
                raise ValueError("comparison requires comparison_start and comparison_end")
            if first >= second:
                raise ValueError("comparison_start must precede comparison_end")
            if first < EARLIEST or second > LATEST:
                raise ValueError("instants must fall between 2000 and 2100")
        elif first is not None or second is not None:
            raise ValueError("comparison bounds apply only to the comparison product")
        selected = self.metric is not None or self.statistic is not None
        if self.product in ("record", "comparison"):
            if self.metric is None or self.statistic is None:
                raise ValueError("records and comparisons select a metric and statistic")
        elif selected:
            raise ValueError("this product has a fixed metric")
        return self

    @property
    def comparison(self) -> tuple[datetime, datetime] | None:
        if self.comparison_start is None or self.comparison_end is None:
            return None
        return self.comparison_start, self.comparison_end

    def cursor_filters(self) -> dict[str, object]:
        return {
            "kind": "almanac",
            "start": utc_text(self.start),
            "end": utc_text(self.end),
            "product": self.product,
            "metric": self.metric,
            "statistic": self.statistic,
            "comparison": [utc_text(t) for t in self.comparison] if self.comparison else None,
            "limit": self.limit,
        }


def rounded(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


# Search (#25) ---------------------------------------------------------------------------

MAX_QUERY = 512
MAX_PREDICATES = 8
MAX_HITS = 20
CameraID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Segment = Literal["daytime", "overnight"]
SearchMode = Literal["hybrid_exact", "lexical", "numeric"]
DegradedReason = Literal["embedding_unavailable", "embedding_busy", "lexical_no_terms"]
Operator = Literal["<", "<=", "=", ">=", ">"]
# Whitelisted measured predicates over the whole video segment (api-contract).
PREDICATES: dict[str, tuple[str, ...]] = {
    "precipitation": ("sum",),
    "wind_gust": ("max",),
    "temperature": ("min", "max", "mean"),
}


def _plain_text(value: str) -> str:
    """Reject malformed Unicode and control characters; never normalize or trim (D12)."""
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF or unicodedata.category(character) == "Cc":
            raise ValueError("Query contains prohibited characters")
    return value


class Predicate(PublicModel):
    metric: Literal["precipitation", "wind_gust", "temperature"]
    statistic: Literal["sum", "max", "min", "mean"]
    op: Operator
    value: float
    unit: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    # Unknown or under-covered aggregates never satisfy a comparison, even "= 0".
    min_coverage: Annotated[float, Field(gt=0, le=1)] = 0.95

    @model_validator(mode="after")
    def supported(self) -> Predicate:
        if self.statistic not in PREDICATES[self.metric]:
            raise ValueError("Statistic is unsupported for this metric")
        return self


class SearchRequest(Window):
    """Explicit UTC bounds; empty or whitespace-only text browses newest first (numeric)."""

    query: Annotated[str, StringConstraints(max_length=MAX_QUERY), AfterValidator(_plain_text)] = ""
    cameras: Annotated[list[CameraID], Field(max_length=16)] = []
    segments: Annotated[list[Segment], Field(max_length=2)] = []
    predicates: Annotated[list[Predicate], Field(max_length=MAX_PREDICATES)] = []
    top_k: Annotated[int, Field(ge=1, le=MAX_HITS)] = 10


class Seek(PublicModel):
    """A playback offset inside the hit's cited media interval and its source instant."""

    media_seconds: Annotated[float, Field(ge=0)]
    source_at: UTCInstant


class MatchedMeasurement(PublicModel):
    """A whole-video aggregate that satisfied a predicate; never a clip-time event."""

    metric: MetricID
    statistic: str
    value: float
    unit: str
    interval: Interval
    coverage: Coverage
    evidence_count: Annotated[int, Field(ge=0)]
    evidence_hash: str | None


class SearchHit(PublicModel):
    rank: Annotated[int, Field(ge=1)]
    video_id: str
    document_id: str
    camera_id: str
    segment: Segment
    video_interval: Interval
    source_interval: Interval
    evidence_precision: Literal["frame", "interval", "clip", "segment"]
    analysis_mode: str
    snippet: str
    media_revision: Annotated[int, Field(ge=1)]
    poster_asset_id: str | None
    seek: Seek | None
    matched_measurements: list[MatchedMeasurement]

    @model_validator(mode="after")
    def cited(self) -> SearchHit:
        source, video = self.source_interval, self.video_interval
        if not video.start <= source.start < source.end <= video.end:
            raise ValueError("A hit cites an interval inside its video")
        if self.seek and not source.start <= self.seek.source_at < source.end:
            raise ValueError("A seek lies inside the cited interval")
        return self


class SearchResults(PublicModel):
    generated_at: UTCInstant
    mode: SearchMode
    degraded_reason: DegradedReason | None
    profile_id: str | None
    count: Annotated[int, Field(ge=0, le=MAX_HITS)]
    hits: Annotated[list[SearchHit], Field(max_length=MAX_HITS)]

    @model_validator(mode="after")
    def counted(self) -> SearchResults:
        if self.count != len(self.hits):
            raise ValueError("count equals the number of hits")
        return self


# Delayed camera previews (#39), served by `weather-preview`.
PreviewState = Literal["fresh", "stale", "unavailable"]
PreviewReason = Literal["no_preview", "too_old", "media_unavailable"]


class PreviewInterval(PublicModel):
    """Closed capture interval [lower, upper] of one snapshot request."""

    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def ordered(self) -> PreviewInterval:
        if self.start > self.end:
            raise ValueError("A capture interval must not be reversed")
        return self


class PreviewImage(PublicModel):
    variant: Literal["large", "small"]
    url: Annotated[str, StringConstraints(pattern=r"^/media/previews/[0-9a-f-]{36}$")]
    content_type: Literal["image/jpeg"]
    width: Annotated[int, Field(ge=1)]
    height: Annotated[int, Field(ge=1)]
    bytes: Annotated[int, Field(ge=1)]


class Preview(PublicModel):
    """The camera's latest eligible preview, judged at `generated_at` by the database clock.

    Age is `generated_at - source_interval.start`. Clients age a received preview locally from
    `age_seconds` and stop showing it as current past `stale_seconds`, even when polls fail.
    Unavailable carries a reason and no images.
    """

    schema_version: Literal[1] = 1
    camera: Camera
    generated_at: UTCInstant
    state: PreviewState
    reason: PreviewReason | None
    refresh_seconds: int
    fresh_seconds: int
    stale_seconds: int
    minimum_delay_seconds: int
    skew_margin_seconds: int
    generation: int | None
    source_interval: PreviewInterval | None
    exposure_upper_bound: UTCInstant | None
    public_not_before: UTCInstant | None
    first_published_at: UTCInstant | None
    expires_at: UTCInstant | None
    age_seconds: float | None
    images: list[PreviewImage] = Field(max_length=2)

    @model_validator(mode="after")
    def consistent(self) -> Preview:
        if (self.state == "unavailable") != (self.reason is not None) or (
            self.state == "unavailable" and self.images
        ):
            raise ValueError("Unavailable previews carry a reason and no images")
        if self.state != "unavailable" and (not self.images or self.source_interval is None):
            raise ValueError("A current preview carries its images and capture interval")
        return self
