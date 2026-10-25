# SPDX-License-Identifier: MIT
"""Staging watermarks (D16): 70 % alert, 85 % urgent, 90 % stop, resume below 75 %.

Occupancy is `statvfs` on the staging mount, which reports the share's hard quota
(storage-infra#1106), exactly as the collectors compute it. The levels only change what runs
first; they never relax a deletion condition, so pressure can never delete pinned input.

Shedding order, cheapest loss first:

1. **alert (≥ 70 %)**: metrics only.
2. **urgent (≥ 85 %)**: the pipeline runs cleanup on every loop instead of every
   `cleanup_interval`, ahead of rendering, and `optional_allowed()` turns false. No optional
   work exists yet; any future optional re-render or reanalysis must check it.
3. **stop (≥ 90 %)**: the collectors stop writing JPEG payloads on their own statvfs check
   (`capture/collector.py`, same thresholds, independent of the pipeline and of every
   cleanup condition) and record `staging_full` gaps. The pipeline keeps rendering: rendering
   is what releases input and lets cleanup free it.
4. **render stop (≥ 97 %)**: the pipeline adds no mezzanine blocks
   (`Housekeeping.render_allowed`) but still finalizes complete mezzanines, whose render
   directories cleanup then frees. It resumes below 97 %. Nothing else writes payload bytes,
   so the share cannot fill past 97 % by more than in-flight writes.

Urgent and stop hold until occupancy is below 75 % and the last cleanup pass succeeded (the
"failed dependency" is healthy); then the level follows the ratio again.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Protocol

from prometheus_client import Gauge


class Level(IntEnum):
    NORMAL = 0
    ALERT = 1
    URGENT = 2
    STOP = 3


RATIO = Gauge(
    "weather_staging_ratio",
    "Staging occupancy (1 - f_bavail/f_blocks) seen by the pipeline; -1 when statvfs fails",
    ["camera"],
)
LEVEL = Gauge(
    "weather_staging_watermark_level",
    "Staging watermark: 0 normal, 1 alert (70%), 2 urgent (85%), 3 stop (90%)",
    ["camera"],
)


class StatVfs(Protocol):
    @property
    def f_blocks(self) -> int: ...
    @property
    def f_bavail(self) -> int: ...


@dataclass(frozen=True)
class Watermarks:
    alert: float = 0.70
    urgent: float = 0.85
    stop: float = 0.90
    resume: float = 0.75
    render_stop: float = 0.97

    def __post_init__(self) -> None:
        if not 0 < self.alert <= self.resume <= self.urgent <= self.stop < self.render_stop < 1:
            raise ValueError(
                "Watermarks must be ordered alert <= resume <= urgent <= stop < render_stop"
            )

    def by_ratio(self, ratio: float) -> Level:
        if ratio >= self.stop:
            return Level.STOP
        if ratio >= self.urgent:
            return Level.URGENT
        if ratio >= self.alert:
            return Level.ALERT
        return Level.NORMAL


DEFAULT_MARKS = Watermarks()


def occupancy(stat: StatVfs) -> float:
    if stat.f_blocks <= 0:
        raise OSError("Staging capacity unavailable")
    return 1 - stat.f_bavail / stat.f_blocks


class Pressure:
    """One camera pipeline's view of the shared staging share, with hysteresis."""

    def __init__(
        self,
        camera: str,
        staging: Path,
        *,
        marks: Watermarks = DEFAULT_MARKS,
        statvfs: Callable[[Path], StatVfs] = os.statvfs,
    ) -> None:
        self.camera, self.staging, self.marks, self.statvfs = camera, staging, marks, statvfs
        self.level = Level.NORMAL
        self.ratio: float | None = None
        LEVEL.labels(camera).set(0)

    def update(self, *, dependency_healthy: bool = True) -> Level:
        """Read statvfs once. A failed read keeps the previous level (never relaxes it)."""
        try:
            ratio = occupancy(self.statvfs(self.staging))
        except OSError:
            self.ratio = None
            RATIO.labels(self.camera).set(-1)
            raise
        self.ratio = ratio
        RATIO.labels(self.camera).set(ratio)
        target = self.marks.by_ratio(ratio)
        if self.level >= Level.URGENT and (ratio >= self.marks.resume or not dependency_healthy):
            # Hold urgent/stop until below the resume mark with a healthy cleanup.
            target = max(target, self.level)
        self.level = target
        LEVEL.labels(self.camera).set(int(target))
        return target

    @property
    def urgent(self) -> bool:
        return self.level >= Level.URGENT

    def render_allowed(self) -> bool:
        """New mezzanine blocks may be written below the render stop (or when unknown: a
        failed statvfs must not stall the renderer that frees space)."""
        return self.ratio is None or self.ratio < self.marks.render_stop

    def optional_allowed(self) -> bool:
        """Optional re-renders or reanalysis may start only below urgent pressure."""
        return self.level < Level.URGENT
