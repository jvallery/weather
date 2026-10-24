# SPDX-License-Identifier: MIT
"""Transactional outbox -> idempotent job dispatch."""

import logging
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db.jobs import enqueue
from weather.scheduler.registry import Registry, UnregisteredJob

LOG = logging.getLogger("weather.scheduler")
QUARANTINED = Counter(
    "weather_outbox_quarantined_total", "Outbox rows quarantined for an unregistered topic"
)
UNROUTED = "unregistered_topic"
# During a rollout an older scheduler may see a new topic first; only rows still unrouted
# after this long are quarantined, so a newer scheduler can claim young ones.
QUARANTINE_AFTER = timedelta(minutes=10)


async def dispatch(connection: AsyncConnection, registry: Registry, *, limit: int = 100) -> int:
    """Route pending rows; an unknown topic quarantines only its own row once it is old."""
    if not 1 <= limit <= 100:
        raise ValueError("Invalid outbox batch size")
    rows = (
        (
            await connection.execute(
                text("""
        SELECT id,topic,payload,not_before,deadline FROM weather.outbox
        WHERE dispatched_at IS NULL
          AND (topic = ANY(CAST(:topics AS text[]))
               OR created_at < clock_timestamp() - CAST(:grace AS interval))
        ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT :limit
    """),
                {"topics": sorted(registry.topics()), "grace": QUARANTINE_AFTER, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        row_id = cast(UUID, row["id"])
        try:
            kind = registry.topic_kind(str(row["topic"]))
        except UnregisteredJob:
            QUARANTINED.inc()
            LOG.error("Outbox row quarantined: unregistered topic")
            await connection.execute(
                text("""
                UPDATE weather.outbox SET dispatched_at=clock_timestamp(),error=:error
                WHERE id=:id
            """),
                {"error": UNROUTED, "id": row_id},
            )
            continue
        job_id = await enqueue(
            connection,
            kind=kind,
            key="outbox:" + str(row_id),
            payload=cast(dict[str, Any], row["payload"]),
            not_before=cast(datetime, row["not_before"]),
            deadline=cast(datetime, row["deadline"]),
        )
        await connection.execute(
            text("""
            UPDATE weather.outbox SET dispatched_at=clock_timestamp(),job_id=:job_id WHERE id=:id
        """),
            {"job_id": job_id, "id": row_id},
        )
    return len(rows)
