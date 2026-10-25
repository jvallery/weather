# SPDX-License-Identifier: MIT
"""Search document builder: canonical public text, revision CAS, withdrawal and embedding jobs.

Producers (publication #18, descriptions #23, the editor #44) call `replace` or `withdraw`
inside the transaction that changes the public description, so stale text stops matching when
that transaction commits, before any reindex. Embedding happens later in a job that holds no
database connection while it waits for Cortex, and stores a vector only if its document is
still current with the same text hash (compare-and-set). Withdrawal is final in the database.

Only the fields of `VideoText` and `WindowText` reach a document. They carry the effective
public description; favorites, notes, operator identity and originals metadata have no slot.
"""

import re
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.scheduler.registry import Handler, JobContext, Registry

from .cortex import Embedder, EmbeddingUnavailable, vector_text

EMBED_KIND = "search.embed"
MAX_FIELD = 2000
# A description (automated or human, #44) may be 8,000 characters and is indexed whole.
MAX_DESCRIPTION = 8000
MAX_TEXT = 9000
EMBED_TIMEOUT_SECONDS = 30
TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
Segment = Literal["daytime", "overnight"]


class StaleRevision(ValueError):
    """The producer's revisions are older than the current documents; nothing changed."""


@dataclass(frozen=True)
class WindowText:
    """An hour window of the video's public description (full-text only)."""

    source_start: datetime
    source_end: datetime
    observed_conditions: str
    evidence_quality: str
    media_start_seconds: float | None = None
    media_end_seconds: float | None = None


@dataclass(frozen=True)
class VideoText:
    """The effective public description of one published video and its revisions."""

    video_id: UUID
    camera_id: str
    camera_label: str
    segment: Segment
    video_start: datetime
    video_end: datetime
    public_not_before: datetime
    media_revision: int
    description_revision: int
    editorial_revision: int
    analysis_mode: str
    observed_conditions: str
    evidence_quality: str
    media_seconds: float | None = None
    poster_asset_id: UUID | None = None
    windows: tuple[WindowText, ...] = ()

    @property
    def revisions(self) -> tuple[int, int, int]:
        return (self.media_revision, self.description_revision, self.editorial_revision)


def _field(value: str, limit: int = MAX_FIELD) -> str:
    """One line per field; over-long text is rejected, never silently truncated."""
    line = " ".join(value.split())
    if not line or len(line) > limit:
        raise ValueError("Search document field is empty or too long")
    return line


def _utc(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Search document times must be aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_text(
    video: VideoText, start: datetime, end: datetime, observed: str, quality: str
) -> str:
    """Camera / Interval / Segment / Analysis mode / Observed conditions / Evidence quality."""
    body = "\n".join(
        (
            f"Camera: {_field(video.camera_label)}",
            f"Interval: {_utc(start)} to {_utc(end)}",
            f"Segment: {video.segment}",
            f"Analysis mode: {video.analysis_mode}",
            f"Observed conditions: {_field(observed, MAX_DESCRIPTION)}",
            f"Evidence quality: {_field(quality)}",
        )
    )
    if len(body) > MAX_TEXT:
        raise ValueError("Search document is too long")
    return body


def text_hash(body: str) -> str:
    return sha256(body.encode()).hexdigest()


def documents(video: VideoText) -> list[dict[str, Any]]:
    """Rows for one video: the embedded video document, then its hour windows."""
    if not TOKEN.match(video.camera_id) or not TOKEN.match(video.analysis_mode):
        raise ValueError("Invalid camera or analysis mode")
    if video.segment not in ("daytime", "overnight") or not video.video_start < video.video_end:
        raise ValueError("Invalid segment or interval")
    if min(video.revisions) < 0 or video.media_revision < 1:
        raise ValueError("Invalid revisions")
    base = {
        "video_id": video.video_id,
        "camera_id": video.camera_id,
        "segment": video.segment,
        "video_start": video.video_start,
        "video_end": video.video_end,
        "media_revision": video.media_revision,
        "description_revision": video.description_revision,
        "editorial_revision": video.editorial_revision,
        "analysis_mode": video.analysis_mode,
        "poster_asset_id": video.poster_asset_id,
        "public_not_before": video.public_not_before,
    }
    body = canonical_text(
        video, video.video_start, video.video_end, video.observed_conditions,
        video.evidence_quality,
    )
    rows = [
        {
            **base,
            "kind": "video",
            "source_start": video.video_start,
            "source_end": video.video_end,
            "media_start_seconds": 0.0 if video.media_seconds else None,
            "media_end_seconds": video.media_seconds or None,
            "text": body,
            "text_hash": text_hash(body),
            "index_status": "pending",
        }
    ]
    for window in video.windows:
        if not video.video_start <= window.source_start < window.source_end <= video.video_end:
            raise ValueError("A window lies inside its video")
        body = canonical_text(
            video, window.source_start, window.source_end, window.observed_conditions,
            window.evidence_quality,
        )
        rows.append(
            {
                **base,
                "kind": "window",
                "source_start": window.source_start,
                "source_end": window.source_end,
                "media_start_seconds": window.media_start_seconds,
                "media_end_seconds": window.media_end_seconds,
                "text": body,
                "text_hash": text_hash(body),
                "index_status": "lexical",
            }
        )
    return rows


# Every revision of the video, withdrawn ones included: history decides staleness.
HISTORY_SQL = """SELECT id,kind,source_start,text_hash,visibility,media_revision,
  description_revision,editorial_revision FROM weather.search_document
WHERE video_id=:video ORDER BY id FOR UPDATE"""
WITHDRAW_SQL = """UPDATE weather.search_document SET visibility='withdrawn',withdrawn_at=now()
WHERE video_id=:video AND visibility='public'"""
INSERT_SQL = """INSERT INTO weather.search_document(video_id,kind,camera_id,segment,video_start,
  video_end,source_start,source_end,media_start_seconds,media_end_seconds,media_revision,
  description_revision,editorial_revision,analysis_mode,poster_asset_id,public_not_before,text,
  text_hash,index_status)
VALUES(:video_id,:kind,:camera_id,:segment,:video_start,:video_end,:source_start,:source_end,
  :media_start_seconds,:media_end_seconds,:media_revision,:description_revision,
  :editorial_revision,:analysis_mode,:poster_asset_id,:public_not_before,:text,:text_hash,
  :index_status) RETURNING id"""


async def enqueue_embedding(
    connection: AsyncConnection, document_id: UUID, digest: str, profile_id: str, now: datetime
) -> None:
    """Idempotent per (document, text hash, profile) and UTC day; the deadline is fixed."""
    day = now.astimezone(UTC).date()
    await jobs.enqueue(
        connection,
        kind=EMBED_KIND,
        key=f"{EMBED_KIND}:{document_id}:{profile_id}:{day.isoformat()}",
        payload={"document_id": str(document_id), "text_hash": digest, "profile_id": profile_id},
        not_before=now,
        deadline=datetime.combine(day + timedelta(days=2), time(), UTC),
    )


async def replace(
    connection: AsyncConnection, video: VideoText, *, profile_id: str, now: datetime
) -> list[UUID]:
    """Make `video`'s text current in the caller's transaction; returns the new document IDs.

    Idempotent for identical revisions and text. Revisions not newer than the video's history
    (withdrawn documents included) raise StaleRevision and change nothing, so a late producer
    cannot revive withdrawn text. Newer ones withdraw every current document before inserting.
    """
    rows = documents(video)
    history = list(
        (await connection.execute(text(HISTORY_SQL), {"video": video.video_id})).mappings()
    )
    if history:
        latest = tuple(
            max(r[name] for r in history)
            for name in ("media_revision", "description_revision", "editorial_revision")
        )
        if any(new < old for new, old in zip(video.revisions, latest, strict=True)):
            raise StaleRevision("Search document revisions are older than the current ones")
        current = [r for r in history if r["visibility"] == "public"]
        if video.revisions == latest:
            # A replay is a no-op; it never revives withdrawn text or changes text in place.
            same = sorted((r["kind"], r["source_start"], r["text_hash"]) for r in current)
            if not current or same != sorted(
                (r["kind"], r["source_start"], r["text_hash"]) for r in rows
            ):
                raise StaleRevision("These revisions are already withdrawn or differ in text")
            return [r["id"] for r in current]
        await connection.execute(text(WITHDRAW_SQL), {"video": video.video_id})
    ids = []
    for row in rows:
        document = (await connection.execute(text(INSERT_SQL), row)).scalar_one()
        ids.append(document)
        if row["kind"] == "video":
            await enqueue_embedding(connection, document, row["text_hash"], profile_id, now)
    return ids


async def withdraw(connection: AsyncConnection, video_id: UUID) -> int:
    """Withdraw every current document of a video (publication withdrawal, editor hook)."""
    result = await connection.execute(text(WITHDRAW_SQL), {"video": video_id})
    return int(getattr(result, "rowcount", 0) or 0)


MISSING_SQL = """SELECT d.id,d.text_hash FROM weather.search_document d
WHERE d.kind='video' AND d.visibility='public' AND d.id > :after
  AND NOT EXISTS (SELECT 1 FROM weather.search_embedding e
                  WHERE e.document_id=d.id AND e.profile_id=:profile)
ORDER BY d.id LIMIT :limit"""


async def enqueue_missing(
    connection: AsyncConnection, profile_id: str, now: datetime, *, limit: int = 500
) -> int:
    """Queue current video documents without a vector for `profile_id` (profile change)."""
    after, queued = UUID(int=0), 0
    while queued < limit:
        page = (
            await connection.execute(
                text(MISSING_SQL),
                {"after": after, "profile": profile_id, "limit": min(100, limit - queued)},
            )
        ).all()
        if not page:
            break
        for document, digest in page:
            await enqueue_embedding(connection, document, digest, profile_id, now)
        queued += len(page)
        after = page[-1][0]
    return queued


READ_SQL = """SELECT text,text_hash,visibility FROM weather.search_document
WHERE id=:id AND kind='video'"""
# Compare-and-set: the vector is stored only while the document is current with the same text.
STORE_SQL = """INSERT INTO weather.search_embedding(document_id,profile_id,text_hash,embedding)
SELECT d.id,:profile,d.text_hash,CAST(CAST(:embedding AS text) AS vector)
FROM weather.search_document d
WHERE d.id=:id AND d.text_hash=:hash AND d.visibility='public'
ON CONFLICT (document_id,profile_id) DO NOTHING"""
LOCK_SQL = """SELECT visibility FROM weather.search_document
WHERE id=:id AND text_hash=:hash FOR UPDATE"""
INDEXED_SQL = """UPDATE weather.search_document SET index_status='indexed'
WHERE id=:id AND visibility='public' AND index_status='pending'"""


class Slot(Protocol):
    """The cross-process inference slot (#23's advisory lock); held only around the call."""

    def hold(self, wait: float) -> AbstractAsyncContextManager[Any]: ...


def embed_handler(embedder: Embedder, slot: Slot | None = None) -> Handler:
    async def embed(context: JobContext) -> None:
        payload = context.job.payload
        document, digest = UUID(str(payload["document_id"])), str(payload["text_hash"])
        if str(payload["profile_id"]) != embedder.profile.id:
            raise ValueError("Job profile differs from the configured profile")
        # Short read; no connection is held while Cortex works.
        async with context.engine.connect() as connection:
            row = (
                (await connection.execute(text(READ_SQL), {"id": document})).mappings().first()
            )
        if row is None or row["text_hash"] != digest or row["visibility"] != "public":
            return
        try:
            async with slot.hold(EMBED_TIMEOUT_SECONDS) if slot else nullcontext():
                (vector,) = await embedder.embed([row["text"]], timeout=EMBED_TIMEOUT_SECONDS)
        except EmbeddingUnavailable as error:
            raise RuntimeError(f"Embedding failed ({error.reason})") from None

        async def write(connection: AsyncConnection) -> None:
            # Serializes with a concurrent withdrawal: whichever commits first wins.
            locked = (
                await connection.execute(text(LOCK_SQL), {"id": document, "hash": digest})
            ).scalar_one_or_none()
            if locked != "public":
                return
            await connection.execute(
                text(STORE_SQL),
                {
                    "id": document,
                    "hash": digest,
                    "profile": embedder.profile.id,
                    "embedding": vector_text(vector),
                },
            )
            await connection.execute(text(INDEXED_SQL), {"id": document})

        await context.commit(write)

    return embed


def register(registry: Registry, embedder: Embedder, *, slot: Slot | None = None) -> None:
    """Wired by the worker that holds Cortex egress (#23); the scheduler leaves jobs queued."""
    registry.register(EMBED_KIND, embed_handler(embedder, slot))

