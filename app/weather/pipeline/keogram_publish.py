# SPDX-License-Identifier: MIT
"""Keogram bins in the catalog, boundary assembly, publication through #18 and status.

Bins stream into `keogram_bin` from the render thread before each block becomes durable
(`BinStore`), so a raw chunk's keogram duty is done with its block. After the segment's video
is finalized, `Keograms.sweep` assembles the PNG and thumbnail from the bins alone (the raw
chunks may be gone), publishes them as product `keogram`, and, once the commit returns the
read-back assets, deletes the segment's bins and records the status. The video is never held
back or withdrawn by a keogram: it publishes with its keogram `pending`, and its sidecar gets
the final keogram status as a sidecar-only revision.
"""

import asyncio
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.publish import catalog
from weather.publish.model import EMBARGO, Product, ProductFile
from weather.publish.publisher import Publisher

from . import metrics, schedule
from .keogram import (
    BINS,
    Assembled,
    KeogramError,
    KeogramStage,
    Region,
    Sample,
    assemble,
    instant,
    micros,
    sidecar,
)
from .render import SegmentJob

LOG = logging.getLogger("weather.pipeline")
MAX_ATTEMPTS = 3
SINK_TIMEOUT = 120
PUBLISH_DEADLINE = timedelta(hours=1)
# Deterministic database refusals: retrying the block cannot help, so the keogram fails alone.
REFUSED = (IntegrityError, DataError, ProgrammingError)

SEGMENT = """SELECT id FROM weather.segment WHERE camera_id=:camera AND cycle_date=:cycle_date
             AND kind=:kind AND revision=:revision"""


def _segment(job: SegmentJob) -> dict[str, Any]:
    return {
        "camera": job.camera,
        "cycle_date": job.cycle_date,
        "kind": job.kind,
        "revision": job.revision,
    }


async def fail(
    connection: AsyncConnection, segment_id: UUID, reason: str, *, state: str = "failed"
) -> None:
    """A final status without a keogram; the working bins go with it."""
    await connection.execute(
        text("""
        UPDATE weather.keogram SET state=:state,reason=:reason,updated_at=clock_timestamp()
        WHERE segment_id=:id AND state='pending'
    """),
        {"id": segment_id, "state": state, "reason": reason},
    )
    await connection.execute(
        text("DELETE FROM weather.keogram_bin WHERE segment_id=:id"), {"id": segment_id}
    )


async def write_bins(
    connection: AsyncConnection,
    job: SegmentJob,
    region: Region,
    samples: list[Sample],
    failed: str | None,
) -> None:
    """Merge one block's bins: a stored bin is replaced only by a nearer (then earlier, then
    lower-ID) capture, so re-rendered blocks and bins split across blocks converge."""
    await connection.execute(
        text(f"""
        INSERT INTO weather.keogram (segment_id,region_version,region)
        SELECT id,CAST(:version AS varchar),CAST(:region AS jsonb) FROM ({SEGMENT}) s
        ON CONFLICT (segment_id) DO NOTHING
    """),
        {**_segment(job), "version": region.version, "region": json.dumps(region.record())},
    )
    row = (
        await connection.execute(
            text(f"""
        SELECT k.segment_id,k.state,k.region_version FROM weather.keogram k
        WHERE k.segment_id=({SEGMENT})
    """),
            _segment(job),
        )
    ).one_or_none()
    if row is None or row.state != "pending":
        return
    segment_id = cast(UUID, row.segment_id)
    if failed is not None:
        await fail(connection, segment_id, failed)
        return
    if row.region_version != region.version:
        # The region changed mid-segment: never mix two regions in one keogram.
        await fail(connection, segment_id, "region_changed")
        return
    if not samples:
        return
    await connection.execute(
        text("""
        INSERT INTO weather.keogram_bin AS b (segment_id,bin,distance,captured_at,capture_id,
          capture_upper_bound,uncertainty_ms,strip)
        SELECT CAST(:id AS uuid),* FROM unnest(CAST(:bins AS smallint[]),
          CAST(:distances AS bigint[]),CAST(:captured AS timestamptz[]),CAST(:ids AS text[]),
          CAST(:uppers AS timestamptz[]),
          CAST(:uncertainty AS double precision[]),CAST(:strips AS bytea[]))
        ON CONFLICT (segment_id,bin) DO UPDATE SET distance=EXCLUDED.distance,
          captured_at=EXCLUDED.captured_at,capture_id=EXCLUDED.capture_id,
          capture_upper_bound=EXCLUDED.capture_upper_bound,
          uncertainty_ms=EXCLUDED.uncertainty_ms,strip=EXCLUDED.strip
        WHERE (EXCLUDED.distance,EXCLUDED.captured_at,EXCLUDED.capture_id)
            < (b.distance,b.captured_at,b.capture_id)
    """),
        {
            "id": segment_id,
            "bins": [s.bin for s in samples],
            "distances": [s.distance for s in samples],
            "captured": [instant(s.captured) for s in samples],
            "ids": [s.capture_id for s in samples],
            "uppers": [instant(s.upper) for s in samples],
            "uncertainty": [s.uncertainty_ms for s in samples],
            "strips": [s.strip for s in samples],
        },
    )


class BinStore:
    """The keogram stage's sink, called from the render thread; it runs on the event loop.

    Connectivity errors propagate, so the block is retried (the video needs the database
    too). A refusal by the database fails only this segment's keogram."""

    def __init__(self, engine: AsyncEngine, loop: asyncio.AbstractEventLoop) -> None:
        self.engine, self.loop = engine, loop

    def __call__(
        self, job: SegmentJob, region: Region, samples: list[Sample], failed: str | None
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self.write(job, region, samples, failed), self.loop
        )
        future.result(timeout=SINK_TIMEOUT)

    async def write(
        self, job: SegmentJob, region: Region, samples: list[Sample], failed: str | None
    ) -> None:
        try:
            async with self.engine.begin() as connection:
                await write_bins(connection, job, region, samples, failed)
        except REFUSED as error:
            metrics.KEOGRAMS.labels(job.camera, "failed").inc()
            LOG.error("Keogram bins refused (%s, %s)", job.camera, type(error).__name__)
            async with self.engine.begin() as connection:
                segment = (await connection.execute(text(SEGMENT), _segment(job))).scalar()
                if segment is not None:
                    await fail(connection, segment, "store")


@dataclass(frozen=True)
class Due:
    segment_id: UUID
    state: str
    reason: str | None
    attempts: int
    expected_revision: int | None
    video_synced: bool
    finalized: bool
    region: Region
    job: SegmentJob


async def due(engine: AsyncEngine, camera: str) -> list[Due]:
    """This camera's keograms with work left: a pending keogram whose video is finalized (or
    whose segment is past the late-render window), or a final status not yet in its finalized
    video's sidecar (never before: the video's own publish must not race a sidecar revision)."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("""
            SELECT k.segment_id,k.state,k.reason,k.attempts,k.expected_revision,k.video_synced,
              k.region,s.finalized_at IS NOT NULL AS finalized,s.cycle_date,s.kind,s.revision,
              s.starts_at,s.ends_at,s.frames
            FROM weather.keogram k JOIN weather.segment s ON s.id=k.segment_id
            WHERE s.camera_id=:camera AND s.current
              AND ((k.state='pending' AND (s.finalized_at IS NOT NULL
                     OR s.ends_at < clock_timestamp() - CAST(:late AS interval)))
                   OR (k.state<>'pending' AND NOT k.video_synced
                       AND s.finalized_at IS NOT NULL))
            ORDER BY s.starts_at LIMIT 12
        """),
            {"camera": camera, "late": schedule.LATE_RENDER},
        )
        return [
            Due(
                segment_id=cast(UUID, row.segment_id),
                state=str(row.state),
                reason=row.reason,
                attempts=int(row.attempts),
                expected_revision=row.expected_revision,
                video_synced=bool(row.video_synced),
                finalized=bool(row.finalized),
                region=Region.model_validate(row.region),
                job=SegmentJob(
                    camera=camera,
                    cycle_date=row.cycle_date,
                    kind=str(row.kind),
                    revision=int(row.revision),
                    starts_at=row.starts_at,
                    ends_at=row.ends_at,
                    frames=int(row.frames),
                ),
            )
            for row in rows
        ]


async def load_bins(engine: AsyncEngine, segment_id: UUID) -> list[Sample]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("""
            SELECT bin,distance,captured_at,capture_id,capture_upper_bound,uncertainty_ms,strip
            FROM weather.keogram_bin WHERE segment_id=:id ORDER BY bin
        """),
            {"id": segment_id},
        )
        return [
            Sample(
                bin=int(row.bin),
                distance=int(row.distance),
                captured=micros(row.captured_at),
                capture_id=str(row.capture_id),
                upper=micros(row.capture_upper_bound),
                uncertainty_ms=row.uncertainty_ms,
                strip=bytes(row.strip),
            )
            for row in rows
        ]


def keogram_identity(job: SegmentJob) -> Product:
    return Product.keogram(
        camera_id=job.camera,
        cycle_date=job.cycle_date,
        segment=job.kind,
        source_start=job.starts_at,
        source_end=job.ends_at,
        public_not_before=job.ends_at + EMBARGO,
        files=(),
    )


def embargo(job: SegmentJob, assembled: Assembled) -> datetime:
    """The newest bin's capture upper bound + 600 s + 30 s skew, and never before the segment
    end + 630 s that the catalog guards on every public product."""
    return max(job.ends_at, instant(assembled.upper)) + EMBARGO


class Keograms:
    """One camera's keogram stage plus the boundary work that follows its videos."""

    def __init__(
        self,
        engine: AsyncEngine,
        publisher: Publisher,
        region: Region,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.engine, self.publisher, self.region = engine, publisher, region
        self.stage = KeogramStage(region, BinStore(engine, loop))
        self.backoff: dict[UUID, datetime] = {}

    async def sweep(self, camera: str, now: datetime) -> None:
        for item in await due(self.engine, camera):
            if now < self.backoff.get(item.segment_id, now):
                continue
            try:
                await self.process(item)
                self.backoff.pop(item.segment_id, None)
            except Exception as error:
                metrics.FAILURES.labels(camera, "keogram").inc()
                LOG.error("Keogram failed (%s, %s)", camera, type(error).__name__)
                self.backoff[item.segment_id] = now + timedelta(seconds=60)
                if item.state == "pending" and await self._attempt_failed(item):
                    # Final: the video's sidecar says so now (or on a later pass).
                    try:
                        await self.sync_video(item, "failed", "publish_failed")
                    except Exception as sync_error:
                        LOG.error("Keogram status sync failed (%s)", type(sync_error).__name__)

    async def _attempt_failed(self, item: Due) -> bool:
        """Count a failed attempt; returns True when it made the keogram `failed`."""
        async with self.engine.begin() as connection:
            attempts = (
                await connection.execute(
                    text("""
                UPDATE weather.keogram SET attempts=attempts+1,updated_at=clock_timestamp()
                WHERE segment_id=:id AND state='pending' RETURNING attempts
            """),
                    {"id": item.segment_id},
                )
            ).scalar()
            if attempts is None or attempts < MAX_ATTEMPTS:
                return False
            await fail(connection, item.segment_id, "publish_failed")
        metrics.KEOGRAMS.labels(item.job.camera, "failed").inc()
        return True

    async def process(self, item: Due) -> None:
        state, reason = item.state, item.reason
        if state == "pending":
            state, reason = await self.finish(item)
        if state != "pending" and not item.video_synced:
            await self.sync_video(item, state, reason)

    async def finish(self, item: Due) -> tuple[str, str | None]:
        job = item.job
        if not item.finalized:
            # The segment never finished rendering inside its late-render window.
            async with self.engine.begin() as connection:
                await fail(connection, item.segment_id, "deadline_missed")
            metrics.KEOGRAMS.labels(job.camera, "failed").inc()
            return "failed", "deadline_missed"
        samples = await load_bins(self.engine, item.segment_id)
        if not samples:
            async with self.engine.begin() as connection:
                await fail(connection, item.segment_id, "no_observations", state="unavailable")
            metrics.KEOGRAMS.labels(job.camera, "unavailable").inc()
            return "unavailable", "no_observations"
        region = item.region
        assembled = await asyncio.to_thread(assemble, samples, region.height)
        state, reason = assembled.state
        public_not_before = embargo(job, assembled)
        document = sidecar(job, region, samples, assembled, public_not_before)
        expected = await self._expected_revision(item)
        with tempfile.TemporaryDirectory(prefix="keogram-") as directory:
            root = Path(directory)
            (root / "keogram.png").write_bytes(assembled.png)
            (root / "thumbnail.png").write_bytes(assembled.thumbnail)
            product = Product.keogram(
                camera_id=job.camera,
                cycle_date=job.cycle_date,
                segment=job.kind,
                source_start=job.starts_at,
                source_end=job.ends_at,
                public_not_before=public_not_before,
                state=state,
                reason=reason,
                files=(
                    ProductFile(
                        "keogram",
                        "png",
                        path=root / "keogram.png",
                        width=assembled.width,
                        height=assembled.height,
                    ),
                    ProductFile(
                        "keogram_thumb",
                        "png",
                        path=root / "thumbnail.png",
                        width=assembled.thumb_width,
                        height=assembled.thumb_height,
                    ),
                    ProductFile.sidecar(document),
                ),
            )
            try:
                committed = await self.publisher.publish(
                    product,
                    expected_revision=expected,
                    deadline=datetime.now(UTC) + PUBLISH_DEADLINE,
                )
            except catalog.Withdrawn:
                # Its video was withdrawn: the keogram never publishes; its bins go too.
                await self._resolve(item, "unavailable", "withdrawn", None, None)
                return "unavailable", "withdrawn"
            except catalog.RevisionConflict:
                # Another revision committed under us: the next attempt builds on it.
                async with self.engine.begin() as connection:
                    await connection.execute(
                        text("""
                    UPDATE weather.keogram SET expected_revision=NULL WHERE segment_id=:id
                """),
                        {"id": item.segment_id},
                    )
                raise
        if not {"keogram", "keogram_thumb", "sidecar"} <= set(committed.assets):
            raise KeogramError("Committed keogram lacks an asset")
        await self._resolve(item, state, reason, assembled, public_not_before)
        metrics.KEOGRAMS.labels(job.camera, state).inc()
        metrics.KEOGRAM_MISSING_BINS.labels(job.camera).set(BINS - assembled.observed)
        metrics.KEOGRAM_LAG.labels(job.camera).observe(
            (datetime.now(UTC) - job.ends_at).total_seconds()
        )
        return state, reason

    async def _expected_revision(self, item: Due) -> int:
        """Recorded once, before the first upload, so a retry after a lost acknowledgement is
        recognized by #18 as the same content instead of becoming a second revision."""
        if item.expected_revision is not None:
            return item.expected_revision
        async with self.engine.begin() as connection:
            current = await catalog.current(connection, keogram_identity(item.job))
            stored = (
                await connection.execute(
                    text("""
                UPDATE weather.keogram SET expected_revision=COALESCE(expected_revision,:rev)
                WHERE segment_id=:id RETURNING expected_revision
            """),
                    {"id": item.segment_id, "rev": current.revision},
                )
            ).scalar_one()
        return int(stored)

    async def _resolve(
        self,
        item: Due,
        state: str,
        reason: str | None,
        assembled: Assembled | None,
        public_not_before: datetime | None,
    ) -> None:
        """Record the final status and drop the bins in one transaction."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE weather.keogram SET state=:state,reason=:reason,observed=:observed,
                  gaps=CAST(:gaps AS jsonb),lineage_sha256=:lineage,
                  capture_upper_bound=:upper,public_not_before=:pnb,updated_at=clock_timestamp()
                WHERE segment_id=:id AND state='pending'
            """),
                {
                    "id": item.segment_id,
                    "state": state,
                    "reason": reason,
                    "observed": assembled.observed if assembled else None,
                    "gaps": json.dumps([list(g) for g in assembled.gaps]) if assembled else None,
                    "lineage": assembled.lineage if assembled else None,
                    "upper": instant(assembled.upper) if assembled else None,
                    "pnb": public_not_before,
                },
            )
            await connection.execute(
                text("DELETE FROM weather.keogram_bin WHERE segment_id=:id"),
                {"id": item.segment_id},
            )

    async def sync_video(self, item: Due, state: str, reason: str | None) -> None:
        """Put the final keogram status in the video's sidecar (a sidecar-only revision)."""
        job = item.job
        wanted = {"state": state, "reason": reason}
        current = await schedule.current_video(self.engine, job)
        document = None
        async with self.engine.connect() as connection:
            if "sidecar" in current.assets and not current.withdrawn:
                raw = (
                    await connection.execute(
                        text("SELECT sidecar FROM weather.media_asset WHERE id=:id"),
                        {"id": current.assets["sidecar"][0]},
                    )
                ).scalar()
                document = json.loads(bytes(raw)) if raw is not None else None
        if document is not None and document.get("keogram") != wanted:
            document["keogram"] = wanted
            try:
                await self.publisher.publish_sidecar(
                    kind="video",
                    camera_id=job.camera,
                    cycle_date=job.cycle_date,
                    segment=job.kind,
                    document=document,
                    deadline=datetime.now(UTC) + PUBLISH_DEADLINE,
                )
            except catalog.Withdrawn:
                pass
        async with self.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE weather.keogram SET video_synced=true,updated_at=clock_timestamp()
                WHERE segment_id=:id
            """),
                {"id": item.segment_id},
            )

