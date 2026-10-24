# SPDX-License-Identifier: MIT
"""Transactional, idempotent jobs with an expiring lease fenced by the attempt number.

Every helper uses the caller's transaction. Handlers must use fenced_transaction
for catalog writes, so losing a lease also rolls back those writes.
"""

import json
from collections.abc import AsyncIterator, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

RETENTION = timedelta(days=30)
PRUNE_BATCH = 1000


class LeaseLost(RuntimeError):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    id: UUID
    kind: str
    payload: dict[str, Any]
    deadline: datetime
    # Each claim increments the attempt, so it is also the fencing token.
    attempt: int


def _json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError("Job payload too large")
    return encoded


async def _insert_once(
    connection: AsyncConnection,
    *,
    table: str,
    label: str,
    name: str,
    key: str,
    payload: Mapping[str, Any],
    not_before: datetime,
    deadline: datetime,
) -> UUID:
    if not 1 <= len(name) <= 128 or not 1 <= len(key) <= 256:
        raise ValueError(f"Invalid {table} identity")
    if not_before.tzinfo is None or deadline.tzinfo is None:
        raise ValueError(f"{table} times must be aware")
    inserted = (
        await connection.execute(
            text(f"""
        INSERT INTO weather.{table}({label},idempotency_key,payload,not_before,deadline)
        VALUES(:name,:key,CAST(:payload AS jsonb),:not_before,:deadline)
        ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
    """),
            {
                "name": name,
                "key": key,
                "payload": _json(payload),
                "not_before": not_before,
                "deadline": deadline,
            },
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return cast(UUID, inserted)
    # A unique-key race waits for the first transaction, then reads its committed row.
    existing = (
        (
            await connection.execute(
                text(f"""
        SELECT id,{label} AS name,payload,deadline FROM weather.{table} WHERE idempotency_key=:key
    """),
                {"key": key},
            )
        )
        .mappings()
        .one()
    )
    if (
        existing["name"] != name
        or existing["payload"] != dict(payload)
        or existing["deadline"] != deadline
    ):
        raise IdempotencyConflict("Idempotency key is bound to different work")
    return cast(UUID, existing["id"])


async def enqueue(
    connection: AsyncConnection,
    *,
    kind: str,
    key: str,
    payload: Mapping[str, Any],
    not_before: datetime,
    deadline: datetime,
) -> UUID:
    return await _insert_once(
        connection,
        table="job",
        label="kind",
        name=kind,
        key=key,
        payload=payload,
        not_before=not_before,
        deadline=deadline,
    )


async def append_outbox(
    connection: AsyncConnection,
    *,
    topic: str,
    key: str,
    payload: Mapping[str, Any],
    not_before: datetime,
    deadline: datetime,
) -> UUID:
    return await _insert_once(
        connection,
        table="outbox",
        label="topic",
        name=topic,
        key=key,
        payload=payload,
        not_before=not_before,
        deadline=deadline,
    )


async def expire(connection: AsyncConnection) -> int:
    result = await connection.execute(
        text("""
        UPDATE weather.job SET status='expired',lease_until=NULL,last_error='deadline'
        WHERE status IN ('queued','running') AND deadline<=clock_timestamp()
    """)
    )
    return result.rowcount


async def claim(
    connection: AsyncConnection, *, kinds: Collection[str], lease_seconds: int = 60
) -> Job | None:
    """Claim the oldest due job of a registered kind; other kinds stay queued."""
    if not 1 <= lease_seconds <= 300:
        raise ValueError("Invalid lease duration")
    if not kinds:
        return None
    row = (
        (
            await connection.execute(
                text("""
        WITH candidate AS (
          SELECT id FROM weather.job WHERE kind = ANY(:kinds) AND deadline>clock_timestamp() AND
            ((status='queued' AND not_before<=clock_timestamp()) OR
             (status='running' AND lease_until<=clock_timestamp()))
          ORDER BY not_before,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE weather.job j SET status='running',attempt=attempt+1,
          lease_until=LEAST(clock_timestamp()+make_interval(secs=>:seconds),deadline)
        FROM candidate WHERE j.id=candidate.id
        RETURNING j.id,j.kind,j.payload,j.deadline,j.attempt
    """),
                {"kinds": sorted(kinds), "seconds": lease_seconds},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return Job(
        id=cast(UUID, row["id"]),
        kind=str(row["kind"]),
        payload=cast(dict[str, Any], row["payload"]),
        deadline=cast(datetime, row["deadline"]),
        attempt=int(row["attempt"]),
    )


def _identity(job: Job) -> dict[str, Any]:
    return {"id": job.id, "attempt": job.attempt}


# lease_until never exceeds the deadline, so an unexpired lease implies an unexpired job.
_OWNED = "id=:id AND status='running' AND attempt=:attempt AND lease_until>clock_timestamp()"


async def heartbeat(connection: AsyncConnection, job: Job, *, lease_seconds: int = 60) -> bool:
    if not 1 <= lease_seconds <= 300:
        raise ValueError("Invalid lease duration")
    result = await connection.execute(
        text(f"""
        UPDATE weather.job SET lease_until=LEAST(clock_timestamp()+make_interval(secs=>:seconds),
                                                deadline) WHERE {_OWNED}
    """),
        {**_identity(job), "seconds": lease_seconds},
    )
    return result.rowcount == 1


async def complete(
    connection: AsyncConnection, job: Job, result: Mapping[str, Any] | None = None
) -> bool:
    updated = await connection.execute(
        text(f"""
        UPDATE weather.job SET status='succeeded',result=CAST(:result AS jsonb),
          lease_until=NULL,last_error=NULL WHERE {_OWNED}
    """),
        {**_identity(job), "result": _json({} if result is None else result)},
    )
    return updated.rowcount == 1


async def fail(
    connection: AsyncConnection,
    job: Job,
    *,
    reason: str,
    retry: bool,
    delay_seconds: int = 30,
) -> bool:
    if not 0 <= delay_seconds <= 3600 or not 1 <= len(reason) <= 128:
        raise ValueError("Invalid retry policy")
    updated = await connection.execute(
        text(f"""
        UPDATE weather.job SET status=CASE WHEN :retry THEN 'queued' ELSE 'failed' END,
          not_before=CASE WHEN :retry THEN clock_timestamp()+make_interval(secs=>:delay)
                          ELSE not_before END,
          lease_until=NULL,last_error=:reason
        WHERE {_OWNED}
    """),
        {**_identity(job), "retry": retry, "delay": delay_seconds, "reason": reason},
    )
    return updated.rowcount == 1


async def prune(connection: AsyncConnection, *, retention: timedelta = RETENTION) -> int:
    """Delete bounded batches of routed outbox rows and finished jobs past retention.

    Quarantined outbox rows stay for the operator. A job is old enough once its immutable
    deadline is past the retention window, because no attempt can outlive the deadline.
    """
    values = {"retention": retention, "batch": PRUNE_BATCH}
    outbox = await connection.execute(
        text("""
        DELETE FROM weather.outbox WHERE id IN (
          SELECT id FROM weather.outbox WHERE error IS NULL
            AND dispatched_at < clock_timestamp() - CAST(:retention AS interval) LIMIT :batch)
    """),
        values,
    )
    job = await connection.execute(
        text("""
        DELETE FROM weather.job WHERE id IN (
          SELECT j.id FROM weather.job j
          WHERE j.status NOT IN ('queued','running')
            AND j.deadline < clock_timestamp() - CAST(:retention AS interval)
            AND NOT EXISTS (SELECT 1 FROM weather.outbox o WHERE o.job_id=j.id)
          LIMIT :batch)
    """),
        values,
    )
    return outbox.rowcount + job.rowcount


@asynccontextmanager
async def fenced_transaction(engine: AsyncEngine, job: Job) -> AsyncIterator[AsyncConnection]:
    """Lock the owned row around catalog writes; completion re-checks the lease at commit."""
    async with engine.begin() as connection:
        owned = (
            await connection.execute(
                text(f"SELECT id FROM weather.job WHERE {_OWNED} FOR UPDATE"), _identity(job)
            )
        ).scalar_one_or_none()
        if owned is None:
            raise LeaseLost("Job no longer owns its lease")
        yield connection
