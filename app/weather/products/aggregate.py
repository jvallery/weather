# SPDX-License-Identifier: MIT
"""Versioned calculations over qualified support, never receipt-time interpolation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

Number = int | float | Decimal | Fraction
Quality = Literal["valid", "invalid", "variable"]
CellKind = Literal["valid", "invalid", "variable", "conflict", "missing"]
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
COVERAGE_REVISION = "qualified_union_microseconds_v1"
MICROS = 1_000_000


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC-aware source time required")
    return value.astimezone(UTC)


def _number(value: Number) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, Fraction)):
        raise ValueError("Measured values must be numeric, not booleans or text")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Measured numbers must be finite")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Measured numbers must be finite")
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _duration(value: timedelta) -> int:
    return ((value.days * 86400 + value.seconds) * MICROS) + value.microseconds


@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", _utc(self.start))
        object.__setattr__(self, "end", _utc(self.end))
        if self.start >= self.end:
            raise ValueError("Source interval must be nonempty and half-open")

    @property
    def microseconds(self) -> int:
        return _duration(self.end - self.start)

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.microseconds, MICROS)

    def intersect(self, other: Interval) -> Interval | None:
        start, end = max(self.start, other.start), min(self.end, other.end)
        return Interval(start, end) if start < end else None


@dataclass(frozen=True, slots=True)
class Support:
    interval: Interval | None
    value: Number | None
    evidence_id: str
    quality: Quality = "valid"
    policy_revision: str = "qualified_interval_v1"
    authority: str | None = None

    def __post_init__(self) -> None:
        if not ID.fullmatch(self.evidence_id) or not ID.fullmatch(self.policy_revision):
            raise ValueError("Public evidence and policy identities must be bounded opaque IDs")
        if self.quality not in {"valid", "invalid", "variable"}:
            raise ValueError("Unknown support quality")
        if self.value is not None:
            _number(self.value)
        if self.quality == "variable" and self.value is not None:
            raise ValueError("Variable direction cannot carry an angle")
        if self.authority is not None and not ID.fullmatch(self.authority):
            raise ValueError("Invalid support authority identity")


@dataclass(frozen=True, slots=True)
class Observation:
    source_time: datetime
    value: Number | None
    evidence_id: str
    valid: bool = True


def forward_holds(
    observations: Iterable[Observation], *, cap_seconds: Number
) -> tuple[Support, ...]:
    """A known invalid sample breaks a preceding hold just like a valid next sample."""
    cap = _number(cap_seconds)
    micros = cap * MICROS
    if cap <= 0 or micros.denominator != 1:
        raise ValueError("Per-metric hold cap must be positive whole microseconds")
    policy = "forward_hold_v1_" + hashlib.sha256(str(cap).encode()).hexdigest()[:16]
    by_time: dict[datetime, list[Observation]] = defaultdict(list)
    for observation in observations:
        by_time[_utc(observation.source_time)].append(observation)
    times = sorted(by_time)
    result = []
    for index, start in enumerate(times):
        end = start + timedelta(microseconds=micros.numerator)
        if index + 1 < len(times):
            end = min(end, times[index + 1])
        for item in by_time[start]:
            result.append(
                Support(
                    Interval(start, end),
                    item.value,
                    item.evidence_id,
                    "valid" if item.valid else "invalid",
                    policy,
                )
            )
    return tuple(result)


def _unique(samples: Iterable[Support]) -> tuple[Support, ...]:
    found: dict[str, Support] = {}
    for sample in samples:
        previous = found.get(sample.evidence_id)
        if previous is not None and (
            previous.interval != sample.interval
            or previous.quality != sample.quality
            or previous.policy_revision != sample.policy_revision
            or previous.authority != sample.authority
            or (None if previous.value is None else _number(previous.value))
            != (None if sample.value is None else _number(sample.value))
        ):
            raise ValueError("Evidence identity conflict requires a reconciled revision")
        found[sample.evidence_id] = sample
    return tuple(found[key] for key in sorted(found))


@dataclass(frozen=True, slots=True)
class Cell:
    interval: Interval
    value: Fraction | None
    kind: CellKind
    evidence_ids: tuple[str, ...]


def cells(window: Interval, samples: Iterable[Support]) -> tuple[Cell, ...]:
    """Sweep a full window once; competing values become gaps, never an average."""
    points = {window.start, window.end}
    starts: dict[datetime, list[int]] = defaultdict(list)
    ends: dict[datetime, list[int]] = defaultdict(list)
    items = _unique(samples)
    numbers = [None if sample.value is None else _number(sample.value) for sample in items]
    for index, sample in enumerate(items):
        clipped = sample.interval.intersect(window) if sample.interval else None
        if clipped:
            points.update((clipped.start, clipped.end))
            starts[clipped.start].append(index)
            ends[clipped.end].append(index)
    boundaries = sorted(points)
    active: set[int] = set()
    result = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        active.difference_update(ends.get(start, ()))
        active.update(starts.get(start, ()))
        evidence = tuple(sorted(items[i].evidence_id for i in active))
        values = {
            numbers[i] for i in active if items[i].quality == "valid" and numbers[i] is not None
        }
        variable = any(items[i].quality == "variable" for i in active)
        invalid = any(
            items[i].quality == "invalid" or (items[i].quality == "valid" and numbers[i] is None)
            for i in active
        )
        value = None
        kind: CellKind
        if invalid:
            kind = "invalid"
        elif len(values) > 1 or (variable and values):
            kind = "conflict"
        elif values:
            kind, value = "valid", next(iter(values))
        elif variable:
            kind = "variable"
        else:
            kind = "missing"
        result.append(Cell(Interval(start, end), value, kind, evidence))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Coverage:
    support_seconds: Fraction
    expected_seconds: Fraction
    gaps: tuple[Interval, ...]
    algorithm_revision: str = COVERAGE_REVISION

    @property
    def fraction(self) -> Fraction:
        return self.support_seconds / self.expected_seconds


def coverage(window: Interval, intervals: Iterable[Interval]) -> Coverage:
    clipped = [piece for interval in intervals if (piece := interval.intersect(window))]
    merged: list[Interval] = []
    for piece in sorted(clipped, key=lambda item: item.start):
        if merged and piece.start <= merged[-1].end:
            merged[-1] = Interval(merged[-1].start, max(piece.end, merged[-1].end))
        else:
            merged.append(piece)
    cursor, gaps = window.start, []
    for piece in merged:
        if cursor < piece.start:
            gaps.append(Interval(cursor, piece.start))
        cursor = piece.end
    if cursor < window.end:
        gaps.append(Interval(cursor, window.end))
    return Coverage(
        sum((piece.seconds for piece in merged), Fraction()), window.seconds, tuple(gaps)
    )


def _identity(
    metric: str,
    statistic: str,
    unit: str,
    window: Interval,
    samples: tuple[Support, ...],
    evidence_revision: str,
    recipe: dict[str, object],
    *,
    input_roles: tuple[str, ...] | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    if not all(ID.fullmatch(value) for value in (metric, statistic, evidence_revision)):
        raise ValueError("Aggregate identities must be bounded opaque IDs")
    roles = input_roles if input_roles is not None else (metric,) * len(samples)
    relevant = [
        (role, sample)
        for role, sample in zip(roles, samples, strict=True)
        if sample.interval is not None and sample.interval.intersect(window) is not None
    ]
    samples = tuple(sample for _, sample in relevant)
    policies = sorted({sample.policy_revision for sample in samples})
    recipe_hash = hashlib.sha256(
        json.dumps(
            {
                **recipe,
                "metric": metric,
                "statistic": statistic,
                "unit": unit,
                "coverage_recipe": COVERAGE_REVISION,
                "support_policies": policies,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    algorithm = "weather_aggregate_v1_" + recipe_hash[:20]
    inputs = [
        [
            role,
            sample.evidence_id,
            None
            if sample.interval is None
            else [sample.interval.start.isoformat(), sample.interval.end.isoformat()],
            None if sample.value is None else str(_number(sample.value)),
            sample.quality,
            sample.authority,
        ]
        for role, sample in relevant
    ]
    binding = [
        metric,
        statistic,
        unit,
        window.start.isoformat(),
        window.end.isoformat(),
        algorithm,
        COVERAGE_REVISION,
        evidence_revision,
        inputs,
    ]
    aggregate_id = (
        "aggregate_"
        + hashlib.sha256(json.dumps(binding, separators=(",", ":")).encode()).hexdigest()
    )
    return algorithm, aggregate_id, tuple(sorted({sample.evidence_id for sample in samples}))


@dataclass(frozen=True, slots=True)
class Scalar:
    value: Fraction | None
    unit: str
    coverage: Coverage
    reason: str | None
    algorithm_revision: str
    evidence_revision: str
    evidence_ids: tuple[str, ...]
    aggregate_id: str
    estimate: str | None = None


def scalar(
    window: Interval,
    samples: Iterable[Support],
    *,
    metric: str,
    unit: str,
    statistic: Literal["mean", "min", "max", "solar_energy"],
    evidence_revision: str,
) -> Scalar:
    items = _unique(samples)
    if statistic == "solar_energy" and unit != "W/m2":
        raise ValueError("Solar integration requires canonical W/m2")
    parts = [cell for cell in cells(window, items) if cell.kind == "valid"]
    support = coverage(window, (part.interval for part in parts))
    value: Fraction | None = None
    if parts:
        values = [part.value for part in parts if part.value is not None]
        weighted = sum(
            (part.value * part.interval.seconds for part in parts if part.value is not None),
            Fraction(),
        )
        if statistic == "mean":
            value = weighted / support.support_seconds
        elif statistic == "min":
            value = min(values)
        elif statistic == "max":
            value = max(values)
        elif statistic == "solar_energy":
            value = weighted / 3600
        else:
            raise ValueError("Unsupported scalar statistic")
    elif statistic not in {"mean", "min", "max", "solar_energy"}:
        raise ValueError("Unsupported scalar statistic")
    output_unit = "Wh/m2" if statistic == "solar_energy" else unit
    algorithm, identity, evidence = _identity(
        metric, statistic, output_unit, window, items, evidence_revision, {"formula": statistic}
    )
    return Scalar(
        value,
        output_unit,
        support,
        None if value is not None else "no_qualified_support",
        algorithm,
        evidence_revision,
        evidence,
        identity,
    )


def _amounts(window: Interval, samples: Iterable[Support]) -> tuple[Support, ...]:
    valid = tuple(
        sample
        for sample in _unique(samples)
        if sample.quality == "valid"
        and sample.value is not None
        and sample.interval is not None
        and sample.interval.intersect(window) is not None
    )
    prior_end: datetime | None = None
    for sample in sorted(
        valid, key=lambda item: item.interval.start if item.interval else window.start
    ):
        if sample.interval is None:
            continue
        if _number(sample.value if sample.value is not None else 0) < 0:
            raise ValueError("Accumulations cannot be negative")
        if prior_end is not None and sample.interval.start < prior_end:
            raise ValueError("Accumulation support requires reconciliation before summation")
        prior_end = sample.interval.end
    return valid


def precipitation(
    window: Interval, samples: Iterable[Support], *, evidence_revision: str
) -> Scalar:
    items = _unique(samples)
    valid = _amounts(window, items)
    total = Fraction()
    intervals = []
    estimated = False
    for sample in valid:
        if sample.interval is None or sample.value is None:
            continue
        clipped = sample.interval.intersect(window)
        if clipped is None:
            continue
        total += _number(sample.value) * Fraction(
            clipped.microseconds, sample.interval.microseconds
        )
        intervals.append(clipped)
        estimated |= clipped != sample.interval
    support = coverage(window, intervals)
    algorithm, identity, evidence = _identity(
        "precipitation",
        "sum",
        "mm",
        window,
        items,
        evidence_revision,
        {"formula": "conserved_overlap_v1"},
    )
    return Scalar(
        total if valid else None,
        "mm",
        support,
        None if valid else "unknown_or_missing_accumulation_support",
        algorithm,
        evidence_revision,
        evidence,
        identity,
        "uniform intensity over support" if estimated else None,
    )


@dataclass(frozen=True, slots=True)
class Lightning:
    observed_subtotal: int
    boundary_uncertain_count: int
    coverage: Coverage
    reason: str | None
    authority: str
    algorithm_revision: str
    evidence_revision: str
    evidence_ids: tuple[str, ...]
    aggregate_id: str

    @property
    def value(self) -> int | None:
        return self.observed_subtotal if self.coverage.support_seconds else None


def lightning(
    window: Interval, samples: Iterable[Support], *, authority: str, evidence_revision: str
) -> Lightning:
    if authority not in {"obs_st", "obs_air"}:
        raise ValueError("One explicit interval-count authority is required")
    items = _unique(samples)
    selected = tuple(sample for sample in items if sample.authority == authority)
    subtotal, uncertain, intervals = 0, 0, []
    for sample in _amounts(window, selected):
        if sample.interval is None or sample.value is None:
            continue
        number = _number(sample.value)
        if number.denominator != 1:
            raise ValueError("Lightning counts must be integers")
        if sample.interval.start >= window.start and sample.interval.end <= window.end:
            subtotal += number.numerator
            intervals.append(sample.interval)
        else:
            uncertain += number.numerator
    support = coverage(window, intervals)
    algorithm, identity, evidence = _identity(
        "lightning",
        "count",
        "count",
        window,
        selected,
        evidence_revision,
        {"formula": "contained_integer_v1", "authority": authority},
    )
    reason = "boundary_uncertain" if uncertain else (None if intervals else "no_interval_authority")
    return Lightning(
        subtotal,
        uncertain,
        support,
        reason,
        authority,
        algorithm,
        evidence_revision,
        evidence,
        identity,
    )


def freshness(
    source_time: datetime | None,
    valid_until: datetime | None,
    *,
    now: datetime,
    quality: str = "valid",
) -> Literal["fresh", "stale", "unavailable"]:
    """Receive/replay timestamps intentionally cannot extend source validity."""
    if quality != "valid" or source_time is None or valid_until is None:
        return "unavailable"
    source, expires, current = _utc(source_time), _utc(valid_until), _utc(now)
    if source > current or expires <= source:
        return "unavailable"
    return "fresh" if current < expires else "stale"


@dataclass(frozen=True, slots=True)
class RoseBin:
    category: Literal["directional", "calm", "variable", "missing"]
    angle_start: Fraction | None
    angle_end: Fraction | None
    speed_min: Fraction | None
    speed_max: Fraction | None
    duration_seconds: Fraction
    denominator_seconds: Fraction

    @property
    def fraction(self) -> Fraction:
        return self.duration_seconds / self.denominator_seconds


@dataclass(frozen=True, slots=True)
class Wind:
    mean_speed: Fraction | None
    maximum_gust: Fraction | None
    direction_degrees: float | None
    direction_state: Literal["directional", "variable", "calm", "unavailable"]
    resultant: float | None
    speed_coverage: Coverage
    direction_coverage: Coverage
    rose: tuple[RoseBin, ...]
    algorithm_revision: str
    evidence_revision: str
    evidence_ids: tuple[str, ...]
    aggregate_id: str


def _paired_cells(
    left: tuple[Cell, ...], right: tuple[Cell, ...]
) -> Iterable[tuple[Interval, Cell, Cell]]:
    i, j = 0, 0
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        overlap = a.interval.intersect(b.interval)
        if overlap:
            yield overlap, a, b
        if a.interval.end <= b.interval.end:
            i += 1
        if b.interval.end <= a.interval.end:
            j += 1


def wind(
    window: Interval,
    speeds: Iterable[Support],
    directions: Iterable[Support],
    gusts: Iterable[Support] = (),
    *,
    evidence_revision: str,
    calm_speed: Number = Decimal("0.5"),
    weak_resultant: Number = Decimal("0.1"),
) -> Wind:
    """Wind-rose duration is a full-window partition, independent of resultant direction."""
    calm, weak = _number(calm_speed), _number(weak_resultant)
    if not 0 < calm < 2 or not 0 < weak <= 1:
        raise ValueError("Invalid versioned wind policy")
    speed_items, direction_items, gust_items = _unique(speeds), _unique(directions), _unique(gusts)
    for items, is_direction in ((speed_items, False), (gust_items, False), (direction_items, True)):
        for sample in items:
            if sample.quality == "valid" and sample.value is not None:
                value = _number(sample.value)
                if value < 0 or (is_direction and value > 360):
                    raise ValueError("Wind input is outside its canonical range")
    speed_parts, direction_parts = cells(window, speed_items), cells(window, direction_items)
    speed_summary = scalar(
        window,
        speed_items,
        metric="wind_avg",
        unit="m/s",
        statistic="mean",
        evidence_revision=evidence_revision,
    )
    gust_summary = scalar(
        window,
        gust_items,
        metric="wind_gust",
        unit="m/s",
        statistic="max",
        evidence_revision=evidence_revision,
    )
    edges = (calm, Fraction(2), Fraction(5), Fraction(10), Fraction(20))
    totals: dict[tuple[int, int] | str, Fraction] = defaultdict(Fraction)
    east, north = [], []
    vector_weight = Fraction()
    direction_intervals = []
    for interval, speed, direction in _paired_cells(speed_parts, direction_parts):
        if speed.kind != "valid" or speed.value is None:
            totals["missing"] += interval.seconds
        elif speed.value < calm:
            totals["calm"] += interval.seconds
        elif direction.kind == "variable":
            totals["variable"] += interval.seconds
        elif direction.kind == "valid" and direction.value is not None:
            angle = direction.value % 360
            sector = int((angle + Fraction(45, 4)) // Fraction(45, 2)) % 16
            bin_index = max(index for index, edge in enumerate(edges) if speed.value >= edge)
            totals[(sector, bin_index)] += interval.seconds
            weight = speed.value * interval.seconds
            radians = math.radians(float(angle))
            east.append(float(weight) * math.sin(radians))
            north.append(float(weight) * math.cos(radians))
            vector_weight += weight
            direction_intervals.append(interval)
        else:
            totals["missing"] += interval.seconds
    direction_value, resultant = None, None
    state: Literal["directional", "variable", "calm", "unavailable"]
    if vector_weight:
        e, n = math.fsum(east), math.fsum(north)
        resultant = min(1.0, math.hypot(e, n) / float(vector_weight))
        if resultant < float(weak):
            state = "variable"
        else:
            state = "directional"
            direction_value = math.degrees(math.atan2(e, n)) % 360
            if direction_value >= 360 - 1e-12 or abs(direction_value) < 1e-12:
                direction_value = 0.0
    elif totals["variable"]:
        state = "variable"
    elif totals["calm"] and totals["calm"] == speed_summary.coverage.support_seconds:
        state = "calm"
    else:
        state = "unavailable"
    bins = []
    for sector in range(16):
        for index, lower in enumerate(edges):
            angle_start = (Fraction(45, 2) * sector - Fraction(45, 4)) % 360
            angle_end = (Fraction(45, 2) * sector + Fraction(45, 4)) % 360
            bins.append(
                RoseBin(
                    "directional",
                    angle_start,
                    angle_end,
                    lower,
                    edges[index + 1] if index + 1 < len(edges) else None,
                    totals[(sector, index)],
                    window.seconds,
                )
            )
    for category in ("calm", "variable", "missing"):
        bins.append(RoseBin(category, None, None, None, None, totals[category], window.seconds))
    if sum((item.duration_seconds for item in bins), Fraction()) != window.seconds:
        raise RuntimeError("Wind-rose partition failed conservation")
    all_items = speed_items + direction_items + gust_items
    roles = (
        ("speed",) * len(speed_items)
        + ("direction",) * len(direction_items)
        + ("gust",) * len(gust_items)
    )
    algorithm, identity, evidence = _identity(
        "wind",
        "summary",
        "m/s",
        window,
        all_items,
        evidence_revision,
        {
            "formula": "speed_weighted_vector_and_duration_rose_v1",
            "calm_mps": str(calm),
            "weak_resultant": str(weak),
            "speed_edges": [str(edge) for edge in edges],
            "sectors": 16,
            "numeric_north_epsilon_degrees": "1e-12",
        },
        input_roles=roles,
    )
    return Wind(
        speed_summary.value,
        gust_summary.value,
        direction_value,
        state,
        resultant,
        speed_summary.coverage,
        coverage(window, direction_intervals),
        tuple(bins),
        algorithm,
        evidence_revision,
        evidence,
        identity,
    )


@dataclass(frozen=True, slots=True)
class PressureTrend:
    aggregate: Scalar
    first_coverage: Coverage
    last_coverage: Coverage
    midpoint_separation_seconds: Fraction | None


def pressure_trend(
    window: Interval,
    samples: Iterable[Support],
    *,
    endpoint_seconds: Number,
    minimum_endpoint_fraction: Number,
    evidence_revision: str,
) -> PressureTrend:
    """Station-pressure change per hour over the actual supported endpoint midpoints."""
    width, minimum = _number(endpoint_seconds), _number(minimum_endpoint_fraction)
    ticks = width * MICROS
    if width <= 0 or ticks.denominator != 1 or width * 2 > window.seconds or not 0 < minimum <= 1:
        raise ValueError("Pressure endpoint policy requires bounded nonoverlapping windows")
    items = _unique(samples)
    first = Interval(window.start, window.start + timedelta(microseconds=ticks.numerator))
    last = Interval(window.end - timedelta(microseconds=ticks.numerator), window.end)
    a = scalar(
        first,
        items,
        metric="pressure",
        unit="hPa",
        statistic="mean",
        evidence_revision=evidence_revision,
    )
    b = scalar(
        last,
        items,
        metric="pressure",
        unit="hPa",
        statistic="mean",
        evidence_revision=evidence_revision,
    )
    overall = coverage(
        window, (part.interval for part in cells(window, items) if part.kind == "valid")
    )

    def midpoint(interval: Interval) -> Fraction:
        parts = [part for part in cells(interval, items) if part.kind == "valid"]
        return sum(
            (
                Fraction(
                    _duration(part.interval.start - window.start)
                    + _duration(part.interval.end - window.start),
                    2,
                )
                * part.interval.microseconds
                for part in parts
            ),
            Fraction(),
        ) / sum(part.interval.microseconds for part in parts)

    value, separation = None, None
    if (
        a.value is not None
        and b.value is not None
        and a.coverage.fraction >= minimum
        and b.coverage.fraction >= minimum
    ):
        separation = (midpoint(last) - midpoint(first)) / MICROS
        if separation > 0:
            value = (b.value - a.value) * 3600 / separation
    algorithm, identity, evidence = _identity(
        "pressure",
        "trend",
        "hPa/h",
        window,
        items,
        evidence_revision,
        {
            "formula": "station_pressure_supported_endpoint_trend_v1",
            "endpoint_seconds": str(width),
            "minimum_endpoint_fraction": str(minimum),
        },
    )
    result = Scalar(
        value,
        "hPa/h",
        overall,
        None if value is not None else "insufficient_endpoint_support",
        algorithm,
        evidence_revision,
        evidence,
        identity,
    )
    return PressureTrend(result, a.coverage, b.coverage, separation)
