# SPDX-License-Identifier: MIT
"""Gap fill for published renditions (#144): no output frame is ever a flat dark slate.

The mezzanine keeps v1's exposure rule: an output frame below the coverage threshold is a
slate block frame, and the sidecar's `coverage.exposure.gaps` still lists those frames. Only
the renditions change. Each run of slate frames is filled from real frames:

- a run of at most `SHORT_MAX` frames between two rendered frames is a linear cross-fade
  between them (integer arithmetic, so a re-render produces the same bytes);
- any longer run, or a run at either end of the segment, holds the nearest rendered frame with
  a burned-in "Capture gap HH:MM-HH:MM" label in the site's local time.

A segment with no rendered frame at all is never published, so a fill always has a source.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from weather.timemodel.site import TIMEZONE

FILL = "gap-fill/v1"
SHORT_MAX = 12
LOCAL = ZoneInfo(TIMEZONE)


@dataclass(frozen=True)
class Run:
    first: int
    last: int  # exclusive
    mode: str  # "interpolated" or "held"

    @property
    def frames(self) -> int:
        return self.last - self.first


def plan(slates: list[bool]) -> list[Run]:
    """The fill for every slate run. Raises if no frame is rendered."""
    total = len(slates)
    if total and all(slates):
        raise ValueError("A segment without a rendered frame has nothing to fill from")
    runs: list[Run] = []
    k = 0
    while k < total:
        if not slates[k]:
            k += 1
            continue
        start = k
        while k < total and slates[k]:
            k += 1
        inner = start > 0 and k < total
        mode = "interpolated" if inner and k - start <= SHORT_MAX else "held"
        runs.append(Run(start, k, mode))
    return runs


def blend(before: np.ndarray, after: np.ndarray, step: int, steps: int) -> np.ndarray:
    """Frame `step` of `steps` interior frames between two rendered frames, rounded half up."""
    total = steps + 1
    mixed = (
        before.astype(np.uint16) * (total - step) + after.astype(np.uint16) * step + total // 2
    ) // total
    return mixed.astype(np.uint8)


def gap_label(start: datetime, end: datetime) -> str:
    first, last = start.astimezone(LOCAL), end.astimezone(LOCAL)
    return f"Capture gap {first:%H:%M}–{last:%H:%M}"


def labelled(frame: np.ndarray, text: str) -> np.ndarray:
    """The held frame with a small dark band and the label in its lower-left corner."""
    height, width, _ = frame.shape
    image = Image.fromarray(frame, "RGB")
    size = max(12, height // 36)
    font = ImageFont.load_default(size=size)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad = size // 2
    x, y = pad, height - (bottom - top) - 3 * pad
    box = (x - pad, y - pad, x + (right - left) + pad, y + (bottom - top) + pad)
    draw.rectangle(box, fill=(0, 0, 0))
    draw.text((x - left, y - top), text, font=font, fill=(235, 235, 235))
    return np.asarray(image, dtype=np.uint8)


@dataclass
class Stats:
    interpolated: int = 0
    held: int = 0


def fill(
    frames: Iterable[bytes],
    slates: list[bool],
    shape: tuple[int, int],
    span: Callable[[int, int], tuple[datetime, datetime]],
    stats: Stats | None = None,
) -> Iterator[bytes]:
    """Stream the mezzanine's RGB24 frames with every slate replaced.

    `span(first, last)` gives the source interval of output frames [first, last). Only the
    two frames around a short run are held in memory, plus one labelled frame per long run.
    """
    height, width = shape
    runs = {run.first: run for run in plan(slates)}
    stats = stats if stats is not None else Stats()
    source = iter(frames)
    total = len(slates)

    def take() -> np.ndarray:
        try:
            data = next(source)
        except StopIteration:
            raise ValueError("Mezzanine ended before its frame count") from None
        return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)

    previous: np.ndarray | None = None
    k = 0
    while k < total:
        run = runs.get(k)
        if run is None:
            previous = take()
            yield previous.tobytes()
            k += 1
            continue
        for _ in range(run.frames):
            take()  # the slates themselves are discarded
        if run.mode == "interpolated":
            assert previous is not None
            after = take()
            for step in range(1, run.frames + 1):
                yield blend(previous, after, step, run.frames).tobytes()
            stats.interpolated += run.frames
            previous = after
            yield after.tobytes()
            k = run.last + 1
            continue
        start, end = span(run.first, run.last)
        text = gap_label(start, end)
        if previous is None:
            # A leading run holds the first rendered frame, which follows it.
            after = take()
            held = labelled(after, text).tobytes()
            for _ in range(run.frames):
                yield held
            stats.held += run.frames
            previous = after
            yield after.tobytes()
            k = run.last + 1
            continue
        held = labelled(previous, text).tobytes()
        for _ in range(run.frames):
            yield held
        stats.held += run.frames
        k = run.last
    if next(source, None) is not None:
        raise ValueError("Mezzanine has more frames than its frame count")
