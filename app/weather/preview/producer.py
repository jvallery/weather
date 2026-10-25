# SPDX-License-Identifier: MIT
"""The preview producer: one camera's newest eligible sealed frame, about once a minute.

Runs beside the renderer in each `pipeline-<camera>` process (#15), with its own chunk cache
and the worker database role. Every tick it lists only `sealed/<camera>/` for the current and
previous UTC hour (never `tmp/`), reads each new sealed chunk once through the strict #15
intake, and keeps the newest frame whose bounds are plausible and whose
`capture_upper_bound + 600 s + skew_margin` has passed. A frame no newer than the last
published one is not republished, so a stopped camera ages honestly to unavailable. After an
outage it publishes the newest eligible frame once; it never replays the missed minutes.

A generation is the producer's UTC minute, so a camera gets at most one per minute. Its
derivatives are written tmp -> fsync -> rename under `previews/<camera>/<generation>-<variant>
.jpg`, then both pointer rows commit in one transaction with one `first_published_at`, the
database clock at the transaction's start; the CHECKs refuse a row the database clock does
not yet consider eligible.
Rows past `expires_at` are deleted and files are removed about an hour after their generation.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prometheus_client import Counter, Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.pipeline.intake import Candidate, Expected, hour_name, parse_chunk, read_frame, utc
from weather.pipeline.render import durable_write

from . import policy
from .jpeg import derive

LOG = logging.getLogger("weather.preview")
TICK_SECONDS = 15
# Chunks whose seal is older than this cannot hold a frame young enough to publish.
LOOKBACK = policy.STALE + timedelta(minutes=5)
# Once a chunk has yielded an eligible frame, look at this many older chunks for a newer one
# (two writer incarnations can seal out of order), then stop.
EXTRA_CHUNKS = 2
FILE = re.compile(r"(\d{1,16})-(large|small)\.jpg")
# Files outlive their rows: removed an hour after the generation plus this grace.
FILE_GRACE = timedelta(minutes=5)
TMP_GRACE = 300

GENERATIONS = Counter(
    "weather_preview_generations_total", "Preview generations published", ["camera"]
)
FAILURES = Counter(
    "weather_preview_failures_total",
    "Preview producer failures by stage (scan, derive, write, catalog, cleanup)",
    ["camera", "stage"],
)
AGE = Gauge(
    "weather_preview_age_seconds",
    "Now minus the capture lower bound of the newest published preview; -1 before the first",
    ["camera"],
)
INSERT = """
    INSERT INTO weather.camera_preview (camera_id,generation,variant,width,height,bytes,sha256,
      path,captured_lower,captured_upper,public_not_before,first_published_at,expires_at)
    VALUES (:camera,:generation,:variant,:width,:height,:bytes,:sha256,:path,
      CAST(:lower AS timestamptz),CAST(:upper AS timestamptz),CAST(:not_before AS timestamptz),
      now(),CAST(:lower AS timestamptz) + interval '3600 seconds')
    ON CONFLICT (camera_id,generation,variant) DO NOTHING
"""


def _seconds(value: datetime) -> int:
    return int(value.timestamp())


class PreviewProducer:
    def __init__(
        self,
        staging: Path,
        expected: Expected,
        engine: AsyncEngine,
        *,
        skew: timedelta = policy.DEFAULT_SKEW,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if skew < policy.DEFAULT_SKEW:
            raise ValueError("Preview skew margin is below the database floor")
        self.camera = expected.camera
        self.expected, self.engine, self.skew, self.clock = expected, engine, skew, clock
        self.sealed = staging / "sealed" / self.camera
        self.directory = staging / "previews" / self.camera
        # path -> (mtime_ns, size, candidates); sealed chunks never change.
        self.cache: dict[Path, tuple[int, int, list[Candidate]]] = {}
        self.loaded = False
        self.generation = 0
        self.last: tuple[datetime, datetime] | None = None  # (lower, upper) last published
        self.cleaned = 0  # the minute of the last cleanup
        AGE.labels(self.camera).set(-1)

    # ---- selection (runs in a thread: NFS calls may block) -----------------------------------

    def _usable(self, candidate: Candidate, now: datetime) -> bool:
        if candidate.reject is not None or candidate.upper is None or candidate.lower is None:
            return False
        lower, upper = utc(candidate.lower), utc(candidate.upper)
        if not policy.publishable(lower, upper, now, self.skew):
            return False
        return self.last is None or upper > self.last[1]

    def select(self, now: datetime) -> Candidate | None:
        """The newest eligible frame in recently sealed chunks; OSError means retry later."""
        seconds = _seconds(now)
        entries: list[tuple[int, Path, int]] = []
        for hour in sorted({hour_name(seconds - 3600), hour_name(seconds)}):
            try:
                scanned = list(os.scandir(self.sealed / hour))
            except FileNotFoundError:
                continue
            for entry in scanned:
                if not entry.name.endswith(".tar") or not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                entries.append((stat.st_mtime_ns, Path(entry.path), stat.st_size))
        oldest = (seconds - LOOKBACK.total_seconds()) * 1_000_000_000
        entries = sorted((e for e in entries if e[0] >= oldest), reverse=True)
        best: Candidate | None = None
        after_hit = 0
        for mtime, path, size in entries:
            cached = self.cache.get(path)
            if cached is None or cached[:2] != (mtime, size):
                try:
                    candidates = parse_chunk(path, self.expected).candidates
                except ValueError:
                    candidates = []  # invalid chunk: never retried, never used
                self.cache[path] = (mtime, size, candidates)
            for candidate in self.cache[path][2]:
                if self._usable(candidate, now) and (
                    best is None or (candidate.upper, candidate.slot) > (best.upper, best.slot)
                ):
                    best = candidate
            if best is not None:
                after_hit += 1
                if after_hit > EXTRA_CHUNKS:
                    break
        listed = {path for _, path, _ in entries}
        self.cache = {path: value for path, value in self.cache.items() if path in listed}
        return best

    def write(self, generation: int, candidate: Candidate) -> list[tuple[str, bytes, int, int]]:
        derivatives = derive(read_frame(candidate))
        self.directory.mkdir(parents=True, exist_ok=True)
        for variant, data, _, _ in derivatives:
            durable_write(self.directory / f"{generation}-{variant}.jpg", data)
        return derivatives

    def clean_files(self, now: datetime) -> None:
        try:
            scanned = list(os.scandir(self.directory))
        except FileNotFoundError:
            return
        horizon = now - policy.RETENTION - FILE_GRACE
        for entry in scanned:
            match = FILE.fullmatch(entry.name)
            if match is not None:
                if datetime.fromtimestamp(int(match[1]) * 60, UTC) < horizon:
                    os.unlink(entry.path)
            elif entry.name.startswith(".tmp-"):
                # Only this producer writes here; a tmp file this old is from a killed write.
                if entry.stat(follow_symlinks=False).st_mtime < time.time() - TMP_GRACE:
                    os.unlink(entry.path)

    # ---- one tick -----------------------------------------------------------------------------

    async def _load(self) -> None:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT generation,captured_lower,captured_upper "
                        "FROM weather.camera_preview WHERE camera_id=:camera "
                        "ORDER BY generation DESC LIMIT 1"
                    ),
                    {"camera": self.camera},
                )
            ).first()
        if row is not None:
            self.generation = int(row.generation)
            self.last = (row.captured_lower, row.captured_upper)
        self.loaded = True

    async def _commit(
        self, generation: int, candidate: Candidate, derivatives: list[tuple[str, bytes, int, int]]
    ) -> None:
        assert candidate.lower is not None and candidate.upper is not None
        lower, upper = utc(candidate.lower), utc(candidate.upper)
        not_before = upper + policy.MIN_DELAY + self.skew
        async with self.engine.begin() as connection:
            for variant, data, width, height in derivatives:
                await connection.execute(
                    text(INSERT),
                    {
                        "camera": self.camera,
                        "generation": generation,
                        "variant": variant,
                        "width": width,
                        "height": height,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "path": f"previews/{self.camera}/{generation}-{variant}.jpg",
                        "lower": lower,
                        "upper": upper,
                        "not_before": not_before,
                    },
                )
        self.generation, self.last = generation, (lower, upper)

    async def step(self) -> bool:
        """Publish at most one generation; True when one was committed."""
        now = self.clock()
        generation = _seconds(now) // 60
        stage = "catalog"
        published = False
        try:
            if not self.loaded:
                await self._load()
            if generation > self.generation:
                stage = "scan"
                candidate = await asyncio.to_thread(self.select, now)
                if candidate is not None:
                    stage = "derive"
                    derivatives = await asyncio.to_thread(self.write, generation, candidate)
                    stage = "catalog"
                    await self._commit(generation, candidate, derivatives)
                    GENERATIONS.labels(self.camera).inc()
                    published = True
        except Exception as error:
            FAILURES.labels(self.camera, stage).inc()
            # Never log paths, payloads or driver errors.
            LOG.error("Preview %s failed (%s, %s)", stage, self.camera, type(error).__name__)
            # A commit whose acknowledgement was lost must not be followed by new bytes under
            # the same generation's file names: re-read the newest generation first.
            self.loaded = False
        if self.last is not None:
            AGE.labels(self.camera).set((now - self.last[0]).total_seconds())
        if generation > self.cleaned:
            self.cleaned = generation
            await self._cleanup(now)
        return published

    async def _cleanup(self, now: datetime) -> None:
        try:
            async with self.engine.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM weather.camera_preview "
                        "WHERE camera_id=:camera AND expires_at <= clock_timestamp()"
                    ),
                    {"camera": self.camera},
                )
            await asyncio.to_thread(self.clean_files, now)
        except Exception as error:
            FAILURES.labels(self.camera, "cleanup").inc()
            LOG.error("Preview cleanup failed (%s, %s)", self.camera, type(error).__name__)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.step()
            try:
                await asyncio.wait_for(stop.wait(), TICK_SECONDS)
            except TimeoutError:
                pass
