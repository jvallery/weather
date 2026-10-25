# SPDX-License-Identifier: MIT
"""Exact observed-exposure geometry (D10 v1): rational time, half-open intervals, no rounding.

Output frame k of a segment [a, b) with N frames has source centre a + (k + 1/2)(b - a)/N and a
synthetic shutter of width step * theta / 360 centred there. A real capture at t supports
[max(t - d/2, midpoint to previous, a), min(t + d/2, midpoint to next, b)), where d is the
expected capture interval: a capture never covers more than half an interval on either side,
so missed slots leave holes instead of stretching a neighbour. Supports never overlap, so the
covered part of a shutter is a plain sum. Coverage is that sum over the intended width.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor

from weather.timemodel import FrameMap

Interval = tuple[Fraction, Fraction]


@dataclass(frozen=True)
class Geometry:
    start: Fraction
    end: Fraction
    frames: int
    interval: Fraction
    shutter_degrees: int

    def __post_init__(self) -> None:
        if not self.start < self.end or not 0 < self.frames <= 7200:
            raise ValueError("Invalid segment geometry")
        if self.interval <= 0 or not 0 < self.shutter_degrees <= 360:
            raise ValueError("Invalid capture interval or shutter")

    @classmethod
    def of(cls, frame_map: FrameMap, interval: Fraction, shutter_degrees: int) -> Geometry:
        start = frame_map.source_center(0) - frame_map.source_step / 2
        return cls(
            start,
            start + frame_map.source_step * frame_map.frame_count,
            frame_map.frame_count,
            interval,
            shutter_degrees,
        )

    @property
    def step(self) -> Fraction:
        return (self.end - self.start) / self.frames

    @property
    def width(self) -> Fraction:
        return self.step * self.shutter_degrees / 360

    def center(self, k: int) -> Fraction:
        return self.start + (k + Fraction(1, 2)) * self.step

    def shutter(self, k: int) -> Interval:
        centre, half = self.center(k), self.width / 2
        return centre - half, centre + half

    def owned(self, first: int, last: int) -> Interval:
        """Source time owned by output frames [first, last); the blocks partition [a, b)."""
        end = self.end if last >= self.frames else self.start + last * self.step
        return self.start + first * self.step, end

    def overlapping(self, interval: Interval) -> range:
        """Output frames whose shutter intersects the half-open interval."""
        lo, hi = interval
        if hi <= lo:
            return range(0)
        half = self.width / 2
        first = floor((lo - self.start - half) / self.step - Fraction(1, 2)) + 1
        last = ceil((hi - self.start + half) / self.step - Fraction(1, 2)) - 1
        return range(max(first, 0), min(last, self.frames - 1) + 1)

    def support(
        self, time: Fraction, previous: Fraction | None, following: Fraction | None
    ) -> Interval:
        half = self.interval / 2
        lo = max(time - half, self.start)
        hi = min(time + half, self.end)
        if previous is not None:
            lo = max(lo, (previous + time) / 2)
        if following is not None:
            hi = min(hi, (time + following) / 2)
        return lo, max(lo, hi)


def overlap(left: Interval, right: Interval) -> Fraction:
    return max(Fraction(0), min(left[1], right[1]) - max(left[0], right[0]))


def supports(geometry: Geometry, times: Sequence[Fraction]) -> list[Interval]:
    """Supports of strictly increasing, in-segment capture times."""
    if any(not geometry.start <= t < geometry.end for t in times) or any(
        a >= b for a, b in zip(times, times[1:], strict=False)
    ):
        raise ValueError("Capture times must be strictly increasing inside the segment")
    return [
        geometry.support(
            time,
            times[index - 1] if index else None,
            times[index + 1] if index + 1 < len(times) else None,
        )
        for index, time in enumerate(times)
    ]


def coverage(geometry: Geometry, k: int, intervals: Sequence[Interval]) -> Fraction:
    shutter = geometry.shutter(k)
    return sum((overlap(shutter, item) for item in intervals), Fraction(0)) / geometry.width
