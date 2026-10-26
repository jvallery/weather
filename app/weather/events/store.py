# SPDX-License-Identifier: MIT
"""The single event write path: lock, validate, append an immutable revision, link days, emit.

Every writer (the measured rules, a supplied visual/synthesis producer, an operator) goes through
``settle`` under one transaction-scoped lock per logical key, so revision numbers, overrides and
withdrawals serialize. Nothing here logs keys, evidence or authors.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.db.days import ensure_days

from .rules import Basis, Candidate, local_dates

TOPIC = "weather_event.changed"
OUTBOX_DEADLINE = timedelta(days=1)
Visibility = Literal["public", "private", "withdrawn"]
Action = Literal["created", "revised", "withdrawn", "unchanged"]
# A rule run never undoes an operator decision on unchanged evidence; on changed evidence it
# withdraws with this reason and keeps the human revision for review until an operator settles.
OVERRIDE_STALE = "override_stale"


@dataclass(frozen=True)
class Current:
    id: UUID
    revision: int
    visibility: Visibility
    withdrawal_reason: str | None
    author_kind: str
    evidence_hash: str
    row: Mapping[str, Any]


CURRENT_SQL = """SELECT e.id,e.logical_key,e.rule_family,e.revision,r.visibility,
  r.withdrawal_reason,r.origin,r.basis,r.camera_id,r.starts_at,r.ends_at,r.time_precision,
  r.rule_revision,r.author_kind,r.author,r.reason,r.label,r.measurement,r.evidence_ids,
  r.evidence_hash
FROM weather.weather_event e
LEFT JOIN weather.weather_event_revision r ON r.event_id=e.id AND r.revision=e.revision"""


def _current(row: Mapping[str, Any] | None) -> Current | None:
    if row is None or not row["revision"]:
        return None
    return Current(
        id=cast(UUID, row["id"]),
        revision=int(row["revision"]),
        visibility=cast(Visibility, row["visibility"]),
        withdrawal_reason=row["withdrawal_reason"],
        author_kind=str(row["author_kind"]),
        evidence_hash=str(row["evidence_hash"]),
        row=row,
    )


async def current_states(connection: AsyncConnection, keys: list[str]) -> dict[str, Current]:
    rows = (
        await connection.execute(
            text(CURRENT_SQL + " WHERE e.logical_key = ANY(:keys)"), {"keys": keys}
        )
    ).mappings()
    result = {}
    for row in rows:
        state = _current(dict(row))
        if state is not None:
            result[str(row["logical_key"])] = state
    return result


def decide(candidate: Candidate | None, current: Current | None) -> Visibility | None:
    """What a rule run should write: a visibility, or None for a no-op (retries are no-ops)."""
    if current is not None and current.withdrawal_reason == OVERRIDE_STALE:
        return None
    if candidate is None:
        return None if current is None or current.visibility == "withdrawn" else "withdrawn"
    if current is None:
        return "public"
    if current.author_kind == "operator":
        return None if current.evidence_hash == candidate.evidence_hash else "withdrawn"
    if current.evidence_hash == candidate.evidence_hash and current.visibility == "public":
        return None
    return "public"


async def _lock(connection: AsyncConnection, key: str) -> Current | None:
    # The advisory lock also covers a key that has no event row yet.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 41))"), {"key": key}
    )
    row = (
        (
            await connection.execute(
                text(CURRENT_SQL + " WHERE e.logical_key=:key FOR UPDATE OF e"), {"key": key}
            )
        )
        .mappings()
        .first()
    )
    return _current(dict(row) if row is not None else None)


async def _append(
    connection: AsyncConnection,
    *,
    key: str,
    family: str,
    content: Mapping[str, Any],
    visibility: Visibility,
    withdrawal_reason: str | None,
    now: datetime,
) -> tuple[UUID, int]:
    # The caller holds the key's lock, so the event row either exists or is ours to create.
    event_id = (
        await connection.execute(
            text("""
        INSERT INTO weather.weather_event(logical_key,rule_family) VALUES(:key,:family)
        ON CONFLICT (logical_key) DO NOTHING RETURNING id
    """),
            {"key": key, "family": family},
        )
    ).scalar_one_or_none() or (
        await connection.execute(
            text("SELECT id FROM weather.weather_event WHERE logical_key=:key"), {"key": key}
        )
    ).scalar_one()
    revision = (
        await connection.execute(
            text("""
        UPDATE weather.weather_event SET revision=revision+1 WHERE id=:id RETURNING revision
    """),
            {"id": event_id},
        )
    ).scalar_one()
    starts_at, ends_at = content["starts_at"], content["ends_at"]
    await connection.execute(
        text("""
        INSERT INTO weather.weather_event_revision(event_id,revision,visibility,withdrawal_reason,
          origin,basis,camera_id,starts_at,ends_at,time_precision,rule_revision,author_kind,
          author,reason,label,measurement,evidence_ids,evidence_hash)
        VALUES(:id,:revision,:visibility,:withdrawal,:origin,:basis,:camera,:starts_at,:ends_at,
          :precision,:rule_revision,:author_kind,:author,:reason,:label,
          CAST(:measurement AS jsonb),CAST(:evidence AS uuid[]),:evidence_hash)
    """),
        {
            "id": event_id,
            "revision": revision,
            "visibility": visibility,
            "withdrawal": withdrawal_reason,
            "origin": content["origin"],
            "basis": content["basis"],
            "camera": content["camera_id"],
            "starts_at": starts_at,
            "ends_at": ends_at,
            "precision": content["time_precision"],
            "rule_revision": content["rule_revision"],
            "author_kind": content["author_kind"],
            "author": content.get("author"),
            "reason": content.get("reason"),
            "label": content["label"],
            "measurement": json.dumps(content["measurement"], allow_nan=False),
            "evidence": list(content["evidence_ids"]),
            "evidence_hash": content["evidence_hash"],
        },
    )
    # Day links come from the pinned time model; the calendar rows must exist first.
    dates = local_dates(starts_at, ends_at)
    await ensure_days(connection, dates[0], dates[-1])
    await connection.execute(
        text("""
        INSERT INTO weather.weather_event_day(event_id,revision,local_date)
        SELECT :id,:revision,d FROM unnest(CAST(:dates AS date[])) AS d
    """),
        {"id": event_id, "revision": revision, "dates": dates},
    )
    await jobs.append_outbox(
        connection,
        topic=TOPIC,
        key=f"weather_event:{event_id}:{revision}",
        payload={"event_id": str(event_id), "revision": revision},
        not_before=now,
        deadline=now + OUTBOX_DEADLINE,
    )
    return cast(UUID, event_id), int(revision)


def _content(candidate: Candidate, author_kind: str) -> dict[str, Any]:
    return {
        "origin": candidate.origin,
        "basis": candidate.evidence_basis,
        "camera_id": candidate.camera,
        "starts_at": candidate.start,
        "ends_at": candidate.end,
        "time_precision": candidate.precision,
        "rule_revision": candidate.rule_revision,
        "author_kind": author_kind,
        "label": candidate.label,
        "measurement": dict(candidate.measurement),
        "evidence_ids": candidate.evidence,
        "evidence_hash": candidate.evidence_hash,
    }


def _copy(current: Current, **changes: Any) -> dict[str, Any]:
    row = current.row
    content = {
        name: row[name]
        for name in (
            "origin",
            "basis",
            "camera_id",
            "starts_at",
            "ends_at",
            "time_precision",
            "rule_revision",
            "author_kind",
            "author",
            "reason",
            "label",
            "measurement",
            "evidence_ids",
            "evidence_hash",
        )
    }
    content.update(changes)
    return content


Derive = Callable[[AsyncConnection], Awaitable[Candidate | None]]


async def settle(
    connection: AsyncConnection,
    key: str,
    derive: Derive,
    *,
    family: str,
    author_kind: Literal["rule", "model"],
    now: datetime,
) -> Action:
    """Lock the key, then derive from evidence read *after* the lock, then write or no-op.

    Deriving under the lock means evidence that changed while the job prepared is re-read, and
    invalid evidence withdraws the old public claim in this same transaction.
    """
    current = await _lock(connection, key)
    candidate = await derive(connection)
    visibility = decide(candidate, current)
    if visibility is None:
        return "unchanged"
    if candidate is None:
        assert current is not None
        await _append(
            connection,
            key=key,
            family=family,
            content=_copy(current, author_kind=author_kind, author=None, reason=None),
            visibility="withdrawn",
            withdrawal_reason="evidence_invalid",
            now=now,
        )
        return "withdrawn"
    stale = current is not None and current.author_kind == "operator"
    await _append(
        connection,
        key=key,
        family=family,
        content=_content(candidate, author_kind),
        visibility=visibility,
        withdrawal_reason=OVERRIDE_STALE if stale else None,
        now=now,
    )
    if stale:
        return "withdrawn"
    return "created" if current is None else "revised"


async def record(connection: AsyncConnection, candidate: Candidate, *, now: datetime) -> Action:
    """Write path for supplied, already-validated visual/synthesis events (#23)."""
    if candidate.origin not in ("visual", "synthesis"):
        raise ValueError("Only model-produced events are recorded here")

    async def supplied(_connection: AsyncConnection) -> Candidate:
        return candidate

    return await settle(
        connection,
        candidate.key,
        supplied,
        family=candidate.family,
        author_kind="model",
        now=now,
    )


def _operator(author: str, reason: str) -> None:
    if not 1 <= len(author) <= 256 or not 1 <= len(reason) <= 2000:
        raise ValueError("An authenticated operator and a reason are required")


async def override(
    connection: AsyncConnection,
    event_id: UUID,
    *,
    author: str,
    reason: str,
    visibility: Literal["public", "private", "withdrawn"],
    label: str | None = None,
    now: datetime | None = None,
) -> int:
    """Operator hook (#44): change visibility or label only; never a station measurement."""
    _operator(author, reason)
    key = (
        await connection.execute(
            text("SELECT logical_key FROM weather.weather_event WHERE id=:id"), {"id": event_id}
        )
    ).scalar_one()
    current = await _lock(connection, key)
    if current is None:
        raise LookupError("No such event")
    _, revision = await _append(
        connection,
        key=key,
        family=str(current.row["rule_family"]),
        content=_copy(
            current,
            author_kind="operator",
            author=author,
            reason=reason,
            label=label if label is not None else current.row["label"],
        ),
        visibility=visibility,
        withdrawal_reason="operator" if visibility == "withdrawn" else None,
        now=now or datetime.now(UTC),
    )
    return revision


async def write_manual(
    connection: AsyncConnection,
    *,
    author: str,
    reason: str,
    label: str,
    basis: Basis,
    start: datetime,
    end: datetime,
    camera: str | None = None,
    evidence: tuple[UUID, ...] = (),
    event_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[UUID, int]:
    """Operator hook (#44): a standalone manual annotation, labelled manual and attributed.

    Public like every event, so it may only describe an interval that ended 600 s ago."""
    _operator(author, reason)
    if end > (now or datetime.now(UTC)) - timedelta(seconds=600):
        raise ValueError("A manual event must have ended at least 600 s ago")
    if event_id is None:
        key = f"manual:{uuid4()}"
    else:
        key = (
            await connection.execute(
                text(
                    "SELECT logical_key FROM weather.weather_event WHERE id=:id "
                    "AND rule_family='manual'"
                ),
                {"id": event_id},
            )
        ).scalar_one()
    candidate = Candidate(
        key=key,
        family="manual",
        origin="manual",
        basis=basis,
        start=start,
        end=end,
        precision="interval",
        rule_revision="manual/1",
        evidence=evidence,
        camera=camera,
        label=label,
    )
    await _lock(connection, key)
    return await _append(
        connection,
        key=key,
        family="manual",
        content={**_content(candidate, "operator"), "author": author, "reason": reason},
        visibility="public",
        withdrawal_reason=None,
        now=now or datetime.now(UTC),
    )
