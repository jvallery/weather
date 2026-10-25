# SPDX-License-Identifier: MIT
"""Civil dawn-to-dawn cycles; private altitude never corrects the horizon."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from astral import Observer
from astral import sun as astral_sun

from ._instant import as_utc, duration_seconds, require_date
from .allocation import FrameAllocation, FrameMap, allocate_frames
from .errors import ScheduleError
from .site import HORIZON_MODEL, SiteConfig, site_timezone

SegmentKind = Literal["daytime", "overnight"]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _date_label(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.day}"


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    cycle_date: date
    start: datetime
    end: datetime
    frame_count: int

    def __post_init__(self) -> None:
        if self.kind not in ("daytime", "overnight"):
            raise ScheduleError("Unknown solar segment kind")
        require_date(self.cycle_date)
        mapping = FrameMap(self.start, self.end, self.frame_count)
        object.__setattr__(self, "start", mapping.start)
        object.__setattr__(self, "end", mapping.end)

    @property
    def frame_map(self) -> FrameMap:
        return FrameMap(self.start, self.end, self.frame_count)

    @property
    def local_start(self) -> str:
        return self.start.astimezone(site_timezone()).isoformat()

    @property
    def local_end(self) -> str:
        return self.end.astimezone(site_timezone()).isoformat()

    @property
    def label(self) -> str:
        first = _date_label(self.cycle_date)
        if self.kind == "daytime":
            return f"Daytime · {first}"
        last = self.end.astimezone(site_timezone()).date()
        suffix = str(last.day) if last.month == self.cycle_date.month else _date_label(last)
        return f"Overnight · {first}–{suffix}"


@dataclass(frozen=True)
class SolarCycle:
    cycle_date: date
    dawn: datetime
    dusk: datetime
    next_dawn: datetime
    sunrise: datetime
    sunset: datetime
    allocation: FrameAllocation
    config_hash: str = field(repr=False)

    def __post_init__(self) -> None:
        require_date(self.cycle_date)
        for name in ("dawn", "dusk", "next_dawn", "sunrise", "sunset"):
            object.__setattr__(self, name, as_utc(getattr(self, name)))
        if not self.dawn < self.dusk < self.next_dawn:
            raise ScheduleError("Civil boundaries are not ordered")
        if self.cycle_date != self.dawn.astimezone(site_timezone()).date():
            raise ScheduleError("Cycle date must be the local date of dawn")
        expected = allocate_frames(
            duration_seconds(self.dusk - self.dawn),
            duration_seconds(self.next_dawn - self.dusk),
        )
        if self.allocation != expected:
            raise ScheduleError("Frame allocation does not match elapsed solar durations")

    @property
    def daytime(self) -> Segment:
        return Segment("daytime", self.cycle_date, self.dawn, self.dusk, self.allocation.day_frames)

    @property
    def overnight(self) -> Segment:
        return Segment(
            "overnight",
            self.cycle_date,
            self.dusk,
            self.next_dawn,
            self.allocation.night_frames,
        )

    @property
    def segments(self) -> tuple[Segment, Segment]:
        return self.daytime, self.overnight


def solar_cycle(local_date: date, site: SiteConfig) -> SolarCycle:
    require_date(local_date)
    if site.horizon_model != HORIZON_MODEL:
        raise ScheduleError("Unsupported horizon model")
    try:
        following = local_date + timedelta(days=1)
        observer = Observer(site.latitude, site.longitude, elevation=0)
        zone = site_timezone()
        dawn = as_utc(astral_sun.dawn(observer, local_date, depression=6, tzinfo=zone))
        dusk = as_utc(astral_sun.dusk(observer, local_date, depression=6, tzinfo=zone))
        next_dawn = as_utc(astral_sun.dawn(observer, following, depression=6, tzinfo=zone))
        dawn += timedelta(seconds=site.dawn_offset_seconds)
        dusk += timedelta(seconds=site.dusk_offset_seconds)
        next_dawn += timedelta(seconds=site.dawn_offset_seconds)
        sunrise = as_utc(astral_sun.sunrise(observer, local_date, tzinfo=zone))
        sunset = as_utc(astral_sun.sunset(observer, local_date, tzinfo=zone))
        if not dawn < dusk < next_dawn:
            raise ScheduleError("Shifted civil boundaries must satisfy dawn < dusk < next dawn")
        allocation = allocate_frames(
            duration_seconds(dusk - dawn),
            duration_seconds(next_dawn - dusk),
        )
    except ScheduleError:
        # Already sanitized; keep its specific reason instead of the generic one.
        raise
    except ValueError, OverflowError:
        raise ScheduleError("Civil solar schedule is unavailable or invalid") from None
    return SolarCycle(
        cycle_date=dawn.astimezone(zone).date(),
        dawn=dawn,
        dusk=dusk,
        next_dawn=next_dawn,
        sunrise=sunrise,
        sunset=sunset,
        allocation=allocation,
        config_hash=site.config_hash,
    )
