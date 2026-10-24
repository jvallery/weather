# SPDX-License-Identifier: MIT
"""Local calendar days and distinct UTC-hour identities through Denver DST."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from ._instant import as_utc, require_date
from .errors import ScheduleError
from .site import site_timezone

HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class WeatherDay:
    local_date: date

    def __post_init__(self) -> None:
        require_date(self.local_date)
        if self.local_date == date.max:
            raise ScheduleError("The next local calendar day is outside the date range")

    @property
    def start(self) -> datetime:
        return datetime.combine(self.local_date, time.min, site_timezone()).astimezone(UTC)

    @property
    def end(self) -> datetime:
        following = self.local_date + timedelta(days=1)
        return datetime.combine(following, time.min, site_timezone()).astimezone(UTC)


@dataclass(frozen=True)
class HourSlot:
    start: datetime
    weather_day: WeatherDay

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start))
        if self.start.minute or self.start.second or self.start.microsecond:
            raise ScheduleError("A slot must start at a UTC hour")
        if not self.weather_day.start <= self.start < self.end <= self.weather_day.end:
            raise ScheduleError("An hour slot cannot belong to another calendar day")

    @property
    def end(self) -> datetime:
        return self.start + HOUR

    @property
    def local_label(self) -> str:
        return self.start.astimezone(site_timezone()).isoformat()

    @property
    def fold(self) -> int:
        return self.start.astimezone(site_timezone()).fold


def weather_day(local_date: date) -> WeatherDay:
    return WeatherDay(local_date)


def hour_slots(local_date: date) -> tuple[HourSlot, ...]:
    day = weather_day(local_date)
    starts = range(int((day.end - day.start) // HOUR))
    return tuple(HourSlot(day.start + index * HOUR, day) for index in starts)


def day_for(instant: datetime) -> WeatherDay:
    try:
        local_date = as_utc(instant).astimezone(site_timezone()).date()
    except OverflowError:
        raise ScheduleError("Instant is outside the supported local calendar range") from None
    return weather_day(local_date)


def slot_for(instant: datetime) -> HourSlot:
    utc = as_utc(instant)
    return HourSlot(utc.replace(minute=0, second=0, microsecond=0), day_for(utc))
