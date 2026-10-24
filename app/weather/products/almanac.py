# SPDX-License-Identifier: MIT
"""Pure almanac products over WX-13 computations; no storage or HTTP access.

The caller supplies one effective publication snapshot. Support qualification,
packet deduplication, sums and wind classification remain in the shared engine.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from fractions import Fraction
from typing import Literal

from weather.api.aggregate_adapter import Computation
from weather.api.public_common import Number, OpaqueID, PublicModel, Reason
from weather.api.public_models import Aggregate, Interval, WindRoseBin
from weather.products.aggregate import Wind
from weather.timemodel import site_timezone, weather_day
from weather.timemodel.site import TIMEZONE

FORMULA_REVISION = "almanac_calendar_records_v1"
RECORD_MINIMUM = Fraction(95, 100)
Period = Literal["day", "month", "year"]


def period_interval(start: date, period: Period) -> Interval:
    """Calendar periods use the shared pinned timezone, never fixed 24-hour days."""
    if period == "day":
        end = start + timedelta(days=1)
    elif period == "month" and start.day == 1:
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    elif period == "year" and start.month == start.day == 1:
        end = date(start.year + 1, 1, 1)
    else:
        raise ValueError("A calendar period requires its actual local start date")
    return Interval(start=weather_day(start).start, end=weather_day(end).start)


def _fraction(computation: Computation) -> Fraction:
    return computation.exact.coverage.fraction


def _unique(items: Sequence[Computation]) -> tuple[Computation, ...]:
    windows: dict[tuple[object, object], Computation] = {}
    for item in items:
        key = (item.aggregate.interval.start, item.aggregate.interval.end)
        prior = windows.get(key)
        if prior is not None and prior != item:
            raise ValueError("One effective aggregate per window is required")
        windows[key] = item
    return tuple(sorted(windows.values(), key=lambda item: item.aggregate.interval.start))


def _same_quantity(items: Sequence[Computation]) -> None:
    if len({(i.aggregate.metric, i.aggregate.statistic, i.aggregate.unit) for i in items}) > 1:
        raise ValueError("Almanac windows must select the same metric, statistic and unit")


class RecordObservation(PublicModel):
    aggregate: Aggregate
    aggregate_id: OpaqueID
    ranking_eligible: bool
    rank: int | None
    reason: Reason | None


class Records(PublicModel):
    history_interval: Interval | None
    timezone: str = TIMEZONE
    formula_revision: str = FORMULA_REVISION
    label: str
    observations: tuple[RecordObservation, ...]


def rank_records(
    items: Sequence[Computation], *, history_interval: Interval | None, period: Period = "day"
) -> Records:
    """Keep thin windows browseable; competition ranks include qualifying ties."""
    unique = _unique(items)
    _same_quantity(unique)
    for item in unique:
        aggregate = item.aggregate
        local_start = aggregate.interval.start.astimezone(site_timezone()).date()
        if aggregate.interval != period_interval(local_start, period):
            raise ValueError("Records must use complete local calendar windows")
        if aggregate.statistic not in {"min", "max"}:
            raise ValueError("Record rankings require observed minimum or maximum values")
        if aggregate.value is not None and (
            history_interval is None
            or not (
                aggregate.interval.start < history_interval.end
                and history_interval.start < aggregate.interval.end
            )
        ):
            raise ValueError("Observed records must overlap the supplied station history")
    eligible = [
        item
        for item in unique
        if item.exact.value is not None and _fraction(item) >= RECORD_MINIMUM
    ]
    descending = bool(unique and unique[0].aggregate.statistic == "max")

    def numeric_key(item: Computation) -> Fraction:
        value = item.exact.value
        assert value is not None
        return value

    eligible.sort(key=numeric_key, reverse=descending)
    ranks: dict[str, int] = {}
    previous: Fraction | None = None
    rank = 0
    for index, item in enumerate(eligible, 1):
        if index == 1 or item.exact.value != previous:
            rank = index
        ranks[item.aggregate_id] = rank
        previous = item.exact.value
    observations = tuple(
        RecordObservation(
            aggregate=item.aggregate,
            aggregate_id=item.aggregate_id,
            ranking_eligible=item.aggregate_id in ranks,
            rank=ranks.get(item.aggregate_id),
            reason=(
                None
                if item.aggregate_id in ranks
                else item.aggregate.reason or "below_95_percent_metric_duration_coverage"
            ),
        )
        for item in unique
    )
    since = (
        history_interval.start.astimezone(site_timezone()).date().isoformat()
        if history_interval is not None
        else None
    )
    return Records(
        history_interval=history_interval,
        label=(
            "observed extremes within station history since " + since
            if since
            else "no station history available"
        ),
        observations=observations,
    )


class RainDay(PublicModel):
    weather_day: date
    aggregate: Aggregate
    aggregate_id: OpaqueID
    label: Literal["observed total"] = "observed total"
    complete: bool
    missing_reasons: tuple[Reason, ...]


def rain_calendar(items: Sequence[Computation]) -> tuple[RainDay, ...]:
    result = []
    for item in _unique(items):
        aggregate = item.aggregate
        day = aggregate.interval.start.astimezone(site_timezone()).date()
        if (aggregate.metric, aggregate.statistic) != ("precipitation", "observed_accumulation"):
            raise ValueError("Rain calendar requires qualified observed accumulation")
        if aggregate.interval != period_interval(day, "day"):
            raise ValueError("Rain rows must use actual local weather-day bounds")
        reasons = tuple(
            filter(
                None,
                (aggregate.reason, "partial_weather_day" if _fraction(item) < 1 else None),
            )
        )
        result.append(
            RainDay(
                weather_day=day,
                aggregate=aggregate,
                aggregate_id=item.aggregate_id,
                complete=_fraction(item) == 1,
                missing_reasons=reasons,
            )
        )
    return tuple(result)


class ComparisonWindow(PublicModel):
    aggregate: Aggregate
    aggregate_id: OpaqueID
    duration_seconds: float
    missing_days: tuple[date, ...]
    adequate: bool


def _comparison_window(item: Computation) -> ComparisonWindow:
    aggregate = item.aggregate
    missing: set[date] = set()
    for gap in aggregate.coverage.gaps:
        day = gap.start.astimezone(site_timezone()).date()
        while weather_day(day).start < gap.end:
            missing.add(day)
            day += timedelta(days=1)
    return ComparisonWindow(
        aggregate=aggregate,
        aggregate_id=item.aggregate_id,
        duration_seconds=aggregate.interval.seconds,
        missing_days=tuple(sorted(missing)),
        adequate=item.exact.value is not None and _fraction(item) >= RECORD_MINIMUM,
    )


class Comparison(PublicModel):
    formula_revision: str = FORMULA_REVISION
    first: ComparisonWindow
    second: ComparisonWindow
    # second - first, with no duration normalization or missing-data rescaling.
    difference: Number | None
    reason: Reason | None


def compare_periods(first: Computation, second: Computation) -> Comparison:
    _same_quantity((first, second))
    a, b = _comparison_window(first), _comparison_window(second)
    difference: Number | None = None
    if a.adequate and b.adequate:
        assert first.exact.value is not None and second.exact.value is not None
        exact_difference = second.exact.value - first.exact.value
        difference = (
            exact_difference.numerator
            if exact_difference.denominator == 1
            else float(exact_difference)
        )
    return Comparison(
        first=a,
        second=b,
        difference=difference,
        reason=None if difference is not None else "inadequate_metric_duration_coverage",
    )


def wind_rose_bins(computation: Computation) -> tuple[WindRoseBin, ...]:
    """Project the shared classifier's partition; never run a second wind recipe."""
    wind = computation.details
    if not isinstance(wind, Wind):
        raise ValueError("Wind rose requires the shared wind computation and its evidence")
    return tuple(
        WindRoseBin(
            category=row.category,
            angle_start=None if row.angle_start is None else float(row.angle_start),
            angle_end=None if row.angle_end is None else float(row.angle_end),
            speed_min=None if row.speed_min is None else float(row.speed_min),
            speed_max=None if row.speed_max is None else float(row.speed_max),
            duration_seconds=float(row.duration_seconds),
            denominator_seconds=float(row.denominator_seconds),
        )
        for row in wind.rose
    )
