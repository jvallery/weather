# SPDX-License-Identifier: MIT
"""The sidecar `weather` block: #14's on-read aggregates over the segment's [a, b).

`WeatherSummaryProvider` is the seam; the production binding reuses the API's SQL source and
summarizer (the one aggregate engine, C5) with one whole-interval bin, so a segment's weather
equals what `/api/v1/aggregates` returns for the same interval. It reads only the public
telemetry views and never returns evidence IDs, only counts and hashes.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from weather.api.registry import DEFAULT_CADENCES, sourced
from weather.api.service import summarize, whole_bin
from weather.api.source import SourceUnavailable, WeatherReader

LOG = logging.getLogger("weather.pipeline")
# (metric, statistics) in sidecar order.
SUMMARY = (
    ("temperature", ("min", "max", "mean")),
    ("humidity", ("mean",)),
    ("wind_avg", ("mean",)),
    ("wind_gust", ("max",)),
    ("wind_direction", ("vector_mean",)),
    ("precipitation", ("sum",)),
    ("station_pressure", ("mean",)),
    ("irradiance", ("mean",)),
    ("uv", ("max",)),
    ("lightning_count", ("sum",)),
)


class WeatherSummaryProvider(Protocol):
    async def summary(self, start: datetime, end: datetime) -> dict[str, Any] | None:
        """Aggregates for [start, end), or None when telemetry is unavailable."""
        ...


class NoWeather:
    async def summary(self, start: datetime, end: datetime) -> dict[str, Any] | None:
        return None


class AggregateWeather:
    """Production binding to #14's on-read aggregates."""

    def __init__(self, engine: AsyncEngine) -> None:
        # One aggregate at a time and a generous statement budget: this is background work.
        self.reader = WeatherReader(engine, aggregate_ms=30000, aggregate_slots=1)

    async def summary(self, start: datetime, end: datetime) -> dict[str, Any] | None:
        bins = whole_bin(start, end)
        items: list[dict[str, Any]] = []
        try:
            for metric, statistics in SUMMARY:
                entry, source = sourced(metric)
                rows, rose = await self.reader.aggregates(
                    source,
                    bins,
                    cap=timedelta(seconds=DEFAULT_CADENCES[source.family].cap_seconds),
                    wind=metric == "wind_direction",
                    rose=False,
                )
                for aggregate in summarize(entry, list(statistics), bins, rows, rose):
                    items.append(
                        {
                            "metric": aggregate.metric,
                            "statistic": aggregate.statistic,
                            "value": aggregate.value,
                            "unit": aggregate.unit,
                            "coverage": aggregate.coverage.fraction,
                            "evidence_count": aggregate.evidence_count,
                        }
                    )
        except SourceUnavailable:
            LOG.warning("Weather summary unavailable; the sidecar records none")
            return None
        return {
            "interval": {"start": start.isoformat(), "end": end.isoformat()},
            "source": "aggregates/v1",
            "items": items,
        }
