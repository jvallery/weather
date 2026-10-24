# SPDX-License-Identifier: MIT
"""Sealed-chunk intake: strict TAR reads, canonical grid slots, best attempt per slot.

Each sealed chunk is read once through `capture.chunks.read_chunk`, which binds every frame
member to the manifest by length and sha256. Frames are keyed by the collector's canonical grid
slot (`slot_at_utc`, #75). A slot's best attempt wins: verified > plausible > lower uncertainty
> earlier. Gaps are grid slots with no accepted frame; collector loss records are informational
and never remove a sealed frame. Payload bytes stay on the share until a block needs them.
"""

import hashlib
import logging
import os
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from weather.capture.chunks import read_chunk
from weather.timemodel._instant import epoch_seconds

LOG = logging.getLogger("weather.pipeline")
HOUR = 3600
MAX_CAPTURE_SECONDS = 5
FRESHNESS_RANK = {"verified": 0, "plausible": 1, "unknown": 2}
# Per-slot intake verdicts other than "ok". Decode failures are added by the renderer.
REJECTS = ("settings", "unknown_timing", "geometry", "stale", "changed", "decode")


class IntakeError(ValueError):
    """A frame's bytes no longer match the sealed manifest."""


def instant(text: str) -> Fraction:
    return epoch_seconds(datetime.fromisoformat(text))


def hour_name(seconds: int) -> str:
    return datetime.fromtimestamp(seconds - seconds % HOUR, UTC).strftime("%Y-%m-%dT%HZ")


@dataclass(frozen=True)
class Candidate:
    slot: Fraction  # canonical grid key (UTC seconds)
    time: Fraction  # estimated source time (UTC seconds)
    upper: Fraction | None
    freshness: str
    uncertainty_ms: float | None
    attempt_id: str
    chunk: Path
    member: str
    sha256: str
    size: int
    reject: str | None = None
    offset: int | None = None  # member data offset, so a read never rescans the archive

    def rank(self) -> tuple[Any, ...]:
        uncertainty = self.uncertainty_ms if self.uncertainty_ms is not None else float("inf")
        return (
            FRESHNESS_RANK.get(self.freshness, 9),
            uncertainty,
            self.time,
            self.attempt_id,
        )


@dataclass
class Chunk:
    window: tuple[Fraction, Fraction]
    candidates: list[Candidate] = field(default_factory=list)
    reported_losses: int = 0
    seen: int = 0


@dataclass(frozen=True)
class Expected:
    """The commissioned capture grid and geometry a frame must match."""

    camera: str
    cadence_ms: int
    phase_ms: int
    width: int
    height: int
    max_bytes: int = 16 * 1024 * 1024

    @property
    def interval(self) -> Fraction:
        return Fraction(self.cadence_ms, 1000)

    @property
    def max_lag(self) -> Fraction:
        """How far a frame's source time can trail its slot key: a request starts at most half
        a period late (later slots are skipped) and its midpoint is at most the capture
        deadline (<= 5 s, `capture.records.Settings`) after the start."""
        return self.interval / 2 + MAX_CAPTURE_SECONDS

    def grid(self, start: Fraction, end: Fraction) -> list[Fraction]:
        """Every grid slot key in [start, end)."""
        period, phase = self.interval, Fraction(self.phase_ms, 1000)
        first = -((phase - start) // period)
        slots = []
        key = first * period + phase
        while key < end:
            slots.append(key)
            key += period
        return slots


def parse_chunk(path: Path, expected: Expected) -> Chunk:
    records = read_chunk(path, expected.max_bytes)
    try:
        with tarfile.open(path, mode="r:") as archive:
            offsets = {member.name: member.offset_data for member in archive.getmembers()}
    except tarfile.TarError:
        raise ValueError("Invalid TAR archive") from None
    header = records[0]
    settings = header["settings"]
    window = (instant(header["slot_window_start_utc"]), instant(header["slot_window_end_utc"]))
    mismatch = (
        header["camera"] != expected.camera
        or settings["cadence_ms"] != expected.cadence_ms
        or settings["phase_ms"] != expected.phase_ms
    )
    chunk = Chunk(window)
    period, phase = expected.interval, Fraction(expected.phase_ms, 1000)
    for record in records[1:]:
        kind = record.get("record_type")
        if kind == "loss_range":
            chunk.reported_losses += int(record["attempt_count"])
            continue
        if kind == "skipped_range":
            chunk.reported_losses += int(record["slot_count"])
            continue
        if kind != "capture_attempt" or record.get("status") != "captured":
            continue
        member = record["member"]
        slot = instant(record["slot_at_utc"])
        upper = record.get("capture_upper_bound")
        reject = None
        if mismatch or (slot - phase) % period:
            reject = "settings"
        elif upper is None or record.get("clock_discontinuity"):
            reject = "unknown_timing"
        elif (member["width"], member["height"]) != (expected.width, expected.height):
            reject = "geometry"
        elif record.get("freshness") not in FRESHNESS_RANK:
            reject = "stale"
        chunk.candidates.append(
            Candidate(
                slot=slot,
                time=instant(record["source_at_utc"]),
                upper=instant(upper) if upper is not None else None,
                freshness=str(record.get("freshness")),
                uncertainty_ms=record.get("uncertainty_ms"),
                attempt_id=str(record["attempt_id"]),
                chunk=path,
                member=str(member["name"]),
                sha256=str(member["sha256"]),
                size=int(member["bytes"]),
                reject=reject,
                offset=offsets.get(str(member["name"])),
            )
        )
    return chunk


def read_frame(candidate: Candidate) -> bytes:
    """One member's bytes, re-verified against the sealed manifest.

    An I/O error (a soft-mounted share failing) propagates as OSError so the caller retries;
    only bytes that differ from the manifest are `IntakeError` (verdict `changed`)."""
    if candidate.offset is not None:
        with candidate.chunk.open("rb") as handle:
            handle.seek(candidate.offset)
            data = handle.read(candidate.size)
    else:
        try:
            with tarfile.open(candidate.chunk, mode="r:") as archive:
                source = archive.extractfile(candidate.member)
                if source is None:
                    raise IntakeError("Frame member missing")
                data = source.read(candidate.size + 1)
        except tarfile.TarError, KeyError:
            raise IntakeError("Frame member unreadable") from None
    if len(data) != candidate.size or hashlib.sha256(data).hexdigest() != candidate.sha256:
        raise IntakeError("Frame member changed after sealing")
    return data


@dataclass
class Window:
    """Best candidate per slot plus the slots whose every attempt was rejected."""

    frames: list[Candidate]
    # slot -> (source time, reason) for slots whose every attempt was rejected.
    rejected: dict[Fraction, tuple[Fraction, str]]
    duplicates: int
    reported_losses: int


class ChunkIndex:
    """In-memory index of one camera's sealed chunks, rebuilt by re-reading after a restart."""

    def __init__(self, staging: Path, expected: Expected) -> None:
        self.root = staging / "sealed" / expected.camera
        self.expected = expected
        self.chunks: dict[Path, Chunk] = {}
        self.invalid: set[Path] = set()
        self.generation = 0

    def refresh(self, start: Fraction, end: Fraction) -> list[Chunk]:
        """Parse sealed chunks of every UTC hour that can hold frames for [start, end)."""
        added: list[Chunk] = []
        # Whole UTC hours, from the one before start's (a frame's slot can precede its source
        # time) through end's own hour.
        hour = (int(start // 1) // HOUR - 1) * HOUR
        last = int(end // 1) // HOUR * HOUR
        self.generation += 1
        while hour <= last:
            directory = self.root / hour_name(hour)
            hour += HOUR
            try:
                names = sorted(entry.name for entry in os.scandir(directory) if entry.is_file())
            except FileNotFoundError:
                continue
            for name in names:
                path = directory / name
                if not name.endswith(".tar") or path in self.chunks or path in self.invalid:
                    continue
                try:
                    chunk = parse_chunk(path, self.expected)
                except OSError:
                    # A soft-mounted share can fail transiently: retry on the next refresh.
                    LOG.warning("Sealed chunk unreadable; will retry (%s)", self.expected.camera)
                    continue
                except ValueError:
                    # Never log the path; a reason code is all an operator needs.
                    LOG.warning("Rejected an invalid sealed chunk (%s)", self.expected.camera)
                    self.invalid.add(path)
                    continue
                chunk.seen = self.generation
                self.chunks[path] = chunk
                added.append(chunk)
        return added

    def prune(self, before: Fraction) -> None:
        for path in [p for p, chunk in self.chunks.items() if chunk.window[1] < before]:
            del self.chunks[path]

    def window(self, start: Fraction, end: Fraction) -> Window:
        return select(
            (c for chunk in self.chunks.values() for c in chunk.candidates),
            start,
            end,
            sum(
                chunk.reported_losses
                for chunk in self.chunks.values()
                if start <= chunk.window[0] < end
            ),
        )


def select(
    candidates: Iterable[Candidate], start: Fraction, end: Fraction, reported_losses: int = 0
) -> Window:
    """Best attempt per slot among candidates whose source time is in [start, end)."""
    best: dict[Fraction, Candidate] = {}
    rejected: dict[Fraction, tuple[Fraction, str]] = {}
    duplicates = 0
    for candidate in candidates:
        if not start <= candidate.time < end:
            continue
        if candidate.reject is not None:
            # Deterministic whatever order chunks were indexed in.
            item = (candidate.time, candidate.reject)
            if candidate.slot not in rejected or item < rejected[candidate.slot]:
                rejected[candidate.slot] = item
            continue
        current = best.get(candidate.slot)
        if current is not None:
            duplicates += 1
            if candidate.rank() >= current.rank():
                continue
        best[candidate.slot] = candidate
    frames = sorted(best.values(), key=lambda c: (c.time, c.slot))
    # Equal source times have no independent support: the earlier slot keeps it.
    unique: list[Candidate] = []
    for candidate in frames:
        if unique and unique[-1].time == candidate.time:
            duplicates += 1
            continue
        unique.append(candidate)
    return Window(
        frames=unique,
        rejected={slot: item for slot, item in rejected.items() if slot not in best},
        duplicates=duplicates,
        reported_losses=reported_losses,
    )


def utc(seconds: Fraction) -> datetime:
    whole = int(seconds // 1)
    micros = int((seconds - whole) * 1_000_000)
    return datetime.fromtimestamp(whole, UTC) + timedelta(microseconds=micros)
