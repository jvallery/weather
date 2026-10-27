# SPDX-License-Identifier: MIT
"""Human poster choices (#78): publish the operator's chosen frame as the video's poster.

The editor records only a frame index (`editorial_revision.poster_frame`); this camera's pipeline,
the only identity with the media writer key, makes the poster. It streams the video's published
web rendition into the pipeline's scratch space, decodes exactly that frame and writes the same
six poster roles finalize writes (`finalize.write_posters`: no EXIF or GPS). It publishes them as
revision N+1 of the video with every other role carried unchanged, under the video's own embargo
and content-addressed like every asset (#18).

A choice is live while `editorial_poster_applied` holds a row for its frame with the SHA-256 of
the video's current `poster` asset. A pipeline re-render or any other new video revision replaces
that poster, so the sweep sees the mismatch and publishes the choice again: no rerun ever
overwrites a human poster. One video per pass, so a backlog never delays rendering.
"""

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.db import jobs
from weather.logs import event
from weather.publish.model import Committed, ProductFile
from weather.publish.objects import READ_CHUNK, ObjectStore
from weather.publish.publisher import Publisher

from . import metrics
from .ffmpeg import extract_frame
from .finalize import write_posters

LOG = logging.getLogger("weather.pipeline")
DEADLINE = timedelta(minutes=20)
# The editor's outbox topic: its handler re-projects the video's search documents, which carry
# the poster asset ID.
EDITORIAL_TOPIC = "editorial.changed"

LIVE_POSTER = """(SELECT a.sha256 FROM weather.publication_asset pa
  JOIN weather.media_asset a ON a.id=pa.asset_id
  WHERE pa.publication_id=v.publication_id AND pa.role='poster')"""
PENDING_SQL = f"""SELECT v.id,v.cycle_date,v.segment,v.frames,s.revision AS editorial_revision,
  r.poster_frame,w.object_key,w.bytes,w.width,w.height
FROM weather.editorial_subject s
JOIN weather.editorial_revision r ON r.subject_id=s.subject_id AND r.revision=s.revision
JOIN weather.video v ON v.id=s.video_id
JOIN weather.publication_asset wa ON wa.publication_id=v.publication_id AND wa.role='web'
JOIN weather.media_asset w ON w.id=wa.asset_id
WHERE v.camera_id=:camera AND r.poster_frame IS NOT NULL AND r.poster_frame < v.frames
  AND NOT v.withdrawn AND v.state IN ('published','partial')
  AND NOT EXISTS (SELECT 1 FROM weather.editorial_poster_applied p
    WHERE p.video_id=v.id AND p.poster_frame=r.poster_frame
      AND p.poster_sha256 = {LIVE_POSTER})
ORDER BY v.ends_at DESC
LIMIT 1"""
APPLIED_SQL = """INSERT INTO weather.editorial_poster_applied(video_id,editorial_revision,
  poster_frame,video_revision,poster_sha256)
VALUES(:video,:editorial,:frame,:revision,:sha)
ON CONFLICT (video_id,editorial_revision,video_revision) DO NOTHING"""


@dataclass(frozen=True)
class Pending:
    video_id: UUID
    cycle_date: date
    segment: str
    frames: int
    editorial_revision: int
    frame: int
    web_key: str
    web_bytes: int
    width: int
    height: int


async def pending(engine: AsyncEngine, camera: str) -> Pending | None:
    async with engine.connect() as connection:
        row = (
            (await connection.execute(text(PENDING_SQL), {"camera": camera}))
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return Pending(
        video_id=row["id"],
        cycle_date=row["cycle_date"],
        segment=row["segment"],
        frames=row["frames"],
        editorial_revision=row["editorial_revision"],
        frame=row["poster_frame"],
        web_key=row["object_key"],
        web_bytes=row["bytes"],
        width=row["width"],
        height=row["height"],
    )


async def download(store: ObjectStore, key: str, size: int, path: Path) -> None:
    """Stream the whole object to `path`; the span checks length and range first."""
    span = await store.open_span(key, 0, size - 1, size)
    try:
        with path.open("wb") as handle:
            async for chunk in span.chunks(READ_CHUNK):
                await asyncio.to_thread(handle.write, chunk)
    finally:
        await span.aclose()


class Posters:
    """One camera's human-poster sweep, run once per pipeline loop after the videos."""

    def __init__(
        self, engine: AsyncEngine, publisher: Publisher, store: ObjectStore, camera: str
    ) -> None:
        self.engine, self.publisher, self.store, self.camera = engine, publisher, store, camera

    async def sweep(self, now: datetime) -> bool:
        """Publish at most one pending choice; True when one was published."""
        choice = await pending(self.engine, self.camera)
        if choice is None:
            return False
        with tempfile.TemporaryDirectory(prefix="poster-") as scratch:
            work = Path(scratch)
            rendition = work / "web.mp4"
            await download(self.store, choice.web_key, choice.web_bytes, rendition)
            raw = await asyncio.to_thread(
                extract_frame, rendition, choice.frame, choice.width, choice.height
            )
            rendition.unlink()
            rgb = np.frombuffer(raw, dtype=np.uint8).reshape(choice.height, choice.width, 3)
            posters = await asyncio.to_thread(
                write_posters, Image.fromarray(rgb, "RGB"), work, choice.frame
            )
            files = tuple(
                ProductFile(
                    role=poster.role,
                    extension=poster.extension,
                    path=work / f"{poster.role}.{poster.extension}",
                    width=poster.width,
                    height=poster.height,
                )
                for poster in posters
            )
            jpeg = next(p.sha256 for p in posters if p.role == "poster" and p.extension == "jpg")

            async def record(connection: AsyncConnection, committed: Committed) -> None:
                await connection.execute(
                    text(APPLIED_SQL),
                    {
                        "video": choice.video_id,
                        "editorial": choice.editorial_revision,
                        "frame": choice.frame,
                        "revision": committed.revision,
                        "sha": jpeg,
                    },
                )
                await jobs.append_outbox(
                    connection,
                    topic=EDITORIAL_TOPIC,
                    key=f"poster:{choice.video_id}:{committed.revision}",
                    payload=_payload(choice),
                    not_before=now,
                    deadline=now + timedelta(days=1),
                )

            await self.publisher.publish_poster(
                camera_id=self.camera,
                cycle_date=choice.cycle_date,
                segment=choice.segment,
                files=files,
                deadline=now + DEADLINE,
                then=record,
            )
        metrics.HUMAN_POSTERS.labels(self.camera).inc()
        event(
            LOG,
            "poster_published",
            camera=self.camera,
            video_id=str(choice.video_id),
            frame=choice.frame,
            editorial_revision=choice.editorial_revision,
        )
        return True


def _payload(choice: Pending) -> dict[str, Any]:
    return {
        "subject_id": str(choice.video_id),
        "kind": "video",
        "revision": choice.editorial_revision,
        "public": True,
    }
