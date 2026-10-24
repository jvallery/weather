# SPDX-License-Identifier: MIT
"""Exact cycle frame allocation and authoritative rational source/PTS maps."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import floor

from ._instant import as_utc, duration_seconds, epoch_seconds
from .errors import ScheduleError

TOTAL_FRAMES = 7200
FPS = Fraction(60, 1)
TIMESCALE = 60000


def _rational(value: int | Fraction) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
        raise ScheduleError("Time arithmetic requires integers or Fraction values")
    return Fraction(value)


def _fps(value: int | Fraction) -> Fraction:
    if _rational(value) != FPS:
        raise ScheduleError("Output cadence must be exactly 60/1 fps")
    return FPS


@dataclass(frozen=True)
class FrameAllocation:
    day_frames: int
    night_frames: int

    def __post_init__(self) -> None:
        if (
            type(self.day_frames) is not int
            or type(self.night_frames) is not int
            or self.day_frames <= 0
            or self.night_frames <= 0
            or self.day_frames + self.night_frames != TOTAL_FRAMES
        ):
            raise ScheduleError("Invalid cycle frame allocation")

    @property
    def day_output_seconds(self) -> Fraction:
        return Fraction(self.day_frames, 60)

    @property
    def night_output_seconds(self) -> Fraction:
        return Fraction(self.night_frames, 60)

    @property
    def day_terminal_ticks(self) -> int:
        return self.day_frames * 1000

    @property
    def night_terminal_ticks(self) -> int:
        return self.night_frames * 1000


def allocate_frames(
    day_seconds: int | Fraction,
    night_seconds: int | Fraction,
    *,
    fps: int | Fraction = FPS,
) -> FrameAllocation:
    _fps(fps)
    day, night = _rational(day_seconds), _rational(night_seconds)
    if day <= 0 or night <= 0:
        raise ScheduleError("Both solar segments must have positive duration")
    daytime = floor(TOTAL_FRAMES * day / (day + night) + Fraction(1, 2))
    if not 0 < daytime < TOTAL_FRAMES:
        raise ScheduleError("Both solar segments must receive at least one frame")
    return FrameAllocation(daytime, TOTAL_FRAMES - daytime)


@dataclass(frozen=True)
class FrameMap:
    start: datetime
    end: datetime
    frame_count: int
    fps: Fraction = FPS

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start))
        object.__setattr__(self, "end", as_utc(self.end))
        object.__setattr__(self, "fps", _fps(self.fps))
        if self.end <= self.start:
            raise ScheduleError("A source interval must be nonempty and ordered")
        if type(self.frame_count) is not int or not 0 < self.frame_count <= TOTAL_FRAMES:
            raise ScheduleError("Invalid segment frame count")

    @property
    def source_step(self) -> Fraction:
        return duration_seconds(self.end - self.start) / self.frame_count

    def _index(self, frame: int) -> int:
        if type(frame) is not int or not 0 <= frame < self.frame_count:
            raise ScheduleError("Frame index is outside the segment")
        return frame

    def source_offset(self, frame: int) -> Fraction:
        return (self._index(frame) + Fraction(1, 2)) * self.source_step

    def source_center(self, frame: int) -> Fraction:
        """Exact UTC Unix seconds; no datetime or JSON-float rounding decides ownership."""
        return epoch_seconds(self.start) + self.source_offset(frame)

    def output_pts(self, frame: int) -> Fraction:
        return Fraction(self._index(frame), 60)

    @property
    def output_duration(self) -> Fraction:
        return Fraction(self.frame_count, 60)

    @property
    def terminal_ticks(self) -> int:
        return self.frame_count * (TIMESCALE // 60)
