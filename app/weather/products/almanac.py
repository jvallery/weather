# SPDX-License-Identifier: MIT
"""Local almanac products over #14's on-read aggregates (#42); no storage, HTTP or network access.

Support, coverage, sums and wind classification come from the shared aggregate engine
(weather.api.source and weather.api.service.summarize). This module only ranks, labels and
projects those results, and derives sun and moon times through the shared Astral wrapper (#10).
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime

import astral
from astral import Observer
from astral import moon as astral_moon
from astral import sun as astral_sun

from weather.api.models import (
    Aggregate,
    AlmanacReason,
    AstronomyItem,
    ComparisonItem,
    ComparisonWindow,
    Coverage,
    MoonPhase,
    RainDayItem,
    RecordItem,
    WindRoseBinItem,
    rounded,
)
from weather.api.source import SPEED_EDGES
from weather.timemodel import (
    IANA_VERSION,
    TZDATA_VERSION,
    ScheduleError,
    SiteConfig,
    WeatherDay,
    site_timezone,
    solar_cycle,
)

FORMULA_REVISION = "almanac-v1"
LIBRARY_REVISION = f"astral {astral.__version__}; tzdata {TZDATA_VERSION} (IANA {IANA_VERSION})"
# Versioned record policy: only windows with at least 95% metric-duration coverage are ranked.
RECORD_COVERAGE = 0.95
SECTORS = 16
MOON_PHASES: tuple[MoonPhase, ...] = (
    "new",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
)


def local_date(instant: datetime) -> date:
    return instant.astimezone(site_timezone()).date()


def _reasons(aggregate: Aggregate, history_start: datetime | None) -> list[AlmanacReason]:
    reasons: list[AlmanacReason] = []
    if history_start is None or aggregate.end <= history_start:
        reasons.append("before_history")
    if aggregate.value is None:
        reasons.append("no_observations")
    elif aggregate.coverage.fraction < 1:
        reasons.append("partial_coverage")
    return reasons


def eligible(aggregate: Aggregate) -> bool:
    return aggregate.value is not None and aggregate.coverage.fraction >= RECORD_COVERAGE


def record_items(
    aggregates: Sequence[Aggregate], *, descending: bool, history_start: datetime | None
) -> list[RecordItem]:
    """Every day stays browseable; competition ranks (1, 1, 3) cover qualifying days only."""
    ranked = sorted(
        (item for item in aggregates if eligible(item)),
        key=lambda item: item.value or 0.0,
        reverse=descending,
    )
    ranks: dict[datetime, int] = {}
    previous: float | None = None
    rank = 0
    for index, item in enumerate(ranked, 1):
        if item.value != previous:
            rank = index
        ranks[item.start] = rank
        previous = item.value
    result = []
    for item in aggregates:
        reasons = _reasons(item, history_start)
        if item.value is not None and not eligible(item):
            reasons.append("below_record_coverage")
        result.append(
            RecordItem(
                local_date=local_date(item.start),
                start=item.start,
                end=item.end,
                value=item.value,
                coverage=item.coverage,
                evidence_count=item.evidence_count,
                evidence_hash=item.evidence_hash,
                ranking_eligible=item.start in ranks,
                rank=ranks.get(item.start),
                missing_reasons=reasons,
            )
        )
    return result


def records_label(history_start: datetime | None, interval_start: datetime) -> str:
    if history_start is None:
        return "No station history yet; there are no observed extremes to rank"
    label = f"Observed extremes within station history since {local_date(history_start)}"
    if history_start < interval_start:
        label += ", ranked within the selected dates"
    return label


def rain_day_items(
    aggregates: Sequence[Aggregate], *, history_start: datetime | None
) -> list[RainDayItem]:
    """The observed total and its coverage; a partial day is never scaled up to a full day."""
    return [
        RainDayItem(
            local_date=local_date(item.start),
            start=item.start,
            end=item.end,
            value=item.value,
            coverage=item.coverage,
            evidence_count=item.evidence_count,
            evidence_hash=item.evidence_hash,
            complete=item.value is not None and item.coverage.fraction >= 1,
            missing_reasons=_reasons(item, history_start),
        )
        for item in aggregates
    ]


def rose_items(aggregate: Aggregate) -> list[WindRoseBinItem]:
    """Project #14's rose onto the full 16-sector x speed-bin grid plus calm/variable/missing.

    Classification stays in the shared SQL; this only fills empty cells with zero duration so
    clients get structured bins and never parse labels. Nothing is renormalized.
    """
    denominator = aggregate.coverage.expected_seconds
    directional: dict[tuple[float | None, float | None], float] = {}
    others: dict[str, float] = {}
    for rose_bin in aggregate.rose or []:
        if rose_bin.category == "directional":
            key = (rose_bin.sector_start, rose_bin.speed_min)
            directional[key] = directional.get(key, 0.0) + rose_bin.duration_seconds
        else:
            seconds = others.get(rose_bin.category, 0.0)
            others[rose_bin.category] = seconds + rose_bin.duration_seconds
    items = []
    for sector in range(SECTORS):
        start, end = (sector * 22.5 - 11.25) % 360, sector * 22.5 + 11.25
        for index, low in enumerate(SPEED_EDGES):
            high = SPEED_EDGES[index + 1] if index + 1 < len(SPEED_EDGES) else None
            items.append(
                WindRoseBinItem(
                    category="directional",
                    sector_start=start,
                    sector_end=end,
                    speed_min=low,
                    speed_max=high,
                    duration_seconds=directional.pop((start, low), 0.0),
                    denominator_seconds=denominator,
                )
            )
    if directional:
        raise ValueError("A wind-rose bin falls outside the contract sectors and speed bins")
    for category in ("calm", "variable", "missing"):
        items.append(
            WindRoseBinItem(
                category=category,
                sector_start=None,
                sector_end=None,
                speed_min=None,
                speed_max=None,
                duration_seconds=others.get(category, 0.0),
                denominator_seconds=denominator,
            )
        )
    return items


def comparison_window(whole: Aggregate, daily: Sequence[Aggregate]) -> ComparisonWindow:
    """Actual duration, coverage and missing days; no interpolation or rescaling."""
    adequate = eligible(whole)
    reasons: list[AlmanacReason] = []
    if whole.value is None:
        reasons.append("no_observations")
    elif not adequate:
        reasons.append("inadequate_coverage")
    if whole.value is not None and whole.coverage.fraction < 1:
        reasons.append("partial_coverage")
    return ComparisonWindow(
        start=whole.start,
        end=whole.end,
        duration_seconds=whole.coverage.expected_seconds,
        value=whole.value,
        coverage=whole.coverage,
        evidence_count=whole.evidence_count,
        evidence_hash=whole.evidence_hash,
        adequate=adequate,
        missing_days=[local_date(d.start) for d in daily if d.coverage.support_seconds <= 0],
        partial_days=[
            local_date(d.start)
            for d in daily
            if d.coverage.support_seconds > 0 and d.coverage.fraction < 1
        ],
        missing_reasons=reasons,
    )


def compare(first: ComparisonWindow, second: ComparisonWindow) -> ComparisonItem:
    difference = None
    if first.adequate and second.adequate:
        assert first.value is not None and second.value is not None
        difference = rounded(second.value - first.value)
    return ComparisonItem(
        first=first,
        second=second,
        difference=difference,
        reason=None if difference is not None else "inadequate_coverage",
    )


def combined_coverage(aggregates: Sequence[Aggregate]) -> Coverage | None:
    expected = sum(item.coverage.expected_seconds for item in aggregates)
    if expected <= 0:
        return None
    support = min(expected, sum(item.coverage.support_seconds for item in aggregates))
    return Coverage(
        fraction=round(support / expected, 6),
        support_seconds=round(support, 6),
        expected_seconds=expected,
    )


def combined_evidence(aggregates: Sequence[Aggregate]) -> tuple[int, str | None]:
    """Sum of per-window counts and a SHA-256 over the per-window hashes, in window order."""
    count = sum(item.evidence_count for item in aggregates)
    if count == 0:
        return 0, None
    joined = "\n".join(item.evidence_hash or "-" for item in aggregates)
    return count, hashlib.sha256(joined.encode()).hexdigest()


def moon_phase_name(phase: float) -> MoonPhase:
    """Eight equal phase sectors centred on new, quarters and full (Astral's 0-28 scale)."""
    return MOON_PHASES[int(phase / 3.5 + 0.5) % len(MOON_PHASES)]


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


def _moon_event(event: str, observer: Observer, day: date) -> datetime | None:
    function = astral_moon.moonrise if event == "rise" else astral_moon.moonset
    try:
        return _utc(function(observer, day, tzinfo=site_timezone()))
    except ValueError:
        return None


def astronomy_day(day: WeatherDay, site: SiteConfig | None) -> AstronomyItem:
    """Civil dawn/dusk, sunrise and sunset are exactly the shared solar_cycle (#10) values."""
    phase = astral_moon.phase(day.local_date)
    events: dict[str, datetime | None] = dict.fromkeys(
        ("civil_dawn", "sunrise", "solar_noon", "sunset", "civil_dusk", "moonrise", "moonset")
    )
    reasons: list[AlmanacReason] = []
    if site is None:
        reasons.append("site_not_configured")
    else:
        # The same observer the wrapper builds: private position, horizon at elevation 0.
        observer = Observer(site.latitude, site.longitude, elevation=0)
        try:
            cycle = solar_cycle(day.local_date, site)
            noon = astral_sun.noon(observer, day.local_date, tzinfo=site_timezone())
            events.update(
                civil_dawn=cycle.dawn,
                sunrise=cycle.sunrise,
                solar_noon=_utc(noon),
                sunset=cycle.sunset,
                civil_dusk=cycle.dusk,
            )
        except ScheduleError, ValueError:
            reasons.append("solar_events_unavailable")
        events["moonrise"] = _moon_event("rise", observer, day.local_date)
        events["moonset"] = _moon_event("set", observer, day.local_date)
        if events["moonrise"] is None:
            reasons.append("no_moonrise")
        if events["moonset"] is None:
            reasons.append("no_moonset")
    sunrise, sunset = events["sunrise"], events["sunset"]
    length = (sunset - sunrise).total_seconds() if sunrise and sunset else None
    return AstronomyItem(
        local_date=day.local_date,
        start=day.start,
        end=day.end,
        civil_dawn=events["civil_dawn"],
        sunrise=sunrise,
        solar_noon=events["solar_noon"],
        sunset=sunset,
        civil_dusk=events["civil_dusk"],
        day_length_seconds=length,
        moon_phase=round(phase, 3) % 28,
        moon_phase_name=moon_phase_name(phase),
        moonrise=events["moonrise"],
        moonset=events["moonset"],
        missing_reasons=reasons,
    )


def astronomy_items(days: Sequence[WeatherDay], site: SiteConfig | None) -> list[AstronomyItem]:
    return [astronomy_day(day, site) for day in days]
