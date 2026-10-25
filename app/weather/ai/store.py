# SPDX-License-Identifier: MIT
"""Analysis catalog writes (#23). Every function uses the caller's transaction.

`weather.analysis` is one head row per video: the media revision it describes, its state and
the current immutable `weather.analysis_revision`. The state machine:

    published media -> pending -> complete | degraded            (a run validated output)
                       pending -> pending (next 02:00 local)     (Cortex down, auth or busy)
                       pending -> failed                         (media error, or 7 days)

A new web rendition resets the head to pending; the same bytes keep the pinned analysis (no
automatic reanalysis). `attempt_calls` meters Cortex calls since the head was last queued, so
scheduler retries of one describe job share one budget. Raw model responses live 24 h in
`weather.ai_raw_response`, which no public role can read.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.events.rules import Candidate
from weather.events.store import settle
from weather.search import documents

DESCRIBE_KIND = "analysis.describe"
SYNTHESIZE_KIND = "analysis.synthesize"
RETRY_WINDOW = timedelta(days=7)
JOB_DEADLINE = timedelta(days=1)
NIGHT = time(2, 0)
LOCAL = ZoneInfo("America/Denver")
REFRESH_DEBOUNCE = timedelta(minutes=10)
RAW_BODY_LIMIT = 256 * 1024
PURGE_BATCH = 1000


def next_night(now: datetime) -> datetime:
    """The next 02:00 America/Denver strictly after `now` (the nightly retry slot)."""
    local = now.astimezone(LOCAL)
    candidate = datetime.combine(local.date(), NIGHT, LOCAL)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), NIGHT, LOCAL)
    return candidate.astimezone(UTC)


def _local_date(moment: datetime) -> date:
    return moment.astimezone(LOCAL).date()


async def enqueue_describe(
    connection: AsyncConnection, video_id: UUID, publication_id: UUID, *, tag: str, now: datetime
) -> bool:
    """Queue one describe job per (video, publication, tag); False if that key already ran."""
    key = f"{DESCRIBE_KIND}:{video_id}:{publication_id}:{tag}"
    existing = (
        await connection.execute(
            text("SELECT 1 FROM weather.job WHERE idempotency_key=:key"), {"key": key}
        )
    ).first()
    if existing is not None:
        return False
    await jobs.enqueue(
        connection,
        kind=DESCRIBE_KIND,
        key=key,
        payload={"video_id": str(video_id), "publication_id": str(publication_id)},
        not_before=now,
        deadline=now + JOB_DEADLINE,
    )
    return True


PUBLISHED_SQL = """SELECT v.id,v.state,v.withdrawn,m.sha256 FROM weather.video v
JOIN weather.publication_asset pa ON pa.publication_id=v.publication_id AND pa.role='web'
JOIN weather.media_asset m ON m.id=pa.asset_id
WHERE v.publication_id=:publication"""
UPSERT_SQL = """INSERT INTO weather.analysis(video_id,publication_id,media_sha256,state,
  pending_since,next_attempt_at,updated_at)
VALUES(:video,:publication,:sha,'pending',:now,:retry,:now)
ON CONFLICT (video_id) DO UPDATE SET publication_id=EXCLUDED.publication_id,
  media_sha256=EXCLUDED.media_sha256,state='pending',reason=NULL,stale=false,
  pending_since=EXCLUDED.pending_since,next_attempt_at=EXCLUDED.next_attempt_at,
  evidence_checked_at=NULL,refreshes=0,attempt_calls=0,updated_at=EXCLUDED.updated_at
WHERE weather.analysis.media_sha256 <> EXCLUDED.media_sha256
RETURNING video_id"""


async def on_published(connection: AsyncConnection, publication_id: UUID, now: datetime) -> bool:
    """A committed video revision: queue its analysis unless the web bytes are unchanged."""
    row = (
        await connection.execute(text(PUBLISHED_SQL), {"publication": publication_id})
    ).one_or_none()
    if row is None or row.withdrawn or row.state not in ("published", "partial"):
        return False
    # The nightly slot is a safety net should the first job be lost (e.g. intel disabled).
    queued = (
        await connection.execute(
            text(UPSERT_SQL),
            {
                "video": row.id,
                "publication": publication_id,
                "sha": row.sha256,
                "now": now,
                "retry": next_night(now + timedelta(hours=1)),
            },
        )
    ).scalar_one_or_none()
    if queued is None:
        return False
    await enqueue_describe(connection, row.id, publication_id, tag="published", now=now)
    return True


async def withdraw_event(connection: AsyncConnection, key: str, now: datetime) -> None:
    """Withdraw one visual event through #41's single write path (a no-op when absent)."""

    async def gone(_connection: AsyncConnection) -> Candidate | None:
        return None

    await settle(connection, key, gone, family="visual", author_kind="model", now=now)


async def on_withdrawn(connection: AsyncConnection, video_id: UUID, now: datetime) -> None:
    """A withdrawn video takes its search documents and visual events with it.

    The head row is locked first, in its own statement, so a concurrent publication either
    commits before the keys are read (and its events are withdrawn here) or waits and then sees
    the withdrawn video under its own lock (`_publish` re-reads it FOR SHARE).
    """
    await connection.execute(
        text("SELECT 1 FROM weather.analysis WHERE video_id=:video FOR UPDATE"),
        {"video": video_id},
    )
    await documents.withdraw(connection, video_id)
    keys = (
        await connection.execute(
            text("""SELECT r.event_keys FROM weather.analysis a
            JOIN weather.analysis_revision r ON r.video_id=a.video_id AND r.revision=a.revision
            WHERE a.video_id=:video"""),
            {"video": video_id},
        )
    ).scalar_one_or_none() or []
    for key in sorted(keys):
        await withdraw_event(connection, key, now)


HEAD_SQL = """SELECT a.video_id,a.publication_id,a.media_sha256,a.state,a.reason,a.revision,
  a.stale,a.pending_since,v.camera_id,v.cycle_date,v.segment,v.starts_at,v.ends_at,v.frames,
  v.withdrawn,v.state AS video_state,v.revision AS video_revision,v.public_not_before
FROM weather.analysis a JOIN weather.video v ON v.id=a.video_id WHERE a.video_id=:video"""


async def head(
    connection: AsyncConnection, video_id: UUID, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = HEAD_SQL + (" FOR UPDATE OF a" if lock else "")
    row = (await connection.execute(text(sql), {"video": video_id})).mappings().first()
    return dict(row) if row is not None else None


ASSET_SQL = """SELECT pa.role,m.id,m.object_key,m.sha256,m.bytes FROM weather.publication_asset pa
JOIN weather.media_asset m ON m.id=pa.asset_id
WHERE pa.publication_id=:publication AND pa.role IN ('web','poster')
  AND NOT m.withdrawn AND m.state='available'"""
KEOGRAM_SQL = """SELECT m.object_key,m.sha256,m.bytes FROM weather.publication p
JOIN weather.publication_asset pa ON pa.publication_id=p.id AND pa.role='keogram'
JOIN weather.media_asset m ON m.id=pa.asset_id
WHERE p.product='keogram' AND p.camera_id=:camera AND p.product_key=:key
  AND p.state IN ('published','partial') AND NOT m.withdrawn AND m.state='available'
  AND NOT EXISTS (SELECT 1 FROM weather.product_tombstone t WHERE t.product='keogram'
    AND t.camera_id=:camera AND t.product_key=:key)
ORDER BY p.revision DESC LIMIT 1"""


async def media(connection: AsyncConnection, row: dict[str, Any]) -> dict[str, Any]:
    """The web rendition, poster ID and the optional current keogram of a head's publication."""
    assets = {
        r.role: r
        for r in await connection.execute(text(ASSET_SQL), {"publication": row["publication_id"]})
    }
    key = f"{row['cycle_date'].isoformat()}/{row['segment']}"
    keogram = (
        await connection.execute(text(KEOGRAM_SQL), {"camera": row["camera_id"], "key": key})
    ).one_or_none()
    return {
        "web": assets.get("web"),
        "poster_asset_id": assets["poster"].id if "poster" in assets else None,
        "keogram": keogram,
    }


async def defer(
    connection: AsyncConnection,
    video_id: UUID,
    publication_id: UUID,
    *,
    reason: str,
    calls: int,
    now: datetime,
) -> bool:
    """Cortex was unavailable: stay pending until the next nightly slot."""
    result = await connection.execute(
        text("""UPDATE weather.analysis SET reason=:reason,next_attempt_at=:retry,runs=runs+1,
          calls=calls+:calls,updated_at=:now
        WHERE video_id=:video AND publication_id=:publication AND state='pending'"""),
        {
            "video": video_id,
            "publication": publication_id,
            "reason": reason,
            "retry": next_night(now),
            "calls": calls,
            "now": now,
        },
    )
    return result.rowcount == 1


async def fail(
    connection: AsyncConnection,
    video_id: UUID,
    publication_id: UUID,
    *,
    reason: str,
    calls: int,
    now: datetime,
) -> bool:
    result = await connection.execute(
        text("""UPDATE weather.analysis SET state='failed',reason=:reason,pending_since=NULL,
          next_attempt_at=NULL,runs=runs+1,calls=calls+:calls,updated_at=:now
        WHERE video_id=:video AND publication_id=:publication AND state='pending'"""),
        {
            "video": video_id,
            "publication": publication_id,
            "reason": reason,
            "calls": calls,
            "now": now,
        },
    )
    return result.rowcount == 1


NIGHTLY_SQL = """SELECT a.video_id,a.publication_id,a.next_attempt_at FROM weather.analysis a
WHERE a.state='pending' AND a.next_attempt_at<=:now AND a.pending_since>:oldest
  AND NOT EXISTS (SELECT 1 FROM weather.job j WHERE j.kind=:kind
    AND j.status IN ('queued','running') AND j.payload->>'video_id'=a.video_id::text)
ORDER BY a.next_attempt_at,a.video_id LIMIT :limit FOR UPDATE OF a SKIP LOCKED"""


async def nightly(connection: AsyncConnection, now: datetime, *, limit: int = 100) -> int:
    """Queue one retry per pending head whose nightly slot has come (inside the 7 days).

    Heads with a live describe job are left alone. The slot moves to the following night
    rather than clearing, so a queued job that is lost (intel disabled, retries exhausted) is
    queued again instead of waiting out the 7 days. The call budget is refilled only when a
    job is actually queued.
    """
    rows = list(
        await connection.execute(
            text(NIGHTLY_SQL),
            {"now": now, "oldest": now - RETRY_WINDOW, "limit": limit, "kind": DESCRIBE_KIND},
        )
    )
    queued = 0
    for row in rows:
        # One key per slot: nightly slots are one per night, and a forced retry is its own.
        tag = row.next_attempt_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        fresh = await enqueue_describe(
            connection, row.video_id, row.publication_id, tag=tag, now=now
        )
        queued += fresh
        await connection.execute(
            text("""UPDATE weather.analysis SET next_attempt_at=:retry,
              attempt_calls=CASE WHEN :fresh THEN 0 ELSE attempt_calls END
            WHERE video_id=:video"""),
            {"video": row.video_id, "retry": next_night(now), "fresh": fresh},
        )
    return queued


SPEND_SQL = """UPDATE weather.analysis SET attempt_calls=attempt_calls+1
WHERE video_id=:video AND state='pending' AND attempt_calls<:cap"""


async def spend_call(connection: AsyncConnection, video_id: UUID, cap: int) -> bool:
    """Reserve one Cortex call against the head's budget; False once `cap` is spent."""
    result = await connection.execute(text(SPEND_SQL), {"video": video_id, "cap": cap})
    return result.rowcount == 1


async def expire(connection: AsyncConnection, now: datetime) -> int:
    """Pending for 7 days: failed, keeping the last Cortex reason."""
    result = await connection.execute(
        text("""UPDATE weather.analysis SET state='failed',reason=COALESCE(reason,'expired'),
          pending_since=NULL,next_attempt_at=NULL,updated_at=:now
        WHERE state='pending' AND pending_since<=:oldest"""),
        {"now": now, "oldest": now - RETRY_WINDOW},
    )
    return result.rowcount


async def request_refresh(
    connection: AsyncConnection,
    video_id: UUID,
    publication_id: UUID,
    revision: int,
    now: datetime,
) -> bool:
    """Evidence changed under `revision`: mark it stale and queue one re-render.

    The key is per (video, base revision, local day), so a burst of evidence changes queues
    one refresh, which reads the evidence when it runs.
    """
    marked = await connection.execute(
        text("""UPDATE weather.analysis SET stale=true,evidence_checked_at=:now,updated_at=:now
        WHERE video_id=:video AND publication_id=:publication AND revision=:revision
          AND state IN ('complete','degraded')"""),
        {"video": video_id, "publication": publication_id, "revision": revision, "now": now},
    )
    if marked.rowcount != 1:
        return False
    await jobs.enqueue(
        connection,
        kind=SYNTHESIZE_KIND,
        key=f"{SYNTHESIZE_KIND}:{video_id}:{revision}:{_local_date(now).isoformat()}",
        payload={
            "video_id": str(video_id),
            "publication_id": str(publication_id),
            "base_revision": revision,
        },
        # Only the key, payload and deadline bind the job, so later requests are no-ops.
        not_before=now + REFRESH_DEBOUNCE,
        deadline=datetime.combine(_local_date(now) + timedelta(days=2), time(), LOCAL).astimezone(
            UTC
        ),
    )
    return True


async def store_raw(
    connection: AsyncConnection,
    *,
    video_id: UUID,
    stage: str,
    window_start: datetime | None,
    call: int,
    outcome: str,
    finish_reason: str | None,
    body: bytes | None,
) -> None:
    """Private 24 h diagnostics; the API role has no grant on this table."""
    content = None
    if body is not None:
        content = body[:RAW_BODY_LIMIT].decode("utf-8", "replace").replace("\x00", "")
        while len(content.encode()) > RAW_BODY_LIMIT:
            content = content[: len(content) - 1024]
    await connection.execute(
        text("""INSERT INTO weather.ai_raw_response(video_id,stage,window_start,call,outcome,
          finish_reason,body) VALUES(:video,:stage,:window,:call,:outcome,:finish,:body)"""),
        {
            "video": video_id,
            "stage": stage,
            "window": window_start,
            "call": call,
            "outcome": outcome[:32],
            "finish": finish_reason[:32] if finish_reason else None,
            "body": content,
        },
    )


async def purge_raw(connection: AsyncConnection) -> int:
    result = await connection.execute(
        text("""DELETE FROM weather.ai_raw_response WHERE id IN (
          SELECT id FROM weather.ai_raw_response WHERE expires_at<=clock_timestamp()
          LIMIT :batch)"""),
        {"batch": PURGE_BATCH},
    )
    return result.rowcount


async def queue_depth(connection: AsyncConnection) -> tuple[int, float]:
    """Queued analysis jobs and the age of the oldest pending head, for /metrics."""
    depth = (
        await connection.execute(
            text("""SELECT count(*) FROM weather.job WHERE kind = ANY(:kinds)
            AND status IN ('queued','running')"""),
            {"kinds": [DESCRIBE_KIND, SYNTHESIZE_KIND]},
        )
    ).scalar_one()
    oldest = (
        await connection.execute(
            text("""SELECT extract(epoch FROM clock_timestamp()-min(pending_since))
            FROM weather.analysis WHERE state='pending'""")
        )
    ).scalar_one()
    return int(depth), float(oldest or 0)
