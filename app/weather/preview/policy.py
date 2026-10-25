# SPDX-License-Identifier: MIT
"""P14 preview timing, shared by the producer, the pointer and the bytes route.

A frame is publishable once `capture_upper_bound + 600 s + skew_margin <= now`; the database
also enforces `public_not_before >= capture_upper_bound + 630 s`. Unknown, future, reversed or
implausibly wide bounds are never eligible. Age is `now - capture_lower_bound`, the older bound:
fresh through 1,200 s, stale through 1,800 s, then unavailable. Lifetime is half-open:
`expires_at = capture_lower_bound + 3,600 s` is already denied.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

MIN_DELAY = timedelta(seconds=600)
DEFAULT_SKEW = timedelta(seconds=30)
# The database floor: 600 s plus the default skew margin.
EMBARGO = MIN_DELAY + DEFAULT_SKEW
FRESH = timedelta(seconds=1200)
STALE = timedelta(seconds=1800)
RETENTION = timedelta(seconds=3600)
REFRESH_SECONDS = 60
# A snapshot request lasts at most 5 s; a calibrated cache allowance widens the lower bound.
# Anything wider than this is not a plausible capture interval.
MAX_SPAN = timedelta(seconds=300)

State = Literal["fresh", "stale", "unavailable"]


def plausible(lower: datetime | None, upper: datetime | None, now: datetime) -> bool:
    """Known, ordered, narrow bounds that are not in the future."""
    return (
        lower is not None
        and upper is not None
        and lower <= upper
        and upper - lower <= MAX_SPAN
        and upper <= now
    )


def publishable(
    lower: datetime | None, upper: datetime | None, now: datetime, skew: timedelta = DEFAULT_SKEW
) -> bool:
    """The producer's test: eligible now, and young enough to be worth publishing."""
    if not plausible(lower, upper, now) or skew < DEFAULT_SKEW:
        return False
    assert lower is not None and upper is not None
    return upper + MIN_DELAY + skew <= now and now - lower <= STALE


@dataclass(frozen=True)
class Timing:
    captured_lower: datetime
    captured_upper: datetime
    public_not_before: datetime
    first_published_at: datetime
    expires_at: datetime


def servable(timing: Timing, now: datetime) -> bool:
    """Request-time authorization of a stored row, with the database clock as `now`."""
    return (
        plausible(timing.captured_lower, timing.captured_upper, now)
        and timing.public_not_before >= timing.captured_upper + EMBARGO
        and timing.public_not_before <= now
        and timing.first_published_at <= now
        and now < timing.expires_at
        and timing.expires_at <= timing.captured_lower + RETENTION
    )


def state(lower: datetime, now: datetime) -> State:
    age = now - lower
    if age <= FRESH:
        return "fresh"
    if age <= STALE:
        return "stale"
    return "unavailable"
