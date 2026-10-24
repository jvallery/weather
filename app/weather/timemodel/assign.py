# SPDX-License-Identifier: MIT
"""Half-open segment ownership and conservative optional uncertainty handling."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ._instant import as_utc
from .errors import ScheduleError
from .solar import Segment

AssignmentStatus = Literal["assigned", "boundary_ambiguous", "outside_schedule"]


@dataclass(frozen=True)
class Assignment:
    status: AssignmentStatus
    segment: Segment | None


def assign_capture(
    instant: datetime,
    segments: Iterable[Segment],
    *,
    lower_bound: datetime | None = None,
    upper_bound: datetime | None = None,
    strict: bool = False,
) -> Assignment:
    """Bounds are inclusive plausible exposure times; end equality is ambiguous."""
    point = as_utc(instant)
    ordered = sorted(segments, key=lambda segment: segment.start)
    if any(left.end > right.start for left, right in zip(ordered, ordered[1:], strict=False)):
        raise ScheduleError("Solar segments overlap")
    if (lower_bound is None) != (upper_bound is None):
        raise ScheduleError("Both uncertainty bounds are required")
    lower = point if lower_bound is None else as_utc(lower_bound)
    upper = point if upper_bound is None else as_utc(upper_bound)
    if not lower <= point <= upper:
        raise ScheduleError("Exposure bounds must contain the selected instant")
    for segment in ordered:
        if segment.start <= point < segment.end:
            if strict and not segment.start <= lower <= upper < segment.end:
                return Assignment("boundary_ambiguous", None)
            return Assignment("assigned", segment)
    return Assignment("outside_schedule", None)
