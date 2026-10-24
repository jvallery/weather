# SPDX-License-Identifier: MIT
"""Transactional outbox -> idempotent job dispatch."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db.jobs import enqueue
from weather.scheduler.registry import Registry


async def dispatch(
    connection: AsyncConnection, registry: Registry, *, limit: int = 100
) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("Invalid outbox batch size")
    rows = (
        (
            await connection.execute(
                text("""
        SELECT id,topic,payload,not_before,deadline,expected_revision FROM weather.outbox
        WHERE dispatched_at IS NULL ORDER BY created_at,id
        FOR UPDATE SKIP LOCKED LIMIT :limit
    """),
                {"limit": limit},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        kind = registry.topic_kind(str(row["topic"]))
        job_id = await enqueue(
            connection,
            kind=kind,
            key="outbox:" + str(row["id"]),
            payload=cast(dict[str, Any], row["payload"]),
            not_before=cast(datetime, row["not_before"]),
            deadline=cast(datetime, row["deadline"]),
            expected_revision=cast(int | None, row["expected_revision"]),
        )
        await connection.execute(
            text("""
            UPDATE weather.outbox SET dispatched_at=clock_timestamp(),job_id=:job_id WHERE id=:id
        """),
            {"job_id": job_id, "id": cast(UUID, row["id"])},
        )
    return len(rows)
