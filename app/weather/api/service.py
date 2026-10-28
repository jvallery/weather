# SPDX-License-Identifier: MIT
"""Current-conditions cache, bounded history reads and on-read aggregates."""

import asyncio
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any
from uuid import UUID

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from weather.products import almanac
from weather.timemodel import SiteConfig, WeatherDay, day_for, weather_day

from . import events as feed
from .cursors import CursorCodec, EventPosition, Position
from .models import (
    MAX_POINTS,
    Aggregate,
    AggregateQuery,
    Aggregates,
    Almanac,
    AlmanacItem,
    AlmanacQuery,
    AlmanacReason,
    Conversion,
    Coverage,
    Current,
    DirectionState,
    Event,
    EventQuery,
    Events,
    ImportedHistory,
    Interval,
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
    REGISTRY,
    Cadence,
    Entry,
    Family,
    PublicError,
    require_statistic,
    sourced,
    unavailable,
)
from .source import (
    EPOCH_ORIGIN,
    SPEED_EDGES,
    BinSpec,
    Reading,
    Row,
    SourceUnavailable,
    WeatherReader,
)

REFRESH_SECONDS = 5
# Readiness tolerates a short DB blip; the cache keeps serving meanwhile.
READY_SECONDS = 30
MIN_RESOLUTION_SECONDS = 60
# Raw history and aggregate windows per source family (rows scanned stay bounded).
RAW_WINDOW = {"obs_st": timedelta(days=31), "rapid_wind": timedelta(days=1)}
AGGREGATE_WINDOW = {"obs_st": timedelta(days=92), "rapid_wind": timedelta(days=7)}
ROSE_MAX_BINS = 400
VARIABLE_RESULTANT = 0.1
# Almanac windows are whole local days; one window reads at most a year of one-minute reports.
MAX_ALMANAC_DAYS = 366
RECORD_STATISTICS = ("min", "max", "sum")
COMPARISON_STATISTICS = ("mean", "min", "max", "sum")


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
        source_row: Row | None = None
        if entry.source is None:
            row = reading.rain.get("hour" if entry.metric == "rain_last_hour" else "day")
            # Windows and coverage come from the instant the totals were read, never from now.
            window_start = _rain_window(entry.metric, reading.at)
            if entry.metric == "rain_today" and day_for(now).start != window_start:
                base["reason"] = "A new weather day has started; no total has been read yet."
                metrics.append(Metric.model_validate(base))
                continue
            if row is None or row.get("observed_at") is None:
                base["reason"] = "No rain report in this window."
                metrics.append(Metric.model_validate(base))
                continue
            elapsed = max((reading.at - window_start).total_seconds(), 1e-6)
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
            column = entry.source.column if entry.source else ""
            if source_row is not None and _flagged(source_row, column):
                base.update(quality="invalid", reason="The newest value failed validation.")
            else:
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


def _flagged(row: Row, column: str) -> bool:
    return row.get("valid") is False or column in (row.get("invalid_fields") or ())


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
        for operation in (
            "current",
            "observations",
            "aggregates",
            "almanac",
            "events",
            "event_videos",
        ):
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
        site: SiteConfig | None = None,
    ) -> None:
        self.reader, self.cursors, self.cadences = reader, cursors, dict(cadences)
        # Private observer position for derived astronomy; never serialized.
        self.site = site
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
        rows_per_page = max(1, query.limit // len(query.metrics))
        try:
            rows, snapshot = await self.reader.observations(
                family,
                end=query.end,
                snapshot=position.snapshot if position else None,
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
                        else ("invalid" if _flagged(row, source.column) else "missing"),
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

    # Events (#41) ------------------------------------------------------------------------

    async def events(self, query: EventQuery) -> Events:
        now = self.now()
        position: EventPosition | None = (
            self.cursors.decode_events(query.cursor, query, now=now) if query.cursor else None
        )
        sql = feed.events_sql(query.order)
        parameters = feed.parameters(
            query,
            snapshot=position.snapshot if position else None,
            after=(position.after_start, position.after_id) if position else None,
            rows=query.limit + 1,
        )
        try:
            rows = await self.reader.events(sql, parameters)
        except SourceUnavailable:
            self.metrics.db_errors.labels("events").inc()
            raise unavailable() from None
        page = rows[: query.limit]
        cursor = None
        if len(rows) > query.limit:
            last = page[-1]
            cursor = self.cursors.encode_events(
                query,
                snapshot=str(last["snapshot"]),
                after_start=last["starts_at"],
                after_id=last["id"],
                now=now,
                previous=position,
            )
        return Events(
            generated_at=now,
            items=self._built(page, await self._segments(page)),
            next_cursor=cursor,
        )

    async def event(self, event_id: UUID) -> Event | None:
        """The current public revision of one stable logical event, or None (withdrawn/private)."""
        parameters = feed.parameters(None, snapshot=None, after=None, rows=1, event_id=event_id)
        try:
            rows = await self.reader.events(feed.events_sql("asc"), parameters)
        except SourceUnavailable:
            self.metrics.db_errors.labels("events").inc()
            raise unavailable() from None
        return self._built(rows, await self._segments(rows))[0] if rows else None

    async def _segments(self, rows: list[Row]) -> list[feed.PublishedSegment]:
        """Published videos overlapping the page. A failed catalog read degrades every link to an
        explicit unavailable, never a fabricated one, and never fails the feed."""
        if not rows:
            return []
        try:
            found = await self.reader.segments(
                min(row["starts_at"] for row in rows), max(row["ends_at"] for row in rows)
            )
        except SourceUnavailable:
            self.metrics.db_errors.labels("event_videos").inc()
            return []
        return feed.published_segments(found)

    def _built(self, rows: list[Row], segments: list[feed.PublishedSegment]) -> list[Event]:
        try:
            return [feed.build_event(row, segments) for row in rows]
        except ValueError:
            # The database constraints make this unreachable; fail closed, never half a page.
            self.metrics.db_errors.labels("events").inc()
            raise unavailable() from None

    # Aggregates --------------------------------------------------------------------------

    async def aggregates(self, query: AggregateQuery) -> Aggregates:
        selected = [sourced(metric) for metric in query.metrics]
        for entry, _ in selected:
            for statistic in query.statistics:
                require_statistic(entry.metric, statistic)
            if "observed" in query.statistics:
                raise PublicError("unsupported_statistic", "Use observations for raw values.")
        # Bound the window before building any bins (day bins cost one time-model day each).
        for _, source in selected:
            if query.end - query.start > AGGREGATE_WINDOW[source.family]:
                raise PublicError(
                    "range_too_large", "The aggregate window is too long for this metric."
                )
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
        items: list[Aggregate] = []
        for entry, _ in selected:
            items.extend(await self._summaries(entry, query.statistics, bins, "aggregates"))
        imported = await self._imported()
        return Aggregates(
            generated_at=self.now(),
            start=query.start,
            end=query.end,
            resolution=query.resolution,
            items=items,
            imported=imported
            if imported and imported.start < query.end and imported.last >= query.start
            else None,
        )

    async def _imported(self) -> ImportedHistory | None:
        """The imported range (#167); a failed read only omits the label, never the data."""
        try:
            row = await self.reader.imported()
        except SourceUnavailable:
            return None
        if row is None:
            return None
        return ImportedHistory(source=row["source"], start=row["start"], last=row["last"])

    async def _summaries(
        self, entry: Entry, statistics: list[str], bins: BinSpec, operation: str
    ) -> list[Aggregate]:
        """The one on-read aggregate engine every product uses (no second recipe)."""
        assert entry.source is not None
        try:
            rows, rose_rows = await self.reader.aggregates(
                entry.source,
                bins,
                cap=timedelta(seconds=self.cadences[entry.source.family].cap_seconds),
                wind=entry.metric == "wind_direction",
                rose="rose" in statistics,
            )
        except SourceUnavailable:
            self.metrics.db_errors.labels(operation).inc()
            raise unavailable() from None
        return summarize(entry, statistics, bins, rows, rose_rows)

    # Almanac (#42) -----------------------------------------------------------------------

    async def almanac(self, query: AlmanacQuery) -> Almanac:
        """Local almanac products computed on read from the same aggregates (C5)."""
        days = almanac_days(query.start, query.end)
        comparison_days = almanac_days(*query.comparison) if query.comparison else []
        entry: Entry | None = None
        if query.metric is not None and query.statistic is not None:
            entry, source = sourced(query.metric)
            require_statistic(query.metric, query.statistic)
            allowed = RECORD_STATISTICS if query.product == "record" else COMPARISON_STATISTICS
            if query.statistic not in allowed:
                raise PublicError(
                    "unsupported_statistic", "Statistic is unsupported for this almanac product."
                )
            if source.family != "obs_st":
                raise PublicError(
                    "unsupported_statistic", "Almanac products use the one-minute station reports."
                )
        now = self.now()
        position = self.cursors.decode(query.cursor, query, now=now) if query.cursor else None
        try:
            history_start = await self.reader.history_start(now)
        except SourceUnavailable:
            self.metrics.db_errors.labels("almanac").inc()
            raise unavailable() from None
        product = query.product
        items: Sequence[AlmanacItem]
        windows: list[Aggregate] = []
        coverage: Coverage | None = None
        reasons: list[AlmanacReason] = []
        unit: str | None = None
        metric, statistic = query.metric, query.statistic
        if product == "record":
            assert entry is not None and statistic is not None
            windows = await self._summaries(entry, [statistic], day_bins(days), "almanac")
            items = almanac.record_items(
                windows, descending=statistic != "min", history_start=history_start
            )
            label, unit = almanac.records_label(history_start, query.start), entry.unit
        elif product == "rain_day":
            entry = REGISTRY["precipitation"]
            metric, statistic, unit = entry.metric, "sum", entry.unit
            windows = await self._summaries(entry, ["sum"], day_bins(days), "almanac")
            items = almanac.rain_day_items(windows, history_start=history_start)
            label = "Observed rain per local day; partial days are never scaled up"
        elif product == "wind_rose_bin":
            entry = REGISTRY["wind_direction"]
            metric, statistic, unit = entry.metric, "rose", "m/s"
            windows = await self._summaries(
                entry, ["rose"], whole_bin(query.start, query.end), "almanac"
            )
            items = almanac.rose_items(windows[0])
            label = (
                "Duration-weighted wind rose; calm, variable and missing time share the"
                " whole-interval denominator"
            )
        elif product == "astronomy":
            items = await asyncio.to_thread(almanac.astronomy_items, days, self.site)
            label = "Derived sun and moon times for the station site; not measurements"
            if self.site is None:
                reasons.append("site_not_configured")
        else:
            assert entry is not None and statistic is not None and query.comparison
            sides = []
            for side, side_days in (
                ((query.start, query.end), days),
                (query.comparison, comparison_days),
            ):
                (whole,) = await self._summaries(entry, [statistic], whole_bin(*side), "almanac")
                daily = await self._summaries(entry, [statistic], day_bins(side_days), "almanac")
                windows.append(whole)
                sides.append(almanac.comparison_window(whole, daily))
            items = [almanac.compare(*sides)]
            label = "Observed values for each period; no rescaling for missing time"
            unit = entry.unit
        if product in ("record", "rain_day", "wind_rose_bin"):
            coverage = almanac.combined_coverage(windows)
        if windows:
            if not any(item.evidence_count for item in windows):
                reasons.append("no_observations")
            elif coverage is not None and coverage.fraction < 1:
                reasons.append("partial_coverage")
        evidence_count, evidence_hash = almanac.combined_evidence(windows)
        offset = position.after_id.int if position else 0
        page = list(items[offset : offset + query.limit])
        cursor = None
        if offset + len(page) < len(items):
            cursor = self.cursors.encode(
                query,
                snapshot=position.snapshot if position else now,
                after_time=query.start,
                after_id=UUID(int=offset + len(page)),
                now=now,
                previous=position,
            )
        return Almanac(
            generated_at=now,
            product=product,
            interval=Interval(start=query.start, end=query.end),
            comparison_interval=(
                Interval(start=query.comparison[0], end=query.comparison[1])
                if query.comparison
                else None
            ),
            history_interval=(
                Interval(start=history_start, end=now) if history_start is not None else None
            ),
            imported=await self._imported(),
            timezone="America/Denver",
            label=label,
            derived=product == "astronomy",
            metric=metric,
            statistic=statistic,
            unit=unit,
            formula_revision=almanac.FORMULA_REVISION,
            library_revision=almanac.LIBRARY_REVISION,
            evidence_count=evidence_count,
            evidence_hash=evidence_hash,
            coverage=coverage,
            missing_reasons=reasons,
            items=page,
            next_cursor=cursor,
        )


def almanac_days(start: datetime, end: datetime) -> list[WeatherDay]:
    """Whole America/Denver days (23, 24 or 25 h), checked before any bins are built."""
    first, last = day_for(start), day_for(end)
    if first.start != start or last.start != end:
        raise PublicError(
            "invalid_request", "Almanac intervals start and end at local midnight (America/Denver)."
        )
    count = (last.local_date - first.local_date).days
    if count > MAX_ALMANAC_DAYS:
        raise PublicError("range_too_large", "Almanac intervals cover at most 366 local days.")
    return [weather_day(first.local_date + timedelta(days=n)) for n in range(count)]


def day_bins(days: Sequence[WeatherDay]) -> BinSpec:
    bounds = [day.start for day in days] + [days[-1].end]
    return BinSpec(day=True, start=bounds[0], end=bounds[-1], bounds=bounds)


def whole_bin(start: datetime, end: datetime) -> BinSpec:
    """One bin over the whole interval: its denominator is the real elapsed duration."""
    return BinSpec(day=True, start=start, end=end, bounds=[start, end])


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
        first = bins.start - (bins.start - EPOCH_ORIGIN) % bins.stride
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
