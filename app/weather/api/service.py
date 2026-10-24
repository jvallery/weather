# SPDX-License-Identifier: MIT
"""Current-conditions cache, bounded history reads and on-read aggregates."""

import asyncio
import math
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any
from uuid import UUID

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from weather.timemodel import day_for, weather_day

from .cursors import CursorCodec, Position
from .models import (
    Aggregate,
    AggregateQuery,
    Aggregates,
    Conversion,
    Coverage,
    Current,
    DirectionState,
    Metric,
    MetricDefinition,
    MetricRegistry,
    ObservationPoint,
    ObservationQuery,
    Observations,
    RoseBin,
    rounded,
)
from .registry import (
    DEFAULT_CADENCES,
    ENTRIES,
    Cadence,
    Entry,
    Family,
    PublicError,
    require_statistic,
    sourced,
    unavailable,
)
from .source import SPEED_EDGES, BinSpec, Reading, Row, SourceUnavailable, WeatherReader

REFRESH_SECONDS = 5
# Readiness tolerates a short DB blip; the cache keeps serving meanwhile.
READY_SECONDS = 30
MAX_POINTS = 5000
MIN_RESOLUTION_SECONDS = 60
# Raw history and aggregate windows per source family (rows scanned stay bounded).
RAW_WINDOW = {"obs_st": timedelta(days=31), "rapid_wind": timedelta(days=1)}
AGGREGATE_WINDOW = {"obs_st": timedelta(days=92), "rapid_wind": timedelta(days=7)}
ROSE_MAX_BINS = 400
VARIABLE_RESULTANT = 0.1
UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _conversion(unit: str, scale: Fraction, offset: Fraction) -> Conversion:
    return Conversion(
        unit=unit,
        scale_numerator=scale.numerator,
        scale_denominator=scale.denominator,
        offset_numerator=offset.numerator,
        offset_denominator=offset.denominator,
    )


def metric_registry(cadences: Mapping[Family, Cadence]) -> MetricRegistry:
    return MetricRegistry(
        metrics=[
            MetricDefinition(
                metric=entry.metric,
                label=entry.label,
                canonical_unit=entry.unit,
                conversions=[
                    _conversion(unit, scale, offset)
                    for unit, scale, offset in (
                        (entry.unit, Fraction(1), Fraction(0)),
                        *entry.conversions,
                    )
                ],
                statistics=list(entry.statistics),
                expected_interval_seconds=cadences[
                    entry.source.family if entry.source else "obs_st"
                ].interval_seconds,
            )
            for entry in ENTRIES
        ]
    )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def build_current(
    reading: Reading,
    now: datetime,
    cadences: Mapping[Family, Cadence],
    *,
    cached: bool = False,
) -> Current:
    """Freshness is recomputed from source time on every response; nothing is ever re-dated."""
    metrics = []
    for entry in ENTRIES:
        if not entry.current:
            continue
        family: Family = entry.source.family if entry.source else "obs_st"
        cadence = cadences[family]
        base: dict[str, Any] = {
            "metric": entry.metric,
            "unit": entry.unit,
            "expected_interval_seconds": cadence.interval_seconds,
            "value": None,
            "observed_at": None,
            "received_at": None,
            "valid_until": None,
            "quality": "missing",
            "freshness": "unavailable",
            "reason": "No observation has been received.",
        }
        value: float | None
        if entry.source is None:
            row = reading.rain.get("hour" if entry.metric == "rain_last_hour" else "day")
            if row is None or row.get("observed_at") is None:
                base["reason"] = "No rain report in this window."
                metrics.append(Metric.model_validate(base))
                continue
            window_start = _rain_window(entry.metric, now)
            elapsed = max((now - window_start).total_seconds(), 1e-6)
            value = rounded(_number(row["total"]), 4)
            base.update(
                observed_at=row["observed_at"],
                received_at=row["received_at"],
                coverage=min(1.0, max(0.0, float(row["support_seconds"]) / elapsed)),
            )
        else:
            source_row = reading.obs_st if family == "obs_st" else reading.rapid_wind
            if source_row is None:
                metrics.append(Metric.model_validate(base))
                continue
            base.update(
                observed_at=source_row["observed_at"], received_at=source_row["received_at"]
            )
            value = _number(source_row[entry.source.column])
            if entry.metric == "lightning_distance" and not _number(
                source_row.get("lightning_strike_count")
            ):
                base["reason"] = "No lightning in the newest report."
                metrics.append(Metric.model_validate(base))
                continue
        if value is None:
            base["reason"] = "The newest observation has no valid value."
            metrics.append(Metric.model_validate(base))
            continue
        valid_until = base["observed_at"] + timedelta(seconds=cadence.hold_seconds)
        fresh = now < valid_until
        base.update(
            value=value,
            quality="valid",
            valid_until=valid_until,
            freshness="fresh" if fresh else "stale",
            reason=None if fresh else "No newer observation has arrived.",
        )
        metrics.append(Metric.model_validate(base))
    has_value = any(item.value is not None for item in metrics)
    return Current(
        generated_at=now,
        station_alias=reading.station_alias or "home",
        data_state="cached" if cached and has_value else ("live" if has_value else "unavailable"),
        metrics=metrics,
    )


def _rain_window(metric: str, now: datetime) -> datetime:
    if metric == "rain_last_hour":
        return now - timedelta(hours=1)
    return day_for(now).start


class ApiMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.latency = Histogram(
            "weather_api_request_duration_seconds",
            "Public route latency",
            ["route", "status"],
            registry=registry,
        )
        self.cache = Counter(
            "weather_api_current_cache_total",
            "Current conditions served from a live or cached reading",
            ["result"],
            registry=registry,
        )
        self.db_errors = Counter(
            "weather_api_db_errors_total", "Failed database reads", ["operation"], registry=registry
        )
        for result in ("live", "cached", "unavailable"):
            self.cache.labels(result)
        for operation in ("current", "observations", "aggregates"):
            self.db_errors.labels(operation)


class SourceAges(Collector):
    """Scrape-time ages; weather_current_conditions_age_seconds is absent without an obs_st."""

    def __init__(self, service: WeatherService) -> None:
        self._service = service

    def describe(self) -> Iterable[GaugeMetricFamily]:
        return []

    def collect(self) -> Iterable[GaugeMetricFamily]:
        reading, now = self._service.reading, self._service.now()
        ages = GaugeMetricFamily(
            "weather_metric_source_age_seconds",
            "Now minus the source time of each current metric with a value",
            labels=["metric"],
        )
        if reading is not None:
            for item in build_current(reading, now, self._service.cadences).metrics:
                if item.value is not None and item.observed_at is not None:
                    ages.add_metric([item.metric], (now - item.observed_at).total_seconds())
        yield ages
        if reading is not None and reading.obs_st is not None:
            gauge = GaugeMetricFamily(
                "weather_current_conditions_age_seconds",
                "Now minus the newest valid obs_st source time",
            )
            gauge.add_metric([], (now - reading.obs_st["observed_at"]).total_seconds())
            yield gauge


class WeatherService:
    def __init__(
        self,
        reader: WeatherReader,
        cursors: CursorCodec,
        *,
        cadences: Mapping[Family, Cadence] = DEFAULT_CADENCES,
        registry: CollectorRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reader, self.cursors, self.cadences = reader, cursors, dict(cadences)
        self.now, self._monotonic = clock, monotonic
        registry = registry if registry is not None else CollectorRegistry()
        self.metrics = ApiMetrics(registry)
        registry.register(SourceAges(self))
        self.reading: Reading | None = None
        self.registry_document = metric_registry(self.cadences)
        self._refresh_failed = False
        self._db_ok_at = -math.inf
        self._task: asyncio.Task[None] | None = None

    # Current conditions -------------------------------------------------------------------

    async def refresh(self) -> None:
        now = self.now()
        try:
            self.reading = await self.reader.current(
                now, now - timedelta(hours=1), day_for(now).start
            )
            self._refresh_failed = False
            self._db_ok_at = self._monotonic()
        except SourceUnavailable:
            self._refresh_failed = True
            self.metrics.db_errors.labels("current").inc()
            if await self.reader.ping():
                self._db_ok_at = self._monotonic()

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(REFRESH_SECONDS)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def _servable(self) -> Current | None:
        if self.reading is None:
            return None
        current = build_current(
            self.reading, self.now(), self.cadences, cached=self._refresh_failed
        )
        if self._refresh_failed and not any(m.freshness == "fresh" for m in current.metrics):
            # The DB is down and nothing cached is still fresh: say so instead of guessing.
            return None
        return current

    def current(self) -> Current:
        current = self._servable()
        if current is None:
            self.metrics.cache.labels("unavailable").inc()
            raise unavailable()
        self.metrics.cache.labels("cached" if self._refresh_failed else "live").inc()
        return current

    def ready(self) -> bool:
        """Ready while the DB answered recently, or while a DB outage still has fresh cache."""
        recent = self._monotonic() - self._db_ok_at < READY_SECONDS
        return recent or self._servable() is not None

    # Observations ------------------------------------------------------------------------

    async def observations(self, query: ObservationQuery) -> Observations:
        entries = [sourced(metric) for metric in query.metrics]
        families = {source.family for _, source in entries}
        if len(families) != 1:
            raise PublicError(
                "invalid_request", "Select metrics that share one source cadence per request."
            )
        family: Family = families.pop()
        for metric in query.metrics:
            require_statistic(metric, "observed")
        window = query.end - query.start
        if window > RAW_WINDOW[family]:
            seconds = math.ceil(window.total_seconds() * len(query.metrics) / MAX_POINTS)
            raise PublicError(
                "range_too_large",
                "The raw window is too long; request aggregates or a shorter interval.",
                suggested_resolution_seconds=max(MIN_RESOLUTION_SECONDS, seconds),
            )
        now = self.now()
        position: Position | None = (
            self.cursors.decode(query.cursor, query, now=now) if query.cursor else None
        )
        snapshot = position.snapshot if position else now
        rows_per_page = max(1, query.limit // len(query.metrics))
        try:
            rows = await self.reader.observations(
                family,
                end=query.end,
                snapshot=snapshot,
                after_time=position.after_time if position else query.start,
                after_id=position.after_id if position else UUID(int=0),
                rows=rows_per_page + 1,
            )
        except SourceUnavailable:
            self.metrics.db_errors.labels("observations").inc()
            raise unavailable() from None
        page, more = rows[:rows_per_page], len(rows) > rows_per_page
        points = []
        for row in page:
            for entry, source in entries:
                value = _number(row[source.column])
                points.append(
                    ObservationPoint(
                        id=str(row["id"]),
                        observed_at=row["observed_at"],
                        received_at=row["received_at"],
                        metric=entry.metric,
                        value=value,
                        unit=entry.unit,
                        quality="valid"
                        if value is not None
                        else ("missing" if row["valid"] else "invalid"),
                    )
                )
        cursor = None
        if more:
            last = page[-1]
            cursor = self.cursors.encode(
                query,
                snapshot=snapshot,
                after_time=last["observed_at"],
                after_id=last["id"],
                now=now,
                previous=position,
            )
        return Observations(
            generated_at=now,
            start=query.start,
            end=query.end,
            metrics=query.metrics,
            points=points,
            next_cursor=cursor,
        )

    # Aggregates --------------------------------------------------------------------------

    async def aggregates(self, query: AggregateQuery) -> Aggregates:
        selected = [sourced(metric) for metric in query.metrics]
        for entry, _ in selected:
            for statistic in query.statistics:
                require_statistic(entry.metric, statistic)
            if "observed" in query.statistics:
                raise PublicError("unsupported_statistic", "Use observations for raw values.")
        bins = bin_spec(query)
        count = len(bin_list(bins))
        if count * len(query.metrics) * len(query.statistics) > MAX_POINTS:
            seconds = math.ceil(
                query.seconds * len(query.metrics) * len(query.statistics) / MAX_POINTS
            )
            raise PublicError(
                "range_too_large",
                "Too many aggregate points; choose a coarser resolution.",
                suggested_resolution_seconds=max(MIN_RESOLUTION_SECONDS, seconds),
            )
        if "rose" in query.statistics and count * len(query.metrics) > ROSE_MAX_BINS:
            raise PublicError(
                "range_too_large", "Too many wind-rose intervals; choose a coarser resolution."
            )
        for _, source in selected:
            if query.end - query.start > AGGREGATE_WINDOW[source.family]:
                raise PublicError(
                    "range_too_large", "The aggregate window is too long for this metric."
                )
        items: list[Aggregate] = []
        for entry, source in selected:
            wind = entry.metric == "wind_direction"
            try:
                rows, rose_rows = await self.reader.aggregates(
                    source,
                    bins,
                    cap=timedelta(seconds=self.cadences[source.family].cap_seconds),
                    wind=wind,
                    rose="rose" in query.statistics,
                )
            except SourceUnavailable:
                self.metrics.db_errors.labels("aggregates").inc()
                raise unavailable() from None
            items.extend(summarize(entry, query.statistics, bins, rows, rose_rows))
        return Aggregates(
            generated_at=self.now(),
            start=query.start,
            end=query.end,
            resolution=query.resolution,
            items=items,
        )


def bin_spec(query: AggregateQuery) -> BinSpec:
    if query.resolution == "day":
        first = day_for(query.start).local_date
        last = day_for(query.end - timedelta(microseconds=1)).local_date
        days = [weather_day(first + timedelta(days=n)) for n in range((last - first).days + 1)]
        return BinSpec(
            day=True,
            start=query.start,
            end=query.end,
            bounds=[day.start for day in days] + [days[-1].end],
        )
    seconds = int(query.resolution)
    if seconds < MIN_RESOLUTION_SECONDS:
        raise PublicError("invalid_request", "Resolution must be at least 60 seconds or 'day'.")
    return BinSpec(day=False, start=query.start, end=query.end, stride=timedelta(seconds=seconds))


def bin_list(bins: BinSpec) -> list[tuple[datetime, datetime]]:
    """The same bins the SQL produces: date_bin from the Unix epoch, or weather_day bounds."""
    if bins.day:
        edges = list(bins.bounds)
    else:
        assert bins.stride is not None
        first = bins.start - (bins.start - UTC_EPOCH) % bins.stride
        edges = [first]
        while edges[-1] < bins.end:
            if len(edges) > MAX_POINTS + 1:
                break
            edges.append(edges[-1] + bins.stride)
    result = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        lo, hi = max(lo, bins.start), min(hi, bins.end)
        if hi > lo:
            result.append((lo, hi))
    return result


def _rose(row: Row | None, rose_rows: list[Row], expected: float) -> list[RoseBin]:
    result = []
    directional = 0.0
    for item in rose_rows:
        sector, speed = int(item["sector"]) % 16, int(item["speed_bin"])
        seconds = float(item["seconds"])
        directional += seconds
        result.append(
            RoseBin(
                category="directional",
                sector_start=(sector * 22.5 - 11.25) % 360,
                sector_end=sector * 22.5 + 11.25,
                speed_min=SPEED_EDGES[speed - 1],
                speed_max=SPEED_EDGES[speed] if speed < len(SPEED_EDGES) else None,
                duration_seconds=round(seconds, 6),
            )
        )
    calm = float(row["calm_seconds"] or 0) if row else 0.0
    missing = max(0.0, expected - directional - calm)
    for category, seconds in (("calm", calm), ("variable", 0.0), ("missing", missing)):
        result.append(
            RoseBin(
                category=category,  # type: ignore[arg-type]
                sector_start=None,
                sector_end=None,
                speed_min=None,
                speed_max=None,
                duration_seconds=round(seconds, 6),
            )
        )
    return result


def _vector(row: Row) -> tuple[float | None, DirectionState]:
    weight = float(row["weight"] or 0)
    if weight <= 0:
        return None, "calm" if (row["calm_seconds"] or 0) > 0 else "missing"
    east, north = float(row["east"]), float(row["north"])
    if math.hypot(east, north) / weight < VARIABLE_RESULTANT:
        return None, "variable"
    degrees = round(math.degrees(math.atan2(east, north)) % 360, 1)
    return (0.0 if degrees == 360.0 else degrees), "directional"


def summarize(
    entry: Entry,
    statistics: list[str],
    bins: BinSpec,
    rows: list[Row],
    rose_rows: list[Row],
) -> list[Aggregate]:
    by_bin = {row["bin_start"]: row for row in rows}
    roses: dict[datetime, list[Row]] = {}
    for item in rose_rows:
        roses.setdefault(item["bin_start"], []).append(item)
    items = []
    for start, end in bin_list(bins):
        expected = (end - start).total_seconds()
        row = by_bin.get(start)
        for statistic in statistics:
            value: float | None = None
            state: DirectionState | None = None
            rose = None
            support = float(row["support_seconds"] or 0) if row else 0.0
            if row is not None:
                if statistic == "mean":
                    value = _number(row["mean"])
                elif statistic == "min":
                    value = _number(row["minimum"])
                elif statistic == "max":
                    value = _number(row["maximum"])
                elif statistic == "sum":
                    value = _number(row["total"])
                    if value is not None and entry.metric == "lightning_count":
                        value = float(round(value))
                elif statistic == "vector_mean":
                    value, state = _vector(row)
                    support = float(row["calm_seconds"] or 0) + float(
                        row["directional_seconds"] or 0
                    )
            elif statistic == "vector_mean":
                state = "missing"
            if statistic == "rose":
                rose = _rose(row, roses.get(start, []), expected)
                support = sum(b.duration_seconds for b in rose if b.category != "missing")
            support = min(support, expected)
            reason = None
            if value is None and statistic != "rose":
                reason = {
                    "calm": "Calm: no direction.",
                    "variable": "Variable direction.",
                }.get(state or "", "No valid observations in this interval.")
            items.append(
                Aggregate(
                    metric=entry.metric,
                    statistic=statistic,
                    start=start,
                    end=end,
                    value=rounded(value),
                    unit=entry.unit,
                    coverage=Coverage(
                        fraction=round(support / expected, 6),
                        support_seconds=round(support, 6),
                        expected_seconds=expected,
                    ),
                    evidence_count=int(row["evidence_count"]) if row else 0,
                    evidence_hash=row["evidence_hash"] if row else None,
                    reason=reason,
                    direction_state=state,
                    rose=rose,
                )
            )
    return items

