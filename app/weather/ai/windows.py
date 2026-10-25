# SPDX-License-Identifier: MIT
"""Analysis windows: which output frames of a segment belong to each UTC hour (exact rationals).

Frame k of N over a segment [a, b) represents source time a + (k + 1/2)·step with
step = (b - a)/N. A window [s, e) owns the frames whose centre lies in it, so consecutive
windows tile [0, N) exactly once. A window that owns no frame is `unavailable`: it never borrows
an adjacent frame. Golden vectors: docs/contracts/analysis-window-vectors.json.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction

FPS = 60
# Sampled frames per window (issue #23: at most eight web-rendition frames per window).
SAMPLES = 8


@dataclass(frozen=True)
class Window:
    index: int
    start: datetime
    end: datetime
    first_frame: int
    end_frame: int

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.first_frame

    @property
    def available(self) -> bool:
        return self.frame_count > 0

    @property
    def pts(self) -> tuple[Fraction, Fraction]:
        return Fraction(self.first_frame, FPS), Fraction(self.end_frame, FPS)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def _micros(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def frame_ranges(duration: Fraction, frames: int, cuts: list[Fraction]) -> list[tuple[int, int]]:
    """Half-open frame ranges for the windows between consecutive cuts of [0, duration)."""
    if duration <= 0 or frames < 1:
        raise ValueError("A segment needs a positive duration and at least one frame")
    edges = [Fraction(0), *cuts, duration]
    if any(not lo < hi for lo, hi in zip(edges, edges[1:], strict=False)):
        raise ValueError("Cuts must increase strictly inside the segment")
    step = duration / frames

    def first_at(offset: Fraction) -> int:
        # The smallest k with (k + 1/2)·step >= offset.
        return min(max(math.ceil(offset / step - Fraction(1, 2)), 0), frames)

    return [(first_at(lo), first_at(hi)) for lo, hi in zip(edges, edges[1:], strict=False)]


def hour_windows(start: datetime, end: datetime, frames: int) -> list[Window]:
    """Windows split at UTC hour boundaries strictly inside [start, end)."""
    if start.tzinfo is None or end.tzinfo is None or not start < end:
        raise ValueError("A segment needs an aware, non-empty interval")
    bounds = [start]
    hour = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while hour < end:
        bounds.append(hour)
        hour += timedelta(hours=1)
    bounds.append(end)
    duration = Fraction(_micros(end - start), 1_000_000)
    cuts = [Fraction(_micros(edge - start), 1_000_000) for edge in bounds[1:-1]]
    ranges = frame_ranges(duration, frames, cuts)
    return [
        Window(index, lo, hi, first, last)
        for index, ((lo, hi), (first, last)) in enumerate(
            zip(zip(bounds, bounds[1:], strict=False), ranges, strict=True)
        )
    ]


def samples(window: Window, count: int = SAMPLES) -> list[int]:
    """Up to `count` frames spread evenly across the window, each the centre of its stratum."""
    total = window.frame_count
    taken = min(count, total)
    return [window.first_frame + (2 * i + 1) * total // (2 * taken) for i in range(taken)]
