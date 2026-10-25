# SPDX-License-Identifier: MIT
"""Staging cleanup (#19): catalog rows decide which sealed chunks, stale tmp files and render
directories may go. Runs in each camera's pipeline pod, the only role that mounts staging
besides the collectors (the scheduler and the api never mount NFS, C17).

A sealed chunk is deletable only when its capture settings match the pipeline's (`config`: a
cadence, phase or geometry mismatch makes every frame a `settings` reject, so the slates the
renderer writes must never release the input) and every condition of P3 holds. The order is
the order a pinned chunk reports:

0. `config`: the chunk header's cadence, phase and geometry equal the pipeline's.
1. `mezzanine`: every current segment that can read its frames has released them
   (`segment.mezzanine_through`, a slot key, is at or past the chunk's slot-window end, or the
   segment's mezzanine is complete), and the frozen schedule covers every source time the
   chunk's slots can have. A dusk-spanning chunk therefore waits for both segments.
2. `keogram`: its keogram bins are committed (#37).
3. `hourly`: every overlapping UTC hour slot is finalized, its original archived and read back
   or the slot marked missing (#38).
4. `preview`: its newest capture is more than 1,800 s plus one preview refresh old (#39).

`config`, `mezzanine` and `preview` are implemented here and always required. `hourly`
(#38) is registered here against `archival_still`; `keogram` (#37) is registered by its
feature PR in `CONDITION_QUERIES`. An unregistered condition is unmet (the chunk is kept)
unless its mode is `vacuous` (`WEATHER_CLEANUP_KEOGRAM=vacuous`). Vacuous is honoured only
while the query is unregistered: a vacuous setting for a registered query fails start-up, and
the rule evaluates a wired condition regardless. The default is `required`.

Every deletion is two-phase and idempotent: a `staging_deletion` row (name, hash, size,
reason) commits first, then the file is unlinked and `unlinked_at` set. A crash in between is
finished by the next pass; a file that is already gone is a no-op; a chunk whose manifest hash
or size no longer matches its row is never deleted. Files without a catalog row (unreadable or
invalid chunks, unknown names) are never touched. With `WEATHER_CLEANUP_ENABLED` false (the
default) a pass inventories, evaluates and exports metrics but deletes nothing.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tarfile
from collections import Counter as Tally
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from prometheus_client import Counter, Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .pressure import Pressure

LOG = logging.getLogger("weather.cleanup")
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
CONDITIONS = ("config", "mezzanine", "keogram", "hourly", "preview")
CONFIGURABLE = ("keogram", "hourly")
PREVIEW_WINDOW = timedelta(seconds=1800)
# The pipeline stops selecting an unfinalized terminal segment this long after its end
# (`pipeline.schedule.LATE_RENDER`); its render directory is then no longer written.
LATE_RENDER = timedelta(hours=12)
# active() decides on the pipeline pod's clock, cleanup on the database's: keep a margin.
CLOCK_MARGIN = timedelta(hours=1)
# Under urgent pressure cleanup runs ahead of rendering, but at most this often (seconds).
URGENT_INTERVAL = 60.0
MAX_MANIFEST_BYTES = 1024 * 1024
HOUR_DIR = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}Z\Z")
SEALED_FILE = re.compile(r"[A-Za-z0-9_-]{1,120}\.tar\Z")
TMP_FILE = re.compile(r"([0-9]{1,20})_([0-9]{1,20})__([A-Za-z0-9_-]{1,120})\.tar\Z")
RENDER_DIR = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})_(daytime|overnight)_r([0-9]{1,9})-([A-Za-z0-9._-]{1,80})\Z"
)
CLASSES = ("sealed", "tmp", "render")

BYTES = Gauge(
    "weather_staging_bytes",
    "Staging bytes by camera and class (sealed: inventoried present chunks)",
    ["camera", "class"],
)
CHUNKS = Gauge(
    "weather_staging_chunks",
    "Present inventoried chunks: eligible, or the first unmet condition that pins them",
    ["camera", "condition"],
)
OLDEST_ELIGIBLE = Gauge(
    "weather_staging_oldest_eligible_chunk_seconds",
    "Age (now - slot window end) of the oldest deletable chunk still present; 0 if none",
    ["camera"],
)
OLDEST_PINNED = Gauge(
    "weather_staging_oldest_pinned_chunk_seconds",
    "Age of the oldest chunk kept by each condition (its first unmet one); 0 if none",
    ["camera", "condition"],
)
OLDEST_UNFINALIZED = Gauge(
    "weather_staging_oldest_unfinalized_segment_seconds",
    "Age past its end of the oldest started (release point set) but unfinalized segment,"
    " current or superseded; its render directory is kept until it finalizes. 0 if none",
    ["camera"],
)
UNFINALIZED_BYTES = Gauge(
    "weather_staging_unfinalized_render_bytes",
    "Render-directory bytes of started but unfinalized segments (kept by cleanup)",
    ["camera"],
)
PINNED_BYTES = Gauge(
    "weather_staging_pinned_bytes",
    "Bytes of present chunks kept by each condition (their first unmet one)",
    ["camera", "condition"],
)
UNINDEXED = Gauge(
    "weather_staging_unindexed_files",
    "Sealed files with no catalog row (invalid or unreadable); never deleted",
    ["camera"],
)
DELETIONS = Counter(
    "weather_staging_deletions_total", "Files and directories deleted", ["camera", "class"]
)
FAILURES = Counter(
    "weather_staging_cleanup_failures_total",
    "Cleanup failures by stage (scan, identity, unlink, unsafe_target, statvfs, pass)",
    ["camera", "stage"],
)
LAST_SUCCESS = Gauge(
    "weather_staging_cleanup_last_success_timestamp_seconds",
    "Wall time of the last complete cleanup pass",
    ["camera"],
)
ENABLED = Gauge(
    "weather_staging_cleanup_enabled", "1 when cleanup deletes; 0 in dry-run", ["camera"]
)


class Mode(StrEnum):
    REQUIRED = "required"
    VACUOUS = "vacuous"


def _flag(value: str) -> bool:
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no", ""}:
        return False
    raise ValueError("Invalid boolean")


@dataclass(frozen=True)
class CleanupSettings:
    enabled: bool = False
    interval_seconds: int = 300
    tmp_hours: int = 6
    preview_refresh_seconds: int = 60
    modes: Mapping[str, Mode] = field(
        default_factory=lambda: dict.fromkeys(CONFIGURABLE, Mode.REQUIRED)
    )
    batch: int = 2000

    def __post_init__(self) -> None:
        if (
            not 10 <= self.interval_seconds <= 3600
            or not 1 <= self.tmp_hours <= 168
            or not 0 <= self.preview_refresh_seconds <= 3600
            or not 1 <= self.batch <= 100_000
            or set(self.modes) != set(CONFIGURABLE)
        ):
            raise ValueError("Invalid cleanup configuration")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> CleanupSettings:
        env = os.environ if environ is None else environ
        try:
            return cls(
                enabled=_flag(env.get("WEATHER_CLEANUP_ENABLED", "false")),
                interval_seconds=int(env.get("WEATHER_CLEANUP_INTERVAL_SECONDS", "300")),
                tmp_hours=int(env.get("WEATHER_CLEANUP_TMP_HOURS", "6")),
                preview_refresh_seconds=int(
                    env.get("WEATHER_CLEANUP_PREVIEW_REFRESH_SECONDS", "60")
                ),
                modes={
                    name: Mode(env.get(f"WEATHER_CLEANUP_{name.upper()}", "required"))
                    for name in CONFIGURABLE
                },
                batch=int(env.get("WEATHER_CLEANUP_BATCH", "2000")),
            )
        except ValueError:
            raise ValueError("Invalid cleanup configuration") from None


# Chunks and their identity -------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRow:
    """One inventoried sealed chunk. `name` is relative to the staging root."""

    id: UUID | None
    camera: str
    name: str
    window_start: datetime
    window_end: datetime
    newest_capture_at: datetime
    frames: int
    bytes: int
    manifest_sha256: str
    # Capture settings from the chunk header: cadence_ms, phase_ms, width, height.
    settings: tuple[int, int, int, int] = (0, 0, 0, 0)


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Naive timestamp")
    return parsed.astimezone(UTC)


def chunk_identity(staging: Path, name: str, camera: str) -> ChunkRow:
    """Header walk plus the manifest member only (no frame bytes are read).

    The manifest binds every frame member by length and sha256 (`capture.chunks`), so its
    sha256 plus the file size identify the chunk's content. Raises ValueError for anything
    that is not a well-formed chunk of this camera and OSError for a share failure."""
    path = staging / name
    size = path.stat().st_size
    manifest: bytes | None = None
    try:
        with path.open("rb") as raw, tarfile.open(fileobj=raw, mode="r:") as archive:
            for member in archive:
                if member.name != "manifest.jsonl":
                    continue
                if manifest is not None or not 0 < member.size <= MAX_MANIFEST_BYTES:
                    raise ValueError("Invalid manifest member")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Invalid manifest member")
                manifest = source.read()
    except tarfile.TarError, EOFError:
        raise ValueError("Invalid TAR archive") from None
    if manifest is None or not manifest.endswith(b"\n"):
        raise ValueError("Missing manifest")
    records = [json.loads(line) for line in manifest.splitlines()]
    header = records[0] if records else None
    if (
        not isinstance(header, dict)
        or header.get("record_type") != "chunk_header"
        or header.get("schema_version") != 2
        or header.get("camera") != camera
    ):
        raise ValueError("Invalid chunk header")
    settings = header.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Invalid chunk settings")
    geometry = settings.get("geometry")
    values = (settings.get("cadence_ms"), settings.get("phase_ms"), *(geometry or (None, None)))
    if not isinstance(geometry, list) or len(values) != 4 or any(
        type(v) is not int for v in values
    ):
        raise ValueError("Invalid chunk settings")
    start = _instant(header.get("slot_window_start_utc"))
    end = _instant(header.get("slot_window_end_utc"))
    if not start < end:
        raise ValueError("Invalid chunk window")
    newest, frames = end, 0
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        if record.get("record_type") == "capture_attempt" and record.get("status") == "captured":
            frames += 1
            for key in ("capture_upper_bound", "source_at_utc"):
                if record.get(key) is not None:
                    newest = max(newest, _instant(record[key]))
    return ChunkRow(
        id=None,
        camera=camera,
        name=name,
        window_start=start,
        window_end=end,
        newest_capture_at=newest,
        frames=frames,
        bytes=size,
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        settings=cast(tuple[int, int, int, int], tuple(values)),
    )


def scan_sealed(staging: Path, camera: str) -> tuple[list[str], int, set[str]]:
    """Relative names of every sealed chunk of the camera, the count of non-chunk entries, and
    the hour directories actually listed.

    Any listing error propagates. A row is only ever marked missing when its own hour
    directory was listed, so a vanished or unmounted root never orphans the inventory."""
    root = staging / "sealed" / camera
    names: list[str] = []
    ignored = 0
    listed: set[str] = set()
    try:
        with os.scandir(root) as entries:
            hours = sorted(e.name for e in entries if e.is_dir(follow_symlinks=False))
    except FileNotFoundError:
        return [], 0, listed
    for hour in hours:
        if not HOUR_DIR.fullmatch(hour):
            ignored += 1
            continue
        with os.scandir(root / hour) as entries:
            for entry in entries:
                if SEALED_FILE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    names.append(f"sealed/{camera}/{hour}/{entry.name}")
                else:
                    ignored += 1
        listed.add(f"sealed/{camera}/{hour}/")
    return names, ignored, listed


def remove_empty_hours(staging: Path, camera: str, before: datetime) -> None:
    """Drop empty sealed hour directories that no collector can write again (rmdir only)."""
    root = staging / "sealed" / camera
    try:
        with os.scandir(root) as entries:
            hours = [
                (e.name, e.stat(follow_symlinks=False).st_mtime)
                for e in entries
                if e.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return
    for hour, mtime in hours:
        if not HOUR_DIR.fullmatch(hour):
            continue
        started = datetime.strptime(hour, "%Y-%m-%dT%HZ").replace(tzinfo=UTC)
        # Both the hour and the directory itself are old: a collector that has just created
        # it (about to seal into it) keeps it.
        if started + timedelta(hours=1) < before and mtime < before.timestamp():
            with contextlib.suppress(OSError):
                os.rmdir(root / hour)


# The four-condition rule ----------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentSpan:
    """A current segment's read range and release point (`pipeline.render.released_through`)."""

    starts_at: datetime
    ends_at: datetime
    mezzanine_through: datetime | None
    mezzanine_durable: bool
    # Finalized: the pipeline never selects the segment again, so it reads nothing more. That
    # is a published or terminal result, or an operator's `weather pipeline abandon`.
    finalized: bool = False

    def complete(self) -> bool:
        return (
            self.finalized
            or self.mezzanine_durable
            or (self.mezzanine_through is not None and self.mezzanine_through >= self.ends_at)
        )

    def released(self, slot_end: datetime) -> bool:
        """No frame whose slot key is before `slot_end` will be read again."""
        return self.complete() or (
            self.mezzanine_through is not None and slot_end <= self.mezzanine_through
        )


def mezzanine_released(chunk: ChunkRow, segments: Sequence[SegmentSpan], lag: timedelta) -> bool:
    """A frame with slot key s has its source time in [s, s + lag), so the chunk's frames can
    belong to any segment overlapping [window_start, window_end + lag). Every such segment
    must have released the chunk's whole slot window, and the frozen schedule must cover that
    range without a gap: a range no current segment covers yet may still be rendered."""
    low, high = chunk.window_start, chunk.window_end + lag
    covered = low
    for span in sorted(segments, key=lambda s: s.starts_at):
        if span.ends_at <= low or span.starts_at >= high:
            continue
        if span.starts_at > covered or not span.released(chunk.window_end):
            return False
        covered = max(covered, span.ends_at)
    return covered >= high


@dataclass(frozen=True)
class Facts:
    """Catalog answers for one chunk. `None` means the feature's query is not wired yet."""

    mezzanine: bool
    keogram: bool | None
    hourly: bool | None
    preview: bool


def decide(facts: Facts, modes: Mapping[str, Mode]) -> str | None:
    """The first unmet condition, or None when the chunk may be deleted. Safe by default: an
    unwired feature keeps the chunk unless its mode is `vacuous`, and `vacuous` never waives a
    condition whose query is wired (its answer is not None)."""
    if not facts.mezzanine:
        return "mezzanine"
    for name in CONFIGURABLE:
        answer = getattr(facts, name)
        if answer is None and modes[name] is Mode.VACUOUS:
            continue
        if answer is not True:
            return name
    if not facts.preview:
        return "preview"
    return None


def check_modes(modes: Mapping[str, Mode]) -> None:
    """Start-up check: a feature that registered its query can no longer be waived."""
    for name, mode in modes.items():
        if mode is Mode.VACUOUS and CONDITION_QUERIES.get(name) is not None:
            raise ValueError(f"Condition {name} is wired; vacuous is not allowed")


def preview_passed(chunk: ChunkRow, now: datetime, refresh_seconds: int) -> bool:
    return chunk.newest_capture_at + PREVIEW_WINDOW + timedelta(seconds=refresh_seconds) < now


# Feature conditions. A query returns the ids of the given chunks whose condition holds; `lag`
# is how far a frame's source time can trail its slot key. #37 registers "keogram".
ConditionQuery = Callable[
    [AsyncConnection, str, Sequence[ChunkRow], timedelta], Awaitable[set[UUID]]
]
HOUR = timedelta(hours=1)
# #38 missing reasons that prove the hour had no usable input (stills.policy.choose), plus
# `no_segment`: the backlog's terminal record for an hour older than the lookback that no
# frozen segment covers, so no still can ever bind there (pipeline.hourly._backlog).
NO_INPUT = ("no_capture", "no_eligible_capture", "no_segment")


def hour_slots(chunk: ChunkRow, lag: timedelta) -> list[datetime]:
    """The UTC hour slots a chunk's frames can be selected for (#38 selects by source time,
    which lies in [window_start, window_end + lag))."""
    start = chunk.window_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    slots = []
    while start < chunk.window_end + lag:
        slots.append(start)
        start += HOUR
    return slots


async def hourly_finalized(
    connection: AsyncConnection, camera: str, chunks: Sequence[ChunkRow], lag: timedelta
) -> set[UUID]:
    """#38: every hour slot the chunk overlaps has an active `archival_still` that is either
    archived (its still publication committed, the publisher having read the original back,
    and its original asset still `available` in `weather-originals`) or `missing` for a reason
    that proves there was no input (`no_capture`, `no_eligible_capture`, `no_segment`).
    `selection_expired` only means no selection ran for 12 h (a Garage or pipeline outage):
    unmet, so the chunk is kept and the 48 h pinned alert fires. A slot without an active row
    is unfinalized."""
    if not chunks:
        return set()
    low = min(c.window_start for c in chunks) - HOUR
    high = max(c.window_end for c in chunks) + lag
    rows = await connection.execute(
        text("""
        SELECT s.slot_starts_at FROM weather.archival_still s
        WHERE s.active AND s.camera_id=:camera AND s.slot_starts_at >= :low
          AND s.slot_starts_at < :high
          AND ((s.state = 'missing' AND s.reason = ANY(CAST(:no_input AS text[])))
               OR (s.publication_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM weather.media_asset a
            WHERE a.id = s.original_asset_id AND a.class = 'original'
              AND a.state = 'available')))
    """),
        {"camera": camera, "low": low, "high": high, "no_input": list(NO_INPUT)},
    )
    done = {row.slot_starts_at for row in rows}
    return {
        c.id
        for c in chunks
        if c.id is not None and all(slot in done for slot in hour_slots(c, lag))
    }


CONDITION_QUERIES: dict[str, ConditionQuery | None] = {
    "keogram": None,
    "hourly": hourly_finalized,
}


# Render directories ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderState:
    segment_id: UUID
    current: bool
    state: str
    ends_at: datetime
    finalized: bool
    published: bool  # a committed publication with all three renditions (read back by #18)
    started: bool = False  # a release point exists: blocks whose input may already be gone


def render_deletable(render: RenderState, now: datetime) -> bool:
    """pipeline.md's release rule. A finalized segment's directory goes once its renditions
    are committed (the publisher reads every byte back before it commits), or once its
    result is terminal without media. An unfinalized segment's directory goes only when the
    pipeline can no longer select it: never started (no release point), terminal or superseded,
    and LATE_RENDER past its end plus a margin for the pipeline pod's clock. An unfinalized
    directory with a release point is never deleted: its blocks are the only copy of frames
    whose raw input cleanup may already have removed. The pipeline keeps such a segment active
    until it finalizes and publishes it (`pipeline.schedule.active`)."""
    if render.finalized:
        return render.published or render.state == "unavailable"
    if render.started or render.ends_at + LATE_RENDER + CLOCK_MARGIN >= now:
        return False
    return not render.current or render.state in {"published", "partial", "unavailable"}


# Catalog --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deletion:
    kind: str  # chunk, tmp, render
    camera: str
    name: str
    bytes: int
    reason: str
    chunk_id: UUID | None = None
    segment_id: UUID | None = None
    sha256: str | None = None
    id: UUID | None = None


class Catalog(Protocol):
    def locked(self, camera: str) -> contextlib.AbstractAsyncContextManager[bool]: ...
    async def now(self) -> datetime: ...
    async def known(self, names: Sequence[str]) -> set[str]: ...
    async def present(self, camera: str) -> list[ChunkRow]: ...
    async def register(self, rows: Sequence[ChunkRow]) -> list[ChunkRow]: ...
    async def missing(self, ids: Sequence[UUID]) -> None: ...
    async def facts(
        self, camera: str, chunks: Sequence[ChunkRow], now: datetime, lag: timedelta,
        refresh_seconds: int,
    ) -> dict[UUID, Facts]: ...
    async def pending(self, camera: str) -> list[Deletion]: ...
    async def decide(self, deletion: Deletion) -> Deletion | None: ...
    async def unlinked(self, deletion: Deletion) -> None: ...
    async def renders(
        self, camera: str, keys: Sequence[tuple[date, str, int]]
    ) -> dict[tuple[date, str, int], RenderState]: ...
    async def oldest_unfinalized(self, camera: str, now: datetime) -> float: ...


class SqlCatalog:
    """The CNPG catalog, as the pipeline's `weather_worker` role."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @contextlib.asynccontextmanager
    async def locked(self, camera: str) -> AsyncIterator[bool]:
        """A session advisory lock per camera, so a manual pass never races the pipeline's."""
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            key = {"key": f"weather.staging_cleanup:{camera}"}
            held = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 19))"), key
                    )
                ).scalar_one()
            )
            try:
                yield held
            finally:
                if held:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:key, 19))"), key
                    )

    async def now(self) -> datetime:
        # The database clock decides ages, never the pipeline node's.
        async with self.engine.connect() as connection:
            value = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        return cast(datetime, value)

    async def known(self, names: Sequence[str]) -> set[str]:
        if not names:
            return set()
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT name FROM weather.staging_chunk WHERE name = ANY(:names)"),
                {"names": list(names)},
            )
            return set(rows.scalars())

    async def present(self, camera: str) -> list[ChunkRow]:
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text("""
                SELECT id,camera_id,name,window_start,window_end,newest_capture_at,frames,bytes,
                  manifest_sha256,cadence_ms,phase_ms,width,height
                FROM weather.staging_chunk WHERE camera_id=:camera AND state='present'
                ORDER BY window_start
            """),
                {"camera": camera},
            )
            return [_chunk(row) for row in rows]

    async def register(self, rows: Sequence[ChunkRow]) -> list[ChunkRow]:
        added: list[ChunkRow] = []
        if not rows:
            return added
        async with self.engine.begin() as connection:
            for row in rows:
                found = (
                    await connection.execute(
                        text("""
                    INSERT INTO weather.staging_chunk (camera_id,name,window_start,window_end,
                      newest_capture_at,frames,bytes,manifest_sha256,cadence_ms,phase_ms,width,
                      height)
                    VALUES (:camera,:name,:start,:end,:newest,:frames,:bytes,:sha,:cadence,
                      :phase,:width,:height)
                    ON CONFLICT (name) DO NOTHING RETURNING id
                """),
                        {
                            "camera": row.camera,
                            "name": row.name,
                            "start": row.window_start,
                            "end": row.window_end,
                            "newest": row.newest_capture_at,
                            "frames": row.frames,
                            "bytes": row.bytes,
                            "sha": row.manifest_sha256,
                            "cadence": row.settings[0],
                            "phase": row.settings[1],
                            "width": row.settings[2],
                            "height": row.settings[3],
                        },
                    )
                ).scalar_one_or_none()
                if found is not None:
                    added.append(replace(row, id=found))
        return added

    async def missing(self, ids: Sequence[UUID]) -> None:
        if not ids:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE weather.staging_chunk SET state='missing',resolved_at=clock_timestamp()
                WHERE id = ANY(CAST(:ids AS uuid[])) AND state='present'
            """),
                {"ids": list(ids)},
            )

    async def facts(
        self,
        camera: str,
        chunks: Sequence[ChunkRow],
        now: datetime,
        lag: timedelta,
        refresh_seconds: int,
    ) -> dict[UUID, Facts]:
        if not chunks:
            return {}
        low = min(c.window_start for c in chunks)
        high = max(c.window_end for c in chunks) + lag
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text("""
                SELECT starts_at,ends_at,mezzanine_through,mezzanine_durable_at IS NOT NULL
                  AS durable,finalized_at IS NOT NULL AS finalized
                FROM weather.segment
                WHERE current AND camera_id=:camera AND starts_at < :high AND ends_at > :low
            """),
                {"camera": camera, "low": low, "high": high},
            )
            spans = [
                SegmentSpan(
                    r.starts_at, r.ends_at, r.mezzanine_through, bool(r.durable),
                    bool(r.finalized),
                )
                for r in rows
            ]
            wired: dict[str, set[UUID] | None] = {}
            for name, query in CONDITION_QUERIES.items():
                wired[name] = (
                    None if query is None else await query(connection, camera, chunks, lag)
                )
        result: dict[UUID, Facts] = {}
        for chunk in chunks:
            assert chunk.id is not None
            keogram, hourly = wired["keogram"], wired["hourly"]
            result[chunk.id] = Facts(
                mezzanine=mezzanine_released(chunk, spans, lag),
                keogram=None if keogram is None else chunk.id in keogram,
                hourly=None if hourly is None else chunk.id in hourly,
                preview=preview_passed(chunk, now, refresh_seconds),
            )
        return result

    async def pending(self, camera: str) -> list[Deletion]:
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text("""
                SELECT id,class,camera_id,name,bytes,reason,chunk_id,segment_id,sha256
                FROM weather.staging_deletion WHERE camera_id=:camera AND unlinked_at IS NULL
                ORDER BY decided_at
            """),
                {"camera": camera},
            )
            return [
                Deletion(
                    kind=str(r._mapping["class"]),
                    camera=str(r.camera_id),
                    name=str(r.name),
                    bytes=int(r.bytes),
                    reason=str(r.reason),
                    chunk_id=r.chunk_id,
                    segment_id=r.segment_id,
                    sha256=r.sha256,
                    id=r.id,
                )
                for r in rows
            ]

    async def decide(self, deletion: Deletion) -> Deletion | None:
        """Write the audit row (and resolve the chunk row) before anything is unlinked."""
        async with self.engine.begin() as connection:
            found = (
                await connection.execute(
                    text("""
                INSERT INTO weather.staging_deletion (class,camera_id,name,chunk_id,segment_id,
                  sha256,bytes,reason)
                VALUES (:kind,:camera,:name,:chunk,:segment,:sha,:bytes,:reason)
                ON CONFLICT (name) DO NOTHING RETURNING id
            """),
                    {
                        "kind": deletion.kind,
                        "camera": deletion.camera,
                        "name": deletion.name,
                        "chunk": deletion.chunk_id,
                        "segment": deletion.segment_id,
                        "sha": deletion.sha256,
                        "bytes": deletion.bytes,
                        "reason": deletion.reason,
                    },
                )
            ).scalar_one_or_none()
            if found is None:
                return None
            if deletion.chunk_id is not None:
                updated = await connection.execute(
                    text("""
                    UPDATE weather.staging_chunk SET state='deleted',resolved_at=clock_timestamp()
                    WHERE id=:id AND state='present'
                """),
                    {"id": deletion.chunk_id},
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Chunk row changed during cleanup")
        return replace(deletion, id=found)

    async def unlinked(self, deletion: Deletion) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE weather.staging_deletion SET unlinked_at=clock_timestamp()
                WHERE id=:id AND unlinked_at IS NULL
            """),
                {"id": deletion.id},
            )

    async def oldest_unfinalized(self, camera: str, now: datetime) -> float:
        return await _oldest_unfinalized(self.engine, camera, now)

    async def renders(
        self, camera: str, keys: Sequence[tuple[date, str, int]]
    ) -> dict[tuple[date, str, int], RenderState]:
        """One query for every render directory found (#19 review: no per-directory query)."""
        result: dict[tuple[date, str, int], RenderState] = {}
        if not keys:
            return result
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                text("""
                SELECT s.cycle_date,s.kind,s.revision,s.id,s.current,s.state,s.ends_at,
                  s.finalized_at IS NOT NULL AS done,
                  (s.mezzanine_through IS NOT NULL OR s.mezzanine_durable_at IS NOT NULL)
                    AS started,
                  EXISTS (
                    SELECT 1 FROM weather.video v
                    JOIN weather.publication p ON p.id = v.publication_id
                    WHERE v.camera_id = s.camera_id AND v.cycle_date = s.cycle_date
                      AND v.segment = s.kind AND p.state IN ('published','partial')
                      AND (SELECT count(*) FROM weather.publication_asset pa
                           WHERE pa.publication_id = p.id
                             AND pa.role IN ('native','web','mobile')) = 3
                  ) AS published
                FROM weather.segment s
                JOIN unnest(CAST(:days AS date[]),CAST(:kinds AS text[]),
                            CAST(:revisions AS integer[])) AS k(cycle_date,kind,revision)
                  ON s.cycle_date = k.cycle_date AND s.kind = k.kind AND s.revision = k.revision
                WHERE s.camera_id = :camera
            """),
                {
                    "camera": camera,
                    "days": [k[0] for k in keys],
                    "kinds": [k[1] for k in keys],
                    "revisions": [k[2] for k in keys],
                },
            )
            for row in rows:
                result[(row.cycle_date, str(row.kind), int(row.revision))] = RenderState(
                    segment_id=row.id,
                    current=bool(row.current),
                    state=str(row.state),
                    ends_at=row.ends_at,
                    finalized=bool(row.done),
                    published=bool(row.published),
                    started=bool(row.started),
                )
        return result


async def _oldest_unfinalized(engine: AsyncEngine, camera: str, now: datetime) -> float:
    async with engine.connect() as connection:
        ends = (
            await connection.execute(
                text("""
            SELECT min(ends_at) FROM weather.segment
            WHERE camera_id=:camera AND finalized_at IS NULL AND ends_at < :now
              AND (mezzanine_through IS NOT NULL OR mezzanine_durable_at IS NOT NULL)
        """),
                {"camera": camera, "now": now},
            )
        ).scalar_one()
    return 0.0 if ends is None else max(0.0, (now - ends).total_seconds())


def _chunk(row: Any) -> ChunkRow:
    return ChunkRow(
        id=row.id,
        camera=str(row.camera_id),
        name=str(row.name),
        window_start=row.window_start,
        window_end=row.window_end,
        newest_capture_at=row.newest_capture_at,
        frames=int(row.frames),
        bytes=int(row.bytes),
        manifest_sha256=str(row.manifest_sha256),
        settings=(int(row.cadence_ms), int(row.phase_ms), int(row.width), int(row.height)),
    )


# Filesystem helpers (run in a worker thread; a soft-mounted share may block or fail) ----------


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def tree_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(FileNotFoundError):
                total += os.lstat(os.path.join(root, name)).st_size
    return total


def scan_tmp(staging: Path, camera: str, before: datetime) -> tuple[list[tuple[str, int]], int]:
    """Other incarnations' tmp files: both the name's slot-window end and the mtime are older
    than `before`. A live writer seals within its <= 300 s window and appends every cadence,
    so its file is never this old."""
    cutoff = int(before.timestamp() * 1_000_000_000)
    root = staging / "tmp" / camera
    stale: list[tuple[str, int]] = []
    total = 0
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        return [], 0
    for entry in entries:
        try:
            stat = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        total += stat.st_size
        match = TMP_FILE.fullmatch(entry.name)
        if match is None or not entry.is_file(follow_symlinks=False):
            continue
        if int(match[2]) < cutoff and stat.st_mtime_ns < cutoff:
            stale.append((f"tmp/{camera}/{entry.name}", stat.st_size))
    return stale, total


def scan_render(staging: Path, camera: str) -> list[tuple[str, tuple[date, str, int]]]:
    root = staging / "render" / camera
    found: list[tuple[str, tuple[date, str, int]]] = []
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        return []
    for entry in entries:
        match = RENDER_DIR.fullmatch(entry.name)
        if match is None or not entry.is_dir(follow_symlinks=False):
            continue
        try:
            key = (date.fromisoformat(match[1]), match[2], int(match[3]))
        except ValueError:
            continue
        found.append((f"render/{camera}/{entry.name}", key))
    return found


TARGETS = {
    "chunk": re.compile(
        r"sealed/(?P<camera>[a-z0-9_]+)/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}Z/[A-Za-z0-9_-]{1,120}\.tar\Z"
    ),
    "tmp": re.compile(r"tmp/(?P<camera>[a-z0-9_]+)/" + TMP_FILE.pattern),
    "render": re.compile(r"render/(?P<camera>[a-z0-9_]+)/" + RENDER_DIR.pattern),
}


class UnsafeTarget(ValueError):
    """A decided deletion names something outside its class's own camera directory."""


def target_path(staging: Path, deletion: Deletion) -> Path:
    """Re-validate a deletion taken from the database before touching the share: the name must
    match its class's pattern for its own camera, and every component below the staging root
    must be a real directory (no symlinks), so a bad row can never reach raw input."""
    pattern = TARGETS.get(deletion.kind)
    match = pattern.fullmatch(deletion.name) if pattern is not None else None
    if match is None or match["camera"] != deletion.camera or deletion.camera not in CAMERAS:
        raise UnsafeTarget("Deletion target does not match its class")
    path = staging
    parts = deletion.name.split("/")
    for index, part in enumerate(parts):
        path = path / part
        if index == len(parts) - 1:
            break
        info = os.lstat(path)  # FileNotFoundError propagates: the target is gone
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeTarget("Deletion target crosses a non-directory")
    return path


def remove(staging: Path, deletion: Deletion) -> bool:
    """Unlink one decided target. True if it was removed or was already gone. A chunk is
    re-identified first and kept if its manifest hash or size differs from the audit row; a
    target that fails validation raises UnsafeTarget and is never touched."""
    try:
        path = target_path(staging, deletion)
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    expected_dir = deletion.kind == "render"
    if stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode) != expected_dir:
        raise UnsafeTarget("Deletion target has the wrong type")
    if deletion.kind == "chunk":
        try:
            found = chunk_identity(staging, deletion.name, deletion.camera)
        except FileNotFoundError:
            return True
        except ValueError:
            return False
        if (found.manifest_sha256, found.bytes) != (deletion.sha256, deletion.bytes):
            return False
    try:
        if expected_dir:
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except FileNotFoundError:
        return True
    _fsync_dir(path.parent)
    return True


# The pass -------------------------------------------------------------------------------------


@dataclass
class Report:
    counts: Tally[str] = field(default_factory=Tally)

    def add(self, key: str, value: int = 1) -> None:
        self.counts[key] += value

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


class Cleanup:
    """One camera's cleanup pass. Idempotent; safe to run from a crashed state."""

    def __init__(
        self,
        camera: str,
        staging: Path,
        catalog: Catalog,
        settings: CleanupSettings,
        *,
        lag: timedelta,
        expected: tuple[int, int, int, int],
        remover: Callable[[Path, Deletion], bool] = remove,
    ) -> None:
        """`expected` is the pipeline's capture grid and geometry: cadence_ms, phase_ms,
        width, height. A chunk recorded with other settings is pinned (`config`)."""
        if camera not in CAMERAS:
            raise ValueError("Unknown camera")
        check_modes(settings.modes)
        self.camera, self.staging, self.catalog, self.settings = camera, staging, catalog, settings
        self.lag, self.remover, self.expected = lag, remover, expected
        self.healthy = True
        self._sizes: dict[str, tuple[tuple[int, ...], int]] = {}
        ENABLED.labels(camera).set(int(settings.enabled))
        OLDEST_UNFINALIZED.labels(camera)
        UNFINALIZED_BYTES.labels(camera)
        for name in CONDITIONS:
            OLDEST_PINNED.labels(camera, name)
            PINNED_BYTES.labels(camera, name)
            CHUNKS.labels(camera, name)
        CHUNKS.labels(camera, "eligible")
        for kind in ("chunk", "tmp", "render"):
            DELETIONS.labels(camera, kind)

    def _render_bytes(self, name: str) -> int:
        """Directory size, re-walked only when the directory or its blocks/ and out/ change
        (a finished segment's directory is static), so frequent urgent passes stay cheap."""
        path = self.staging / name
        stamp = []
        for part in (path, path / "blocks", path / "out"):
            try:
                stamp.append(os.stat(part).st_mtime_ns)
            except FileNotFoundError:
                stamp.append(0)
        cached = self._sizes.get(name)
        if cached is not None and cached[0] == tuple(stamp):
            return cached[1]
        size = tree_bytes(path)
        self._sizes[name] = (tuple(stamp), size)
        return size

    async def _thread(self, function: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(function, *args)

    async def run(self, *, dry_run: bool = False) -> dict[str, int]:
        apply = self.settings.enabled and not dry_run
        report = Report()
        try:
            async with self.catalog.locked(self.camera) as held:
                if not held:
                    report.add("skipped_locked")
                    return report.as_dict()
                await self._pass(apply, report)
        except Exception:
            self.healthy = False
            FAILURES.labels(self.camera, "pass").inc()
            raise
        self.healthy = True
        LAST_SUCCESS.labels(self.camera).set(datetime.now(UTC).timestamp())
        return report.as_dict()

    async def _execute(self, deletion: Deletion, report: Report) -> None:
        try:
            done = await self._thread(self.remover, self.staging, deletion)
        except UnsafeTarget:
            # A row that names something outside its class's camera directory: never act.
            FAILURES.labels(self.camera, "unsafe_target").inc()
            report.add("unsafe_target")
            LOG.error("Refused an unsafe staging deletion target (%s)", self.camera)
            return
        except OSError:
            FAILURES.labels(self.camera, "unlink").inc()
            raise
        if not done:
            # Never delete a chunk whose content no longer matches its decision.
            FAILURES.labels(self.camera, "identity").inc()
            report.add("identity_mismatch")
            return
        await self.catalog.unlinked(deletion)
        DELETIONS.labels(self.camera, deletion.kind).inc()
        report.add(f"deleted_{deletion.kind}")

    async def _same(self, row: ChunkRow, report: Report) -> bool:
        """Re-identify a chunk before deciding: a changed file is never deleted."""
        try:
            found = await self._thread(chunk_identity, self.staging, row.name, row.camera)
        except FileNotFoundError:
            # Possibly transient on a soft mount: skip it now; only a listing of its own hour
            # directory may mark the row missing.
            report.add("vanished")
            return False
        except ValueError:
            found = None
        if found is None or (found.manifest_sha256, found.bytes) != (
            row.manifest_sha256,
            row.bytes,
        ):
            FAILURES.labels(self.camera, "identity").inc()
            report.add("identity_mismatch")
            return False
        return True

    async def _pass(self, apply: bool, report: Report) -> None:
        camera, settings = self.camera, self.settings
        now = await self.catalog.now()
        # 1. Finish deletions a crash left between decision and unlink.
        if apply:
            for deletion in await self.catalog.pending(camera):
                report.add("resumed")
                await self._execute(deletion, report)
        # 2. Inventory: every well-formed chunk gets a row; nothing without one is touched.
        try:
            names, ignored, hours = await self._thread(scan_sealed, self.staging, camera)
        except OSError:
            FAILURES.labels(camera, "scan").inc()
            raise
        known = await self.catalog.known(names)
        fresh: list[ChunkRow] = []
        for name in names:
            if name in known:
                continue
            if len(fresh) >= settings.batch:
                report.add("inventory_deferred")
                continue
            try:
                fresh.append(await self._thread(chunk_identity, self.staging, name, camera))
            except FileNotFoundError, ValueError:
                # Not a well-formed chunk of this camera: it gets no row and is never touched.
                continue
            except OSError:
                FAILURES.labels(camera, "identity").inc()
                report.add("identity_io_error")
        added = await self.catalog.register(fresh)
        report.add("inventoried", len(added))
        listed = set(names)
        present = await self.catalog.present(camera)
        gone = [
            row.id
            for row in present
            if row.name not in listed
            and row.name.rsplit("/", 1)[0] + "/" in hours
            and row.id is not None
        ]
        await self.catalog.missing(gone)
        report.add("missing", len(gone))
        rows = [row for row in present if row.name in listed]
        report.add("unlisted", len(present) - len(rows) - len(gone))
        # Listed files without a present row: invalid, unreadable, or a resolved row's name.
        unindexed = ignored + len(listed - {row.name for row in rows})
        UNINDEXED.labels(camera).set(unindexed)
        report.add("unindexed", unindexed)
        # 3. Evaluate the four conditions from catalog rows.
        facts = await self.catalog.facts(
            camera, rows, now, self.lag, settings.preview_refresh_seconds
        )
        eligible: list[ChunkRow] = []
        pinned: dict[str, list[ChunkRow]] = {name: [] for name in CONDITIONS}
        for row in rows:
            assert row.id is not None
            unmet = (
                "config" if row.settings != self.expected else decide(facts[row.id], settings.modes)
            )
            if unmet is None:
                eligible.append(row)
            else:
                pinned[unmet].append(row)

        def age(chunks: list[ChunkRow]) -> float:
            if not chunks:
                return 0.0
            return max(0.0, (now - min(c.window_end for c in chunks)).total_seconds())

        CHUNKS.labels(camera, "eligible").set(len(eligible))
        OLDEST_ELIGIBLE.labels(camera).set(age(eligible))
        for name, kept in pinned.items():
            CHUNKS.labels(camera, name).set(len(kept))
            OLDEST_PINNED.labels(camera, name).set(age(kept))
            PINNED_BYTES.labels(camera, name).set(sum(c.bytes for c in kept))
            report.add(f"pinned_{name}", len(kept))
        report.add("eligible", len(eligible))
        sealed_bytes = sum(row.bytes for row in rows)
        # 4. Delete eligible chunks (decision row first, then unlink).
        if apply:
            for row in eligible:
                assert row.id is not None
                if not await self._same(row, report):
                    continue
                decision = await self.catalog.decide(
                    Deletion(
                        kind="chunk",
                        camera=camera,
                        name=row.name,
                        bytes=row.bytes,
                        reason="p3",
                        chunk_id=row.id,
                        sha256=row.manifest_sha256,
                    )
                )
                if decision is not None:
                    await self._execute(decision, report)
                    sealed_bytes -= row.bytes
            await self._thread(
                remove_empty_hours, self.staging, camera, now - timedelta(hours=settings.tmp_hours)
            )
        BYTES.labels(camera, "sealed").set(sealed_bytes)
        # 5. Other incarnations' stale tmp files.
        before = now - timedelta(hours=settings.tmp_hours)
        stale, tmp_bytes = await self._thread(scan_tmp, self.staging, camera, before)
        report.add("tmp_stale", len(stale))
        for name, size in stale:
            LOG.info("Stale tmp file from another incarnation (%s, %d bytes)", camera, size)
            if apply:
                decision = await self.catalog.decide(
                    Deletion(kind="tmp", camera=camera, name=name, bytes=size, reason="stale_tmp")
                )
                if decision is not None:
                    await self._execute(decision, report)
                    tmp_bytes -= size
        BYTES.labels(camera, "tmp").set(max(0, tmp_bytes))
        # 6. Render directories of finished segments.
        found = await self._thread(scan_render, self.staging, camera)
        states = await self.catalog.renders(camera, [key for _, key in found])
        render_bytes = stuck_bytes = 0
        for name, key in found:
            size = await self._thread(self._render_bytes, name)
            state = states.get(key)
            if state is not None and state.started and not state.finalized:
                stuck_bytes += size
            if state is None or not render_deletable(state, now):
                render_bytes += size
                continue
            report.add("render_eligible")
            if not apply:
                render_bytes += size
                continue
            decision = await self.catalog.decide(
                Deletion(
                    kind="render",
                    camera=camera,
                    name=name,
                    bytes=size,
                    reason="finalized" if state.finalized else "late_render",
                    segment_id=state.segment_id,
                )
            )
            if decision is not None:
                await self._execute(decision, report)
            else:
                render_bytes += size
        BYTES.labels(camera, "render").set(render_bytes)
        UNFINALIZED_BYTES.labels(camera).set(stuck_bytes)
        oldest = await self.catalog.oldest_unfinalized(camera, now)
        OLDEST_UNFINALIZED.labels(camera).set(oldest)
        LOG.info("Staging cleanup pass (%s, %s): %s", camera, "apply" if apply else "dry-run",
                 json.dumps(report.as_dict(), sort_keys=True))


class Housekeeping:
    """The pipeline loop's hook: watermark every loop, cleanup every interval, or every loop
    (ahead of rendering) under urgent pressure. Failures are counted, never fatal."""

    def __init__(self, cleanup: Cleanup, pressure: Pressure) -> None:
        self.cleanup, self.pressure = cleanup, pressure
        self.next_pass = 0.0
        self.last_pass = float("-inf")
        self._statvfs: asyncio.Task[Any] | None = None

    async def _level(self) -> None:
        # One statvfs in flight at most: a hung soft mount must not pile up threads.
        if self._statvfs is not None and self._statvfs.done() and not self._statvfs.cancelled():
            self._statvfs.exception()  # retrieved here; a timed-out read may finish later
            self._statvfs = None
        if self._statvfs is None:
            self._statvfs = asyncio.ensure_future(
                asyncio.to_thread(self.pressure.update, dependency_healthy=self.cleanup.healthy)
            )
        try:
            await asyncio.wait_for(asyncio.shield(self._statvfs), 30)
        except (OSError, TimeoutError):
            FAILURES.labels(self.cleanup.camera, "statvfs").inc()

    def render_allowed(self) -> bool:
        """Above the render stop (97 %) the pipeline adds no mezzanine bytes (finalize still
        runs). Below it rendering continues even at the 90 % collector stop: it frees input."""
        return self.pressure.render_allowed()

    async def tick(self, monotonic: float) -> None:
        await self._level()
        if monotonic < self.next_pass and not (
            self.pressure.urgent and monotonic >= self.last_pass + URGENT_INTERVAL
        ):
            return
        self.last_pass = monotonic
        self.next_pass = monotonic + self.cleanup.settings.interval_seconds
        try:
            await self.cleanup.run()
        except Exception as error:
            # Never log paths or SQL; the type and camera suffice.
            LOG.error("Staging cleanup failed (%s, %s)", self.cleanup.camera, type(error).__name__)
