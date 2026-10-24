# SPDX-License-Identifier: MIT
"""UTC normalization and exact microsecond-to-rational conversion."""

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from .errors import ScheduleError

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError("An instant must carry a timezone offset")
    return value.astimezone(UTC)


def duration_seconds(value: timedelta) -> Fraction:
    micros = (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
    return Fraction(micros, 1000000)


def epoch_seconds(value: datetime) -> Fraction:
    return duration_seconds(as_utc(value) - EPOCH)


def require_date(value: date) -> date:
    if type(value) is not date:
        raise ScheduleError("A local calendar date is required")
    return value
