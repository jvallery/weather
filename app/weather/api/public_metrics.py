# SPDX-License-Identifier: MIT
"""Bounded public API signals. Source ages are computed at scrape time, without I/O."""

from collections.abc import Callable, Iterable
from datetime import datetime

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily, Metric

from .public_models import Current


class SourceAges:
    def __init__(
        self,
        read: Callable[[], tuple[Current | None, datetime | None]],
        clock: Callable[[], datetime],
    ) -> None:
        self._read, self._clock = read, clock

    def describe(self) -> Iterable[Metric]:
        yield GaugeMetricFamily(
            "weather_metric_source_age_seconds",
            "Response clock minus valid metric source time",
            labels=["metric"],
        )
        yield GaugeMetricFamily(
            "weather_current_conditions_age_seconds",
            "Response clock minus newest qualified obs_st source time",
        )

    def collect(self) -> Iterable[Metric]:
        current, obs_st = self._read()
        now = self._clock()
        family = GaugeMetricFamily(
            "weather_metric_source_age_seconds",
            "Response clock minus valid metric source time",
            labels=["metric"],
        )
        if current is not None:
            for item in current.metrics:
                if (
                    item.value is not None
                    and item.quality == "valid"
                    and item.observed_at is not None
                ):
                    family.add_metric([item.metric], (now - item.observed_at).total_seconds())
        yield family
        if obs_st is not None:
            slo = GaugeMetricFamily(
                "weather_current_conditions_age_seconds",
                "Response clock minus newest qualified obs_st source time",
            )
            slo.add_metric([], (now - obs_st).total_seconds())
            yield slo


class PublicMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()
        self.cache = Counter(
            "weather_api_current_cache_total",
            "Current cache outcomes",
            ["result"],
            registry=self.registry,
        )
        self.source_errors = Counter(
            "weather_api_source_errors_total",
            "Sanitized source failures",
            ["operation"],
            registry=self.registry,
        )
        self.source_latency = Histogram(
            "weather_api_source_duration_seconds",
            "Bounded source reads",
            ["operation"],
            registry=self.registry,
        )
        self.http = Histogram(
            "weather_api_request_duration_seconds",
            "Public route latency",
            ["route", "status"],
            registry=self.registry,
        )
        for value in ("hit", "miss", "coalesced", "cached_error"):
            self.cache.labels(value).inc(0)
        for value in ("current", "observations", "aggregates"):
            self.source_errors.labels(value).inc(0)
