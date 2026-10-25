# SPDX-License-Identifier: MIT
"""Streaming per-segment renderer: sealed frames -> durable intra mezzanine blocks.

Output frames are owned in contiguous blocks of at most 60. A block renders once its last
shutter has closed and every chunk that could hold a contributing frame has had time to seal
(`shutter end + 2 intervals + chunk_seconds + 30 s`); chunks sealed later are counted `late`.
Each block reads only the frames that can touch its shutters, decodes each once with the pinned
FFmpeg (a frame FFmpeg cannot decode is a per-slot reject that never aborts the block), and
integrates them in linear light. Only a few frames and accumulators are in memory at once.

A block is durable when its metadata and its Matroska file are both fsynced and renamed into
`blocks/`; a crash leaves at most a `.tmp-*` file that the next run removes. Because the
mezzanine is intra-only, a resumed or differently-sized block produces the same decoded frames.
"""

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from weather.timemodel import FrameMap

from . import metrics
from .exposure import Geometry, overlap
from .ffmpeg import BlockEncoder, decode_jpeg
from .intake import Candidate, ChunkIndex, Expected, IntakeError, read_frame
from .light import to_linear, to_srgb
from .recipe import MAX_BLOCK, SLATE_RGB, Recipe

LOG = logging.getLogger("weather.pipeline")
SEAL_MARGIN = 30


@dataclass(frozen=True)
class SegmentJob:
    camera: str
    cycle_date: date
    kind: str
    revision: int
    starts_at: datetime
    ends_at: datetime
    frames: int

    @property
    def key(self) -> str:
        return f"{self.cycle_date.isoformat()}_{self.kind}_r{self.revision}"

    @property
    def frame_map(self) -> FrameMap:
        return FrameMap(self.starts_at, self.ends_at, self.frames)


class ChunkStage(Protocol):
    """Per-frame hook for products that share the decode (#37 keograms, #38 hourly
    candidates, #39 previews). Called once per accepted frame each time its owning block
    renders, so implementations must be idempotent by slot."""

    def frame(self, camera: str, candidate: Candidate, rgb: NDArray[np.uint8]) -> None: ...


def micros(value: Fraction) -> str:
    """Exact microsecond integer text for a slot key or time (grid keys are whole ms)."""
    return str(value.numerator * 1_000_000 // value.denominator)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".tmp-{path.name}")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass
class _Accumulator:
    sum: NDArray[np.float32] | None = None
    weight: Fraction = Fraction(0)
    weight32: np.float32 = np.float32(0)


class SegmentRenderer:
    def __init__(
        self,
        job: SegmentJob,
        *,
        staging: Path,
        expected: Expected,
        recipe: Recipe,
        index: ChunkIndex,
        block: int = MAX_BLOCK,
        chunk_seconds: int = 60,
        stages: Sequence[ChunkStage] = (),
        after_block: Callable[[int], None] | None = None,
    ) -> None:
        if not 1 <= block <= MAX_BLOCK:
            raise ValueError("Block size must be 1..60 frames")
        self.job, self.expected, self.recipe, self.index = job, expected, recipe, index
        self.block, self.chunk_seconds, self.stages = block, chunk_seconds, tuple(stages)
        self.after_block = after_block
        self.geometry = Geometry.of(job.frame_map, expected.interval, recipe.shutter_degrees)
        self.directory = staging / "render" / job.camera / f"{job.key}-{recipe.id}"
        self.blocks_dir = self.directory / "blocks"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        # os.scandir raises on a failing share; Path.glob would silently list nothing and a
        # restart would resume at frame 0 over input #19 may already have released.
        for name in self._listing():
            if name.startswith(".tmp-"):
                (self.blocks_dir / name).unlink(missing_ok=True)
        self.metas = self._load()
        self.slate = np.full(
            (expected.height, expected.width, 3), SLATE_RGB, dtype=np.uint8
        ).tobytes()

    # Durable state -------------------------------------------------------------------------

    def _listing(self) -> list[str]:
        with os.scandir(self.blocks_dir) as entries:
            return sorted(entry.name for entry in entries)

    def _load(self) -> list[dict[str, Any]]:
        """Contiguous durable blocks from frame 0; anything after a hole is re-rendered.
        Every listing or stat error propagates: only a definite absence is a hole."""
        metas = []
        names = set(self._listing())
        for name in sorted(n for n in names if n.endswith(".json") and not n.startswith(".")):
            meta = json.loads((self.blocks_dir / name).read_text())
            if meta.get("recipe") != self.recipe.id or f"{name[:-5]}.mkv" not in names:
                continue
            metas.append(meta)
        metas.sort(key=lambda meta: int(meta["first"]))
        contiguous: list[dict[str, Any]] = []
        for meta in metas:
            expected_first = contiguous[-1]["last"] if contiguous else 0
            if meta["first"] != expected_first:
                break
            contiguous.append(meta)
        return contiguous

    @property
    def rendered(self) -> int:
        return int(self.metas[-1]["last"]) if self.metas else 0

    @property
    def complete(self) -> bool:
        return self.rendered == self.job.frames

    def released_through(self) -> Fraction | None:
        """Slot key below which this segment will never read another frame (#19).

        Blocks read by source time; a frame's source time trails its slot key by at most
        `max_lag`, so the next block's earliest read (its window start) is converted to the
        smallest slot key that can still be read there. A complete mezzanine reads nothing."""
        if not self.metas:
            return None
        if self.complete:
            return self.geometry.end
        owned_end = self.geometry.owned(0, self.rendered)[1]
        window_start = max(self.geometry.start, owned_end - 2 * self.expected.interval)
        return window_start - self.expected.max_lag

    def block_paths(self) -> list[Path]:
        return [self.blocks_dir / f"{int(meta['first']):05d}.mkv" for meta in self.metas]

    def ready_at(self, last: int) -> Fraction:
        """Wall time (UTC seconds) after which output frames before `last` may render."""
        shutter_end = self.geometry.shutter(last - 1)[1]
        return shutter_end + 2 * self.expected.interval + self.chunk_seconds + SEAL_MARGIN

    def next_block(self) -> tuple[int, int] | None:
        if self.complete:
            return None
        first = self.rendered
        return first, min(first + self.block, self.job.frames)

    # Rendering -----------------------------------------------------------------------------

    def advance(self, now: Fraction, *, limit: int | None = None) -> int:
        """Render every block that is ready at `now`; returns how many were rendered."""
        count = 0
        while (span := self.next_block()) is not None and now >= self.ready_at(span[1]):
            if limit is not None and count >= limit:
                break
            self.render_block(*span)
            count += 1
            if self.after_block is not None:
                self.after_block(span[1])
        return count

    def render_block(self, first: int, last: int) -> dict[str, Any]:
        g, expected = self.geometry, self.expected
        owned = g.owned(first, last)
        shutter_lo, shutter_hi = g.shutter(first)[0], g.shutter(last - 1)[1]
        margin = 2 * expected.interval
        lo = max(g.start, min(shutter_lo, owned[0]) - margin)
        hi = min(g.end, max(shutter_hi, owned[1]) + margin)
        final = last == self.job.frames
        # The final block also judges slots just before b whose capture landed at or after b:
        # they count toward this segment's slot coverage, though their pixels belong to the next.
        tail_end = g.end + expected.max_lag if final else hi
        self.index.refresh(lo, tail_end)
        window = self.index.window(lo, hi)
        own = self.index.window(*owned)
        slots: dict[str, str] = {}
        if self.index.unreadable:
            # A chunk that could hold this block's frames failed to read: never make a block
            # durable without it. The next loop retries.
            raise OSError("Sealed chunk unreadable; block deferred")
        for slot, (source_time, reason) in window.rejected.items():
            if owned[0] <= source_time < owned[1]:
                slots[micros(slot)] = reason
                metrics.REJECTS.labels(self.job.camera, reason).inc()
        width = g.width
        threshold = self.recipe.min_exposure_coverage
        accumulators: dict[int, _Accumulator] = {}
        frames_meta: list[dict[str, Any]] = []
        inputs: set[tuple[str, str]] = set()
        max_upper: Fraction | None = None
        tmp = self.blocks_dir / f".tmp-{first:05d}.mkv"
        encoder = BlockEncoder(tmp, expected.width, expected.height)
        state = {"next": first}

        def emit_through(boundary: Fraction | None) -> None:
            while state["next"] < last and (
                boundary is None or g.shutter(state["next"])[1] <= boundary
            ):
                k = state["next"]
                item = accumulators.pop(k, _Accumulator())
                cover = item.weight / width
                if item.sum is not None and item.weight > 0 and cover >= threshold:
                    pixels = to_srgb(item.sum / item.weight32).tobytes()
                    slate = False
                else:
                    pixels, slate = self.slate, True
                encoder.write(pixels)
                frames_meta.append(
                    {
                        "coverage": round(float(cover), 6),
                        "slate": slate,
                        "sha256": hashlib.sha256(pixels).hexdigest(),
                    }
                )
                metrics.FRAMES.labels(self.job.camera, "gap" if slate else "rendered").inc()
                state["next"] += 1

        def contribute(
            candidate: Candidate, rgb: bytes, support: tuple[Fraction, Fraction]
        ) -> None:
            nonlocal max_upper
            ks = [k for k in g.overlapping(support) if first <= k < last]
            weights = [(k, overlap(g.shutter(k), support)) for k in ks]
            weights = [(k, w) for k, w in weights if w > 0]
            if weights:
                linear = to_linear(
                    np.frombuffer(rgb, dtype=np.uint8).reshape(expected.height, expected.width, 3)
                )
                inputs.add((micros(candidate.slot), candidate.sha256))
                for k, w in weights:
                    item = accumulators.setdefault(k, _Accumulator())
                    w32 = np.float32(float(w))
                    term = linear * w32
                    if item.sum is None:
                        item.sum = term
                    else:
                        item.sum += term
                    item.weight += w
                    item.weight32 = np.float32(item.weight32 + w32)
            emit_through(support[1])

        try:
            previous: Fraction | None = None
            pending: tuple[Candidate, bytes] | None = None
            for candidate in [*window.frames, None]:
                decoded: tuple[Candidate, bytes] | None = None
                if candidate is not None:
                    verdict: str | None = None
                    rgb: bytes | None = None
                    try:
                        rgb = decode_jpeg(read_frame(candidate), expected.width, expected.height)
                    except IntakeError:
                        verdict = "changed"
                    is_owned = owned[0] <= candidate.time < owned[1]
                    if rgb is None:
                        verdict = verdict or "decode"
                        if is_owned:
                            slots[micros(candidate.slot)] = verdict
                            metrics.REJECTS.labels(self.job.camera, verdict).inc()
                        continue
                    decoded = (candidate, rgb)
                    if is_owned:
                        slots[micros(candidate.slot)] = "ok"
                        assert candidate.upper is not None
                        max_upper = (
                            candidate.upper
                            if max_upper is None
                            else max(max_upper, candidate.upper)
                        )
                        if self.stages:
                            array = np.frombuffer(rgb, dtype=np.uint8).reshape(
                                expected.height, expected.width, 3
                            )
                            for stage in self.stages:
                                stage.frame(self.job.camera, candidate, array)
                if pending is not None:
                    current, current_rgb = pending
                    following = decoded[0].time if decoded is not None else None
                    contribute(current, current_rgb, g.support(current.time, previous, following))
                    previous = current.time
                pending = decoded
            emit_through(None)
            encoder.close()
            if final:
                tail = self.index.window(g.end, tail_end)

                def judge(slot: Fraction, verdict: str) -> None:
                    # Another attempt for the slot may already be accepted: never downgrade it.
                    if slots.get(micros(slot)) != "ok":
                        slots[micros(slot)] = verdict

                for slot, (_, reason) in tail.rejected.items():
                    if slot < g.end:
                        judge(slot, reason)
                for candidate in tail.frames:
                    if candidate.slot >= g.end:
                        continue
                    try:
                        good = decode_jpeg(read_frame(candidate), expected.width, expected.height)
                        judge(candidate.slot, "ok" if good is not None else "decode")
                    except IntakeError:
                        judge(candidate.slot, "changed")
        except BaseException:
            encoder.abort()
            tmp.unlink(missing_ok=True)
            raise
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        meta = {
            "schema_version": 1,
            "recipe": self.recipe.id,
            # Build provenance per block: a resumed mezzanine may span an image upgrade.
            "encoder": self.recipe.encoder,
            "image": self.recipe.image,
            "first": first,
            "last": last,
            "mkv_sha256": file_sha256(tmp),
            "frames": frames_meta,
            "slots": dict(sorted(slots.items())),
            "inputs": sorted(inputs),
            "max_upper": micros(max_upper) if max_upper is not None else None,
            # Counted over the owned range only, so blocks never double count.
            "duplicates": own.duplicates,
            "reported_losses": own.reported_losses,
        }
        if len(frames_meta) != last - first:
            raise RuntimeError("Block frame count mismatch")
        # Metadata first, then the block itself: the .mkv link is the commit point. A durable
        # block is never replaced (link fails with EEXIST): a resume that lost sight of it
        # must not overwrite good frames with slates.
        target = self.blocks_dir / f"{first:05d}.mkv"
        try:
            os.lstat(target)  # any error but a definite absence propagates
        except FileNotFoundError:
            pass
        else:
            tmp.unlink(missing_ok=True)
            raise FileExistsError("Durable block already exists")
        durable_write(self.blocks_dir / f"{first:05d}.json", canonical(meta))
        os.link(tmp, target)
        tmp.unlink()
        fsync_dir(self.blocks_dir)
        self.metas.append(meta)
        metrics.LAG.labels(self.job.camera).set(time.time() - float(owned[1]))
        return meta
