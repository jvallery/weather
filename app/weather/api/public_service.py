# SPDX-License-Identifier: MIT
"""Typed service boundary. The adaptor owns SQL, publication ordering and calibrated support."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from .cursors import CursorCodec, CursorState, Snapshot, position, watermark
from .metric_registry import REGISTRY, PublicInputError
from .public_common import utc_instant
from .public_metrics import PublicMetrics, SourceAges
from .public_models import (
    AggregateQuery,
    Aggregates,
    Current,
    History,
    ObservationQuery,
    age_current,
    enforce_raw_window,
)


@dataclass(frozen=True)
class CurrentReading:
    current: Current
    newest_valid_obs_st_at: datetime | None = None


@dataclass(frozen=True)
class SourcePage[T]:
    data: T
    watermark: str
    next_position: str | None


class PublicSource(Protocol):
    async def current(self) -> CurrentReading: ...

    async def observations(
        self, query: ObservationQuery, snapshot: Snapshot | None
    ) -> SourcePage[History]: ...

    async def aggregates(
        self, query: AggregateQuery, snapshot: Snapshot | None
    ) -> SourcePage[Aggregates]: ...


T = TypeVar("T")


def unavailable() -> PublicInputError:
    return PublicInputError("unavailable", "Weather data is temporarily unavailable.")


class PublicWeatherService:
    def __init__(
        self,
        source: PublicSource,
        cursors: CursorCodec,
        *,
        maximum_raw_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        source_timeout_seconds: float = 5,
        metrics: PublicMetrics | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if maximum_raw_seconds <= 0 or not 0 < source_timeout_seconds <= 60:
            raise ValueError("Invalid public service budgets")
        self._source, self._cursors = source, cursors
        self._maximum_raw_seconds = maximum_raw_seconds
        self._clock, self._monotonic = clock, monotonic
        self._source_timeout = source_timeout_seconds
        self._sleep = sleep
        self._closed = False
        self._background: asyncio.Task[None] | None = None
        self.metrics = metrics if metrics is not None else PublicMetrics()
        self._cached: Current | None = None
        self._age_snapshot: tuple[Current | None, datetime | None] = (None, None)
        self._refresh_after = float("-inf")
        self._failed = False
        self._lock = asyncio.Lock()
        self._flight: asyncio.Task[None] | None = None
        self.metrics.registry.register(SourceAges(lambda: self._age_snapshot, self.now))

    def now(self) -> datetime:
        return utc_instant(self._clock())

    async def _read(self, operation: str, run: Callable[[], Awaitable[T]]) -> T:
        started = self._monotonic()
        try:
            async with asyncio.timeout(self._source_timeout):
                return await run()
        except PublicInputError as error:
            if error.code == "unavailable":
                self.metrics.source_errors.labels(operation).inc()
            raise
        except Exception:
            self.metrics.source_errors.labels(operation).inc()
            raise unavailable() from None
        finally:
            self.metrics.source_latency.labels(operation).observe(self._monotonic() - started)

    async def _refresh(self) -> None:
        if self._closed:
            return
        try:
            reading = await self._read("current", self._source.current)
            try:
                current = Current.model_validate(reading.current.model_dump())
                current = age_current(current, self.now())
                obs_st = (
                    utc_instant(reading.newest_valid_obs_st_at)
                    if reading.newest_valid_obs_st_at is not None
                    else None
                )
            except Exception:
                self.metrics.source_errors.labels("current").inc()
                raise
            if self._closed:
                return
            self._cached = current
            self._age_snapshot = (current, obs_st)
            self._failed = False
        except Exception:
            # Keep no exception object/message; adaptor failures can contain private text.
            self._failed = True
        finally:
            self._refresh_after = self._monotonic() + 5

    async def current(self) -> Current:
        if self._closed:
            raise unavailable()
        if self._monotonic() < self._refresh_after:
            self.metrics.cache.labels("cached_error" if self._failed else "hit").inc()
        else:
            async with self._lock:
                if self._monotonic() >= self._refresh_after:
                    if self._flight is None or self._flight.done():
                        self.metrics.cache.labels("miss").inc()
                        self._flight = asyncio.create_task(self._refresh())
                    else:
                        self.metrics.cache.labels("coalesced").inc()
                    flight = self._flight
                else:
                    flight = None
            if flight is not None:
                # A disconnected caller cannot cancel the refresh shared by other requests.
                await asyncio.shield(flight)
        if self._cached is None or not self._cached.metrics:
            raise unavailable()
        if self._failed and (
            self._cached.data_state == "unavailable"
            or not any(item.value is not None for item in self._cached.metrics)
        ):
            raise unavailable()
        try:
            return age_current(self._cached, self.now(), cached=self._failed)
        except Exception:
            self.metrics.source_errors.labels("current").inc()
            raise unavailable() from None

    def _snapshot(self, query: ObservationQuery) -> CursorState | None:
        return self._cursors.decode(query.cursor, query, now=self.now()) if query.cursor else None

    def _next(
        self,
        query: ObservationQuery,
        page: SourcePage[History] | SourcePage[Aggregates],
        previous: CursorState | None,
    ) -> str | None:
        try:
            bound = watermark(page.watermark)
            if previous is not None and bound != previous.watermark:
                raise ValueError("Publication boundary changed")
            if page.next_position is None:
                return None
            pointer = position(page.next_position)
            if previous is not None and pointer == previous.position:
                raise ValueError("Source page made no progress")
            return self._cursors.encode(
                query,
                Snapshot(watermark=bound, position=pointer),
                now=self.now(),
                previous=previous,
            )
        except PublicInputError:
            raise
        except Exception:
            raise unavailable() from None

    async def observations(self, query: ObservationQuery) -> History:
        if any(REGISTRY[metric].source is None for metric in query.metrics):
            raise PublicInputError(
                "unsupported_statistic",
                "Selected metric has no direct source observations; request an aggregate.",
            )
        enforce_raw_window(query, maximum_seconds=self._maximum_raw_seconds)
        previous = self._snapshot(query)
        snapshot = (
            Snapshot(watermark=previous.watermark, position=previous.position) if previous else None
        )
        page = await self._read(
            "observations",
            lambda: self._source.observations(query.model_copy(update={"cursor": None}), snapshot),
        )
        try:
            data = History.model_validate(page.data.model_dump())
            if (
                data.interval.start != query.start
                or data.interval.end != query.end
                or data.resolution_seconds != 0
                or len(data.points) > query.limit
                or data.next_cursor is not None
                or any(point.metric not in query.metrics for point in data.points)
                or (not data.points and page.next_position is not None)
            ):
                raise ValueError("Invalid raw page contract")
            token = self._next(query, page, previous)
            return History.model_validate(
                {**data.model_dump(), "generated_at": self.now(), "next_cursor": token}
            )
        except PublicInputError as error:
            if error.code == "unavailable":
                self.metrics.source_errors.labels("observations").inc()
            raise
        except Exception:
            self.metrics.source_errors.labels("observations").inc()
            raise unavailable() from None

    async def aggregates(self, query: AggregateQuery) -> Aggregates:
        previous = self._snapshot(query)
        snapshot = (
            Snapshot(watermark=previous.watermark, position=previous.position) if previous else None
        )
        page = await self._read(
            "aggregates",
            lambda: self._source.aggregates(query.model_copy(update={"cursor": None}), snapshot),
        )
        try:
            data = Aggregates.model_validate(page.data.model_dump())
            if (
                data.interval.start != query.start
                or data.interval.end != query.end
                or data.resolution_seconds != query.resolution_seconds
                or data.statistic != query.statistic
                or len(data.items) > query.limit
                or data.next_cursor is not None
                or (not data.items and page.next_position is not None)
                or any(
                    item.metric not in query.metrics
                    or item.statistic != query.statistic
                    or item.interval.start < query.start
                    or item.interval.end > query.end
                    for item in data.items
                )
            ):
                raise ValueError("Invalid aggregate page contract")
            token = self._next(query, page, previous)
            return Aggregates.model_validate(
                {**data.model_dump(), "generated_at": self.now(), "next_cursor": token}
            )
        except PublicInputError as error:
            if error.code == "unavailable":
                self.metrics.source_errors.labels("aggregates").inc()
            raise
        except Exception:
            self.metrics.source_errors.labels("aggregates").inc()
            raise unavailable() from None

    async def start(self) -> None:
        """Root lifespan opts in only after injecting the commissioned source/key/pool."""
        if self._closed:
            raise RuntimeError("Public service is closed")
        if self._background is None or self._background.done():
            self._background = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        while not self._closed:
            try:
                await self.current()
            except PublicInputError:
                pass
            await self._sleep(max(0.01, self._refresh_after - self._monotonic()))

    @staticmethod
    def _consume(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def aclose(self, *, timeout_seconds: float = 1) -> None:
        """Bounded cancellation; root still closes its own pool, even on this timeout."""
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Invalid refresh shutdown budget")
        self._closed = True
        tasks = {task for task in (self._background, self._flight) if task is not None}
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            for task in done:
                self._consume(task)
            for task in pending:
                task.add_done_callback(self._consume)
            if pending:
                raise TimeoutError("Public refresh shutdown exceeded its deadline")
