# SPDX-License-Identifier: MIT
"""Keograms (#37): one column per elapsed-UTC bin, extracted in the streaming render pass.

A segment [a, b) has 1,440 equal bins; bin i is [a + i(b - a)/1440, a + (i + 1)(b - a)/1440).
A frame belongs to the segment the renderer gives it (half-open, by source time) and to the one
bin containing its source time, never a neighbour. Each bin keeps the valid observed capture
nearest its midpoint; ties go to the earlier capture, then the lower capture ID. All of this is
integer microsecond arithmetic, so the choice never depends on chunk order or boundaries.

A column is the camera's versioned region averaged across its width in linear light: the sRGB
transfer of #15 (`light.py`), as exact 16-bit linear integers, so the sum is order independent
and the result is the same on every node. The spatial axis is at most 512 px and is never
upscaled. A bin with no capture is a visible checkered gap column; nothing is interpolated.
"""

import hashlib
import io
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from weather.timemodel._instant import EPOCH

from .intake import Candidate
from .light import TO_SRGB, _eotf
from .render import SegmentJob, canonical

LOG = logging.getLogger("weather.pipeline")
BINS = 1440
MAX_SPATIAL = 512
THUMB_FACTOR = 4
RECIPE = "keogram/v1"
SELECTION = "nearest-bin-midpoint-earlier-then-capture-id/v1"
LIGHT = "srgb-linear-u16-mean/v1"
GAP = "checker-64-112-4px/v1"
GAP_RGB = ((64, 64, 64), (112, 112, 112))
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
DEFAULT_REGIONS = Path(__file__).with_name("keogram-regions.json")
# sRGB code -> linear light in 1/65535 steps; `to_srgb`'s table maps a step back to a code.
LINEAR_U16: NDArray[np.int64] = np.array(
    [round(_eotf(i / 255) * 65535) for i in range(256)], dtype=np.int64
)


class KeogramError(RuntimeError):
    """A keogram cannot be produced; its video is unaffected."""


# Region configuration -------------------------------------------------------------------------

Span = tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]


class Region(BaseModel):
    """A camera's keogram region: half-open pixel spans, the spatial axis and a mask policy.

    `vertical` keeps image rows (y) as the keogram's spatial axis and averages across x;
    `horizontal` keeps columns (x) and averages across y. The only mask is `none` (all three
    cameras publish full views, D02); a different mask is a new region version and code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,31}$")
    x: Span
    y: Span
    orientation: Literal["vertical", "horizontal"]
    mask: Literal["none"] = "none"

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not (self.x[0] < self.x[1] and self.y[0] < self.y[1]):
            raise ValueError("A keogram region must be a nonempty half-open span on each axis")
        return self

    @property
    def spatial(self) -> int:
        """Source pixels along the keogram's vertical axis."""
        span = self.y if self.orientation == "vertical" else self.x
        return span[1] - span[0]

    @property
    def height(self) -> int:
        return min(self.spatial, MAX_SPATIAL)

    def check(self, width: int, height: int) -> None:
        if self.x[1] > width or self.y[1] > height:
            raise ValueError("Keogram region lies outside the frame")

    def record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RegionConfig(BaseModel):
    """Every camera's region for one commissioned frame geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    width: int = Field(gt=0, le=16384)
    height: int = Field(gt=0, le=16384)
    cameras: dict[Literal["skycam_east", "skycam_west", "frontyard_1"], Region]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.cameras) != set(CAMERAS):
            raise ValueError("Every camera needs its own keogram region")
        for region in self.cameras.values():
            region.check(self.width, self.height)
        return self

    def region(self, camera: str, width: int, height: int) -> Region:
        """The camera's region, refused unless it was chosen for this frame geometry."""
        if (self.width, self.height) != (width, height):
            raise ValueError("Keogram regions were chosen for another frame geometry")
        return self.cameras[camera]  # type: ignore[index]


def load_regions(path: Path | None = None) -> RegionConfig:
    return RegionConfig.model_validate(json.loads((path or DEFAULT_REGIONS).read_text()))


# Columns ----------------------------------------------------------------------------------------


def _edges(length: int, groups: int) -> list[int]:
    return [index * length // groups for index in range(groups)]


def _means(sums: NDArray[np.int64], counts: NDArray[np.int64]) -> NDArray[np.uint8]:
    """Exact integer means (half up) of 16-bit linear sums, encoded through #15's table."""
    steps = (2 * sums + counts) // (2 * counts)
    result: NDArray[np.uint8] = TO_SRGB[steps]
    return result


def column(rgb: NDArray[np.uint8], region: Region) -> bytes:
    """The region averaged across its width in linear light: `region.height` RGB pixels."""
    (x0, x1), (y0, y1) = region.x, region.y
    if x1 > rgb.shape[1] or y1 > rgb.shape[0]:
        raise KeogramError("Keogram region lies outside the frame")
    area = rgb[y0:y1, x0:x1]
    if region.orientation == "horizontal":
        area = area.transpose(1, 0, 2)
    linear = LINEAR_U16[area]
    across = linear.sum(axis=1, dtype=np.int64)  # (spatial, 3)
    edges = _edges(across.shape[0], region.height)
    sums = np.add.reduceat(across, edges, axis=0)
    counts = np.diff([*edges, across.shape[0]]).astype(np.int64)[:, None] * area.shape[1]
    return _means(sums, counts).tobytes()


def downsample(image: NDArray[np.uint8], height: int, width: int) -> NDArray[np.uint8]:
    """Box-average an image to (height, width) in linear light; never upscales."""
    rows, cols = _edges(image.shape[0], height), _edges(image.shape[1], width)
    linear = LINEAR_U16[image]
    sums = np.add.reduceat(np.add.reduceat(linear, rows, axis=0), cols, axis=1)
    counts = np.outer(
        np.diff([*rows, image.shape[0]]), np.diff([*cols, image.shape[1]])
    ).astype(np.int64)[:, :, None]
    return _means(sums, counts)


# Bins and selection -----------------------------------------------------------------------------


def micros(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1)


def instant(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value)


@dataclass(frozen=True)
class BinMap:
    """1,440 equal half-open bins over [start, end), in integer microseconds."""

    start: int
    end: int
    count: int = BINS

    @classmethod
    def of(cls, job: SegmentJob) -> BinMap:
        return cls(micros(job.starts_at), micros(job.ends_at))

    @property
    def length(self) -> int:
        return self.end - self.start

    def index(self, time: int) -> int | None:
        if not self.start <= time < self.end:
            return None
        return (time - self.start) * self.count // self.length

    def distance(self, index: int, time: int) -> int:
        """|time - midpoint| scaled by 2 * count, an exact integer."""
        return abs(2 * self.count * (time - self.start) - (2 * index + 1) * self.length)

    def bounds(self, index: int) -> tuple[Fraction, Fraction]:
        """Bin `index` in exact microseconds."""
        step = Fraction(self.length, self.count)
        return self.start + index * step, self.start + (index + 1) * step

    @property
    def step_seconds(self) -> Fraction:
        return Fraction(self.length, self.count * 1_000_000)


@dataclass(frozen=True)
class Sample:
    """The capture a bin keeps, with its column."""

    bin: int
    distance: int
    captured: int  # source time, UTC microseconds
    capture_id: str
    upper: int  # capture upper bound, UTC microseconds
    uncertainty_ms: float | None
    strip: bytes

    @property
    def key(self) -> tuple[int, int, str]:
        return self.distance, self.captured, self.capture_id


def _floor_micros(value: Fraction) -> int:
    return value.numerator * 1_000_000 // value.denominator


def _ceil_micros(value: Fraction) -> int:
    """An upper bound rounds up, so the embargo never starts early."""
    return -(-value.numerator * 1_000_000 // value.denominator)


Sink = Callable[[SegmentJob, Region, list[Sample], str | None], None]


def _never(error: BaseException) -> bool:
    return isinstance(error, (OSError, TimeoutError))


class KeogramStage:
    """The renderer's per-frame hook: keeps each bin's best capture for the block in progress
    and hands it to `sink` before the block becomes durable (`render.ChunkStage`).

    The sink merges by the same key (nearest, earlier, capture ID), so re-rendering a block
    after a crash, or a bin that spans two blocks, converges to the same selection. A failure
    here never stops the video: the segment's keogram is reported failed instead. Only an
    error `transient` accepts (lost connectivity) propagates, deferring the block for retry."""

    def __init__(
        self, region: Region, sink: Sink, transient: Callable[[BaseException], bool] = _never
    ) -> None:
        self.region, self.sink, self.transient = region, sink, transient
        self.pending: dict[str, dict[int, Sample]] = {}
        self.failed: dict[str, str] = {}

    def frame(self, job: SegmentJob, candidate: Candidate, rgb: NDArray[np.uint8]) -> None:
        if job.key in self.failed:
            return
        try:
            bins = BinMap.of(job)
            captured = _floor_micros(candidate.time)
            index = bins.index(captured)
            if index is None or candidate.upper is None:
                return
            key = (bins.distance(index, captured), captured, candidate.attempt_id)
            kept = self.pending.setdefault(job.key, {})
            best = kept.get(index)
            if best is not None and best.key <= key:
                return
            kept[index] = Sample(
                bin=index,
                distance=key[0],
                captured=captured,
                capture_id=candidate.attempt_id,
                upper=_ceil_micros(candidate.upper),
                uncertainty_ms=candidate.uncertainty_ms,
                strip=column(rgb, self.region),
            )
        except Exception as error:
            # Never log paths or payloads; the type is enough.
            LOG.error("Keogram extraction failed (%s, %s)", job.camera, type(error).__name__)
            self.failed[job.key] = "extraction"
            self.pending.pop(job.key, None)

    def commit(self, job: SegmentJob, first: int, last: int) -> None:
        samples = sorted(self.pending.pop(job.key, {}).values(), key=lambda s: s.bin)
        try:
            self.sink(job, self.region, samples, self.failed.get(job.key))
        except Exception as error:
            if self.transient(error):
                raise
            # The bins of this block are lost: the keogram cannot be complete, so it fails.
            LOG.error("Keogram bins refused (%s, %s)", job.camera, type(error).__name__)
            self.failed[job.key] = "store"

    def forget(self, job: SegmentJob) -> None:
        self.pending.pop(job.key, None)
        self.failed.pop(job.key, None)


# Assembly ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assembled:
    png: bytes
    thumbnail: bytes
    width: int
    height: int
    thumb_width: int
    thumb_height: int
    observed: int
    gaps: list[tuple[int, int]]
    lineage: str
    upper: int  # latest capture upper bound, UTC microseconds

    @property
    def state(self) -> tuple[Literal["published", "partial"], str | None]:
        return ("published", None) if self.observed == BINS else ("partial", "gaps")


def gap_ranges(observed: Sequence[bool]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for index, seen in enumerate(observed):
        if seen:
            continue
        if ranges and ranges[-1][1] == index:
            ranges[-1] = (ranges[-1][0], index + 1)
        else:
            ranges.append((index, index + 1))
    return ranges


def gap_column(index: int, height: int) -> NDArray[np.uint8]:
    rows = (np.arange(height) // 4 + index // 4) % 2
    return np.array(GAP_RGB, dtype=np.uint8)[rows]


def _png(image: NDArray[np.uint8]) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(image, "RGB").save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def assemble(samples: Sequence[Sample], height: int) -> Assembled:
    """The 1,440-column PNG (column i is bin i, time left to right) and its thumbnail."""
    if not samples:
        raise KeogramError("No observed bins")
    by_bin = {sample.bin: sample for sample in samples}
    if len(by_bin) != len(samples) or not all(0 <= b < BINS for b in by_bin):
        raise KeogramError("Bins must be unique and in range")
    image = np.empty((height, BINS, 3), dtype=np.uint8)
    for index in range(BINS):
        sample = by_bin.get(index)
        if sample is None:
            image[:, index] = gap_column(index, height)
            continue
        if len(sample.strip) != height * 3:
            raise KeogramError("A bin's column does not match the region height")
        image[:, index] = np.frombuffer(sample.strip, dtype=np.uint8).reshape(height, 3)
    thumb_width, thumb_height = BINS // THUMB_FACTOR, max(1, height // THUMB_FACTOR)
    observed = [index in by_bin for index in range(BINS)]
    lineage = hashlib.sha256(
        canonical([[s.bin, s.captured] for s in sorted(samples, key=lambda s: s.bin)])
    ).hexdigest()
    return Assembled(
        png=_png(image),
        thumbnail=_png(downsample(image, thumb_height, thumb_width)),
        width=BINS,
        height=height,
        thumb_width=thumb_width,
        thumb_height=thumb_height,
        observed=len(by_bin),
        gaps=gap_ranges(observed),
        lineage=lineage,
        upper=max(s.upper for s in samples),
    )


def sidecar(
    job: SegmentJob,
    region: Region,
    samples: Sequence[Sample],
    assembled: Assembled,
    public_not_before: datetime,
) -> dict[str, Any]:
    """Public keogram metadata: bin UTC map, gaps, lineage, coverage, recipe and region."""
    bins = BinMap.of(job)
    state, reason = assembled.state
    captures: list[str | None] = [None] * BINS
    for sample in samples:
        captures[sample.bin] = instant(sample.captured).isoformat()
    step = bins.step_seconds
    return {
        "schema_version": 1,
        "product": "keogram",
        "camera": job.camera,
        "cycle_date": job.cycle_date.isoformat(),
        "segment": job.kind,
        "state": state,
        "reason": reason,
        "source_interval": {"start": job.starts_at.isoformat(), "end": job.ends_at.isoformat()},
        "public_not_before": public_not_before.isoformat(),
        "bins": {
            "count": BINS,
            "step_seconds": {"numerator": step.numerator, "denominator": step.denominator},
            "bin": "[start + i * step_seconds, start + (i + 1) * step_seconds)",
            "x": "column i of the image is bin i",
        },
        "image": {
            "width": assembled.width,
            "height": assembled.height,
            "thumbnail": {"width": assembled.thumb_width, "height": assembled.thumb_height},
            "gap_pattern": GAP,
        },
        "coverage": {
            "observed": assembled.observed,
            "missing": BINS - assembled.observed,
            "fraction": round(assembled.observed / BINS, 6),
            "gaps": [list(item) for item in assembled.gaps],
        },
        "captures": captures,
        "lineage_sha256": assembled.lineage,
        "provenance": {
            "recipe": RECIPE,
            "selection": SELECTION,
            "light": LIGHT,
            "spatial_max": MAX_SPATIAL,
            "region": region.record(),
        },
    }
