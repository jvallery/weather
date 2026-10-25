# SPDX-License-Identifier: MIT
"""Stage B: the measured evidence packet over a segment's exact [a, b), from #14's aggregates.

Under C5 an aggregate carries an evidence count and hash, never per-record IDs, so each metric
gets a packet-local ID bound to this run's nonce. The application renders every value in its
canonical unit and its fixed display conversions, which only `render.py`'s templates print;
the model never sees the packet (C18). The packet hash covers values, coverage and evidence
hashes, not the nonce, so an unchanged measurement set hashes the same on every read.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Protocol

from weather.api.models import Aggregate
from weather.api.registry import REGISTRY

# (metric, statistics) in packet order: the sidecar block's scalar subset.
METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("temperature", ("min", "max", "mean")),
    ("humidity", ("mean",)),
    ("wind_avg", ("mean",)),
    ("wind_gust", ("max",)),
    ("precipitation", ("sum",)),
    ("station_pressure", ("mean",)),
    ("lightning_count", ("sum",)),
)
# Below this coverage a value is withheld from the packet and named as a limitation.
MIN_COVERAGE = 0.5
FULL_COVERAGE = 0.95
# Display symbol and decimals per unit; conversions come from the public registry.
UNITS = {
    "degC": ("°C", 1),
    "degF": ("°F", 1),
    "%": ("%", 0),
    "m/s": ("m/s", 1),
    "km/h": ("km/h", 1),
    "mph": ("mph", 1),
    "mm": ("mm", 1),
    "in": ("in", 2),
    "hPa": ("hPa", 1),
    "inHg": ("inHg", 2),
    "1": ("strikes", 0),
}
LABELS = {
    ("temperature", "min"): "Minimum temperature",
    ("temperature", "max"): "Maximum temperature",
    ("temperature", "mean"): "Mean temperature",
    ("humidity", "mean"): "Mean relative humidity",
    ("wind_avg", "mean"): "Mean average wind",
    ("wind_gust", "max"): "Maximum wind gust",
    ("precipitation", "sum"): "Rain total",
    ("station_pressure", "mean"): "Mean station pressure",
    ("lightning_count", "sum"): "Lightning strikes",
}


class AggregateSource(Protocol):
    async def aggregates(
        self, start: datetime, end: datetime, metrics: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> list[Aggregate]: ...


@dataclass(frozen=True)
class Metric:
    id: str
    metric: str
    statistic: str
    label: str
    value: float
    unit: str
    renderings: tuple[tuple[str, str], ...]
    coverage: float
    evidence_count: int

    def text(self) -> str:
        """The first rendering plus conversions, e.g. '21.4 °C (70.5 °F)'."""
        shown = [_join(number, unit) for number, unit in self.renderings]
        return shown[0] + (f" ({', '.join(shown[1:])})" if len(shown) > 1 else "")


@dataclass(frozen=True)
class Packet:
    nonce: str
    start: datetime
    end: datetime
    metrics: tuple[Metric, ...]
    withheld: tuple[str, ...]
    evidence_hash: str
    evidence_count: int

    def by_id(self) -> dict[str, Metric]:
        return {metric.id: metric for metric in self.metrics}

    def find(self, metric: str, statistic: str) -> Metric | None:
        return next(
            (m for m in self.metrics if m.metric == metric and m.statistic == statistic), None
        )


def _join(number: str, unit: str) -> str:
    return f"{number}{unit}" if unit in ("%", "°C", "°F") else f"{number} {unit}"


def _format(value: Fraction, decimals: int) -> str:
    # Half away from zero on the exact rational, so 0.05 renders 0.1, never a float artefact.
    scaled = abs(value) * 10**decimals
    whole = int(scaled + Fraction(1, 2))
    text = str(whole).rjust(decimals + 1, "0")
    body = text if decimals == 0 else f"{text[:-decimals]}.{text[-decimals:]}"
    return "-" + body if value < 0 and whole else body


def renderings(metric: str, value: float) -> tuple[tuple[str, str], ...]:
    entry = REGISTRY[metric]
    exact = Fraction(value)
    out = []
    for unit, scale, offset in (
        (entry.unit, Fraction(1), Fraction(0)),
        *((u, s, o) for u, s, o in entry.conversions if u in UNITS),
    ):
        symbol, decimals = UNITS[unit]
        out.append((_format(exact * scale + offset, decimals), symbol))
    return tuple(out)


def metric_id(nonce: str, metric: str, statistic: str) -> str:
    return f"m-{nonce}-{metric.replace('_', '-')}-{statistic}"


def build_packet(
    nonce: str, start: datetime, end: datetime, aggregates: Sequence[Aggregate]
) -> Packet:
    metrics, withheld, digest_input = [], [], []
    for aggregate in aggregates:
        key = (aggregate.metric, aggregate.statistic)
        if key not in LABELS:
            continue
        digest_input.append(
            [
                aggregate.metric,
                aggregate.statistic,
                aggregate.value,
                aggregate.coverage.fraction,
                aggregate.evidence_count,
                aggregate.evidence_hash,
            ]
        )
        if aggregate.value is None or aggregate.coverage.fraction < MIN_COVERAGE:
            withheld.append(LABELS[key])
            continue
        metrics.append(
            Metric(
                id=metric_id(nonce, *key),
                metric=aggregate.metric,
                statistic=aggregate.statistic,
                label=LABELS[key],
                value=aggregate.value,
                unit=aggregate.unit,
                renderings=renderings(aggregate.metric, aggregate.value),
                coverage=aggregate.coverage.fraction,
                evidence_count=aggregate.evidence_count,
            )
        )
    encoded = json.dumps(digest_input, separators=(",", ":"), allow_nan=False)
    return Packet(
        nonce=nonce,
        start=start,
        end=end,
        metrics=tuple(metrics),
        withheld=tuple(withheld),
        evidence_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        evidence_count=sum(
            a.evidence_count for a in aggregates if (a.metric, a.statistic) in LABELS
        ),
    )


async def read_packet(
    source: AggregateSource, nonce: str, start: datetime, end: datetime
) -> Packet:
    return build_packet(nonce, start, end, await source.aggregates(start, end, METRICS))
