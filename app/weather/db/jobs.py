# SPDX-License-Identifier: MIT
"""Transactional, idempotent jobs with expiring owner/token fencing.

Every helper uses the caller's transaction. Handlers must use fenced_transaction
for catalog writes, so losing a lease also rolls back those writes.
"""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class LeaseLost(RuntimeError):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    id: UUID
    kind: str
    payload: dict[str, Any]
    expected_revision: int | None
    deadline: datetime
    attempt: int
    lease_owner: str
    fencing_token: int


def _json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), allow_nan=False, separators=(",", ":"))
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError("Job payload too large")
    return encoded


async def enqueue(
    connection: AsyncConnection,
    *,
    kind: str,
    key: str,
    payload: Mapping[str, Any],
    not_before: datetime,
    deadline: datetime,
    expected_revision: int | None = None,
) -> UUID:
    if not 1 <= len(kind) <= 128 or not 1 <= len(key) <= 256:
        raise ValueError("Invalid job identity")
    if not_before.tzinfo is None or deadline.tzinfo is None:
        raise ValueError("Job times must be aware")
    values = {
        "kind": kind,
        "key": key,
        "payload": _json(payload),
        "not_before": not_before,
        "deadline": deadline,
        "revision": expected_revision,
    }
    result = await connection.execute(
        text("""
        INSERT INTO weather.job(kind,idempotency_key,payload,not_before,deadline,expected_revision)
        VALUES(:kind,:key,CAST(:payload AS jsonb),:not_before,:deadline,:revision)
        ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
    """),
        values,
    )
    inserted = result.scalar_one_or_none()
    if inserted is not None:
        return cast(UUID, inserted)
    # A unique-key race waits for the first transaction, then reads its committed row.
    existing = (
        (
            await connection.execute(
                text("""
        SELECT id,kind,payload,deadline,expected_revision FROM weather.job
        WHERE idempotency_key=:key
    """),
                {"key": key},
            )
        )
        .mappings()
        .one()
    )
    if (
        existing["kind"] != kind
        or existing["payload"] != dict(payload)
        or existing["deadline"] != deadline
        or existing["expected_revision"] != expected_revision
    ):
        raise IdempotencyConflict("Idempotency key is bound to different work")
    return cast(UUID, existing["id"])


async def append_outbox(
    connection: AsyncConnection,
    *,
    topic: str,
    key: str,
    payload: Mapping[str, Any],
    not_before: datetime,
    deadline: datetime,
    expected_revision: int | None = None,
) -> UUID:
    if not 1 <= len(topic) <= 128 or not 1 <= len(key) <= 256:
        raise ValueError("Invalid outbox identity")
    if not_before.tzinfo is None or deadline.tzinfo is None:
        raise ValueError("Outbox times must be aware")
    result = await connection.execute(
        text("""
        INSERT INTO weather.outbox(topic,idempotency_key,payload,not_before,deadline,
                                   expected_revision)
        VALUES(:topic,:key,CAST(:payload AS jsonb),:not_before,:deadline,:revision)
        ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
    """),
        {
            "topic": topic,
            "key": key,
            "payload": _json(payload),
            "not_before": not_before,
            "deadline": deadline,
            "revision": expected_revision,
        },
    )
    inserted = result.scalar_one_or_none()
    if inserted is not None:
        return cast(UUID, inserted)
    existing = (
        (
            await connection.execute(
                text("""
        SELECT id,topic,payload,deadline,expected_revision FROM weather.outbox
        WHERE idempotency_key=:key
    """),
                {"key": key},
            )
        )
        .mappings()
        .one()
    )
    if (
        existing["topic"] != topic
        or existing["payload"] != dict(payload)
        or existing["deadline"] != deadline
        or existing["expected_revision"] != expected_revision
    ):
        raise IdempotencyConflict("Outbox key is bound to different work")
    return cast(UUID, existing["id"])


async def expire(connection: AsyncConnection) -> int:
    result = await connection.execute(
        text("""
        UPDATE weather.job SET status='expired',lease_owner=NULL,lease_until=NULL,
               last_error='deadline'
        WHERE status IN ('queued','running') AND deadline<=clock_timestamp()
    """)
    )
    return result.rowcount


async def claim(
    connection: AsyncConnection, *, owner: str, lease_seconds: int = 60
) -> Job | None:
    if not 1 <= len(owner) <= 128 or not 1 <= lease_seconds <= 300:
        raise ValueError("Invalid lease owner or duration")
    row = (
        (
            await connection.execute(
                text("""
        WITH candidate AS (
          SELECT id FROM weather.job WHERE deadline>clock_timestamp() AND
            ((status='queued' AND not_before<=clock_timestamp()) OR
             (status='running' AND lease_until<=clock_timestamp()))
          ORDER BY not_before,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE weather.job j SET status='running',attempt=attempt+1,
          fencing_token=fencing_token+1,lease_owner=:owner,
          lease_until=LEAST(clock_timestamp()+make_interval(secs=>:seconds),deadline)
        FROM candidate WHERE j.id=candidate.id
        RETURNING j.id,j.kind,j.payload,j.expected_revision,j.deadline,j.attempt,
                  j.lease_owner,j.fencing_token
    """),
                {"owner": owner, "seconds": lease_seconds},
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
        expected_revision=cast(int | None, row["expected_revision"]),
        deadline=cast(datetime, row["deadline"]),
        attempt=int(row["attempt"]),
        lease_owner=str(row["lease_owner"]),
        fencing_token=int(row["fencing_token"]),
    )


def _identity(job: Job) -> dict[str, Any]:
    return {"id": job.id, "owner": job.lease_owner, "token": job.fencing_token}


_OWNED = """id=:id AND status='running' AND lease_owner=:owner AND fencing_token=:token
            AND lease_until>clock_timestamp() AND deadline>clock_timestamp()"""


async def heartbeat(
    connection: AsyncConnection, job: Job, *, lease_seconds: int = 60
) -> bool:
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
          lease_owner=NULL,lease_until=NULL,last_error=NULL WHERE {_OWNED}
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
          lease_owner=NULL,lease_until=NULL,last_error=:reason
        WHERE {_OWNED}
    """),
        {**_identity(job), "retry": retry, "delay": delay_seconds, "reason": reason},
    )
    return updated.rowcount == 1


@asynccontextmanager
async def fenced_transaction(
    engine: AsyncEngine, job: Job
) -> AsyncIterator[AsyncConnection]:
    """Lock and recheck ownership around catalog writes; any fence loss rolls back."""
    async with engine.begin() as connection:
        owned = (
            await connection.execute(
                text(f"""
            SELECT id FROM weather.job WHERE {_OWNED} FOR UPDATE
        """),
                _identity(job),
            )
        ).scalar_one_or_none()
        if owned is None:
            raise LeaseLost("Job no longer owns its lease")
        yield connection
        # PostgreSQL row lock prevents a competing reclaim during this transaction.
        # Wall-clock expiry still aborts the whole transaction before commit.
        owned = (
            await connection.execute(
                text(f"""
            SELECT id FROM weather.job WHERE id=:id AND fencing_token=:token
              AND deadline>clock_timestamp() AND
                (({_OWNED}) OR status='succeeded')
        """),
                _identity(job),
            )
        ).scalar_one_or_none()
        if owned is None:
            raise LeaseLost("Job lease expired before catalog commit")
