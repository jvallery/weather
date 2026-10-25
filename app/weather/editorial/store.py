# SPDX-License-Identifier: MIT
"""Editorial writes (#44): one CAS on the subject head, then revision, audit and outbox together.

Every function runs in the caller's transaction as `weather_editor`. The head bump comes first,
so a concurrent PATCH with the same `expected_revision` waits on the row lock, then matches
nothing and gets a 409 before it has written anything; its transaction rolls back clean.
Event corrections and manual events go through #41's hooks; video descriptions go through the
shared search projection. The actor is always the server-side allowlisted identity. Nothing
here logs subjects, text, actors or evidence.
"""

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.events import store as events

from . import projection
from .models import (
    AuditEntry,
    DescriptionState,
    EventRevision,
    EventState,
    Evidence,
    ManualEvent,
    Subject,
    SubjectPatch,
    Tag,
    VideoSummary,
)

TOPIC = "editorial.changed"
OUTBOX_DEADLINE = timedelta(days=1)
Kind = Literal["video", "event"]


class NotFound(LookupError):
    pass


class Conflict(Exception):
    def __init__(self, current: int) -> None:
        super().__init__("Revision conflict")
        self.current = current


class RebaseRequired(Exception):
    """The human revision was written against media or evidence that has since changed."""


class Invalid(ValueError):
    """A well-formed request that this subject cannot take."""


ALLOWED_SQL = """SELECT EXISTS (SELECT 1 FROM weather.editorial_operator
WHERE username=:username AND application=:application AND active)"""
KIND_SQL = """SELECT 'video' FROM weather.video WHERE id=:id
UNION ALL SELECT 'event' FROM weather.weather_event WHERE id=:id"""
HEAD_SQL = "SELECT revision,updated_at FROM weather.editorial_subject WHERE subject_id=:id"
REVISION_SQL = """SELECT public_revision,description,media_sha256,evidence_hash,public_tags,
  private_tags,favorite,note,event_revision FROM weather.editorial_revision
WHERE subject_id=:id AND revision=:revision"""
CREATE_SQL = """INSERT INTO weather.editorial_subject(subject_id,kind,video_id,event_id,revision,
  updated_at) VALUES(:id,:kind,:video,:event,1,clock_timestamp())
ON CONFLICT (subject_id) DO NOTHING RETURNING revision"""
BUMP_SQL = """UPDATE weather.editorial_subject SET revision=revision+1,updated_at=clock_timestamp()
WHERE subject_id=:id AND revision=:expected RETURNING revision"""
INSERT_REVISION_SQL = """INSERT INTO weather.editorial_revision(subject_id,revision,
  public_revision,description,media_sha256,evidence_hash,public_tags,private_tags,favorite,note,
  event_revision)
VALUES(:id,:revision,:public_revision,:description,:media,:evidence,CAST(:public AS varchar[]),
  CAST(:private AS varchar[]),:favorite,:note,:event_revision)"""
AUDIT_SQL = """INSERT INTO weather.editorial_audit(subject_id,action,actor,reason,old_revision,
  new_revision,input,media_sha256,evidence_hash,evidence_ids,event_revision)
VALUES(:id,:action,:actor,:reason,:old,:new,CAST(:input AS jsonb),:media,:evidence,
  CAST(:evidence_ids AS uuid[]),:event_revision)"""
VIDEO_SQL = """SELECT s.*,v.camera_id,v.cycle_date,v.segment,v.starts_at,v.ends_at,
  v.state AS video_state FROM weather.video_editorial_state s
JOIN weather.video v ON v.id=s.video_id WHERE s.video_id=:id"""
EVENT_SQL = events.CURRENT_SQL + " WHERE e.id=:id"
HUMAN_EVENT_SQL = """SELECT revision,label,visibility,withdrawal_reason,author_kind,evidence_hash
FROM weather.weather_event_revision WHERE event_id=:id AND author_kind='operator'
ORDER BY revision DESC LIMIT 1"""
HISTORY_SQL = """SELECT action,actor,recorded_at,reason,old_revision,new_revision
FROM weather.editorial_audit WHERE subject_id=:id
ORDER BY new_revision DESC LIMIT 20"""
# Manual events are public, so like every public image they describe only the settled past.
MANUAL_EMBARGO = timedelta(seconds=600)
EMPTY: dict[str, Any] = {
    "public_revision": 0,
    "description": None,
    "media_sha256": None,
    "evidence_hash": None,
    "public_tags": [],
    "private_tags": [],
    "favorite": False,
    "note": None,
    "event_revision": None,
}


async def allowed(connection: AsyncConnection, username: str, application: str) -> bool:
    """Checked on every private request; there is no positive authorization cache."""
    result = await connection.execute(
        text(ALLOWED_SQL), {"username": username, "application": application}
    )
    return bool(result.scalar_one())


async def _kind(connection: AsyncConnection, subject_id: UUID) -> Kind:
    kind = (await connection.execute(text(KIND_SQL), {"id": subject_id})).scalar_one_or_none()
    if kind == "video":
        return "video"
    if kind == "event":
        return "event"
    raise NotFound("No such subject")


async def _one(connection: AsyncConnection, sql: str, **params: Any) -> dict[str, Any] | None:
    row = (await connection.execute(text(sql), params)).mappings().one_or_none()
    return dict(row) if row is not None else None


def _event_revision(row: Mapping[str, Any]) -> EventRevision:
    return EventRevision(
        revision=row["revision"],
        label=row["label"],
        visibility=row["visibility"],
        withdrawal_reason=row["withdrawal_reason"],
        author_kind=row["author_kind"],
        evidence_hash=row["evidence_hash"],
    )


async def read(connection: AsyncConnection, subject_id: UUID) -> Subject:
    kind = await _kind(connection, subject_id)
    head = await _one(connection, HEAD_SQL, id=subject_id)
    content = dict(EMPTY)
    if head is not None:
        content = (
            await _one(connection, REVISION_SQL, id=subject_id, revision=head["revision"])
            or content
        )
    video = description = event = None
    if kind == "video":
        row = await _one(connection, VIDEO_SQL, id=subject_id)
        assert row is not None
        video = VideoSummary(
            camera=row["camera_id"],
            cycle_date=row["cycle_date"],
            segment=row["segment"],
            start=row["starts_at"],
            end=row["ends_at"],
            state=row["video_state"],
            withdrawn=row["withdrawn"],
        )
        human, active = row["human"], bool(row["human_active"])
        automated = row["automated"] if row["analysis_revision"] is not None else None
        description = DescriptionState(
            human=human,
            active=active,
            stale=human is not None and not active,
            effective=human if active else automated,
            automated=automated,
            bound=Evidence(
                media_sha256=row["bound_media_sha256"],
                evidence_hash=row["bound_evidence_hash"],
            )
            if human is not None
            else None,
            current=Evidence(media_sha256=row["media_sha256"], evidence_hash=row["evidence_hash"]),
        )
    else:
        current = await _one(connection, EVENT_SQL, id=subject_id)
        assert current is not None
        human_row = await _one(connection, HUMAN_EVENT_SQL, id=subject_id)
        event = EventState(
            kind=current["rule_family"],
            origin=current["origin"],
            basis=current["basis"],
            camera=current["camera_id"],
            start=current["starts_at"],
            end=current["ends_at"],
            current=_event_revision(current),
            human=_event_revision(human_row) if human_row else None,
            stale=current["withdrawal_reason"] == events.OVERRIDE_STALE,
        )
    history = (await connection.execute(text(HISTORY_SQL), {"id": subject_id})).mappings()
    return Subject(
        subject_id=subject_id,
        kind=kind,
        revision=head["revision"] if head else 0,
        updated_at=head["updated_at"] if head else None,
        video=video,
        description=description,
        event=event,
        tags=sorted(
            [Tag(tag=t, visibility="public") for t in content["public_tags"]]
            + [Tag(tag=t, visibility="private") for t in content["private_tags"]],
            key=lambda tag: (tag.visibility, tag.tag),
        ),
        favorite=content["favorite"],
        note=content["note"],
        audit=[AuditEntry.model_validate(dict(row)) for row in history],
    )


async def _bump(connection: AsyncConnection, subject_id: UUID, kind: Kind, expected: int) -> int:
    """The CAS. Nothing else has been written when this raises Conflict."""
    if expected == 0:
        new = (
            await connection.execute(
                text(CREATE_SQL),
                {
                    "id": subject_id,
                    "kind": kind,
                    "video": subject_id if kind == "video" else None,
                    "event": subject_id if kind == "event" else None,
                },
            )
        ).scalar_one_or_none()
    else:
        new = (
            await connection.execute(text(BUMP_SQL), {"id": subject_id, "expected": expected})
        ).scalar_one_or_none()
    if new is None:
        head = await _one(connection, HEAD_SQL, id=subject_id)
        raise Conflict(head["revision"] if head else 0)
    return int(new)


async def _append(
    connection: AsyncConnection,
    subject_id: UUID,
    kind: Kind,
    revision: int,
    content: Mapping[str, Any],
    *,
    action: str,
    actor: str,
    reason: str,
    changes: Mapping[str, Any],
    evidence: tuple[str | None, str | None, list[UUID]],
    public: bool,
    now: datetime,
) -> None:
    """The revision, its complete audit row and the outbox entry, in the caller's transaction.

    `evidence` is what the subject's public content depended on when it was edited: the web
    rendition SHA-256, the analysis or event evidence hash and the event's evidence IDs.
    """
    await connection.execute(
        text(INSERT_REVISION_SQL),
        {
            "id": subject_id,
            "revision": revision,
            "public_revision": content["public_revision"],
            "description": content["description"],
            "media": content["media_sha256"],
            "evidence": content["evidence_hash"],
            "public": list(content["public_tags"]),
            "private": list(content["private_tags"]),
            "favorite": content["favorite"],
            "note": content["note"],
            "event_revision": content["event_revision"],
        },
    )
    await connection.execute(
        text(AUDIT_SQL),
        {
            "id": subject_id,
            "action": action,
            "actor": actor,
            "reason": reason,
            "old": revision - 1,
            "new": revision,
            "input": json.dumps(changes, allow_nan=False, sort_keys=True),
            "media": evidence[0],
            "evidence": evidence[1],
            "evidence_ids": evidence[2],
            "event_revision": content["event_revision"],
        },
    )
    await jobs.append_outbox(
        connection,
        topic=TOPIC,
        key=f"editorial:{subject_id}:{revision}",
        # `public` tells the handler whether anything public changed (a favorite, note or
        # private tag change needs no re-projection).
        payload={
            "subject_id": str(subject_id),
            "kind": kind,
            "revision": revision,
            "public": public,
        },
        not_before=now,
        deadline=now + OUTBOX_DEADLINE,
    )


async def patch(
    connection: AsyncConnection,
    subject_id: UUID,
    body: SubjectPatch,
    *,
    actor: str,
    profile_id: str,
    now: datetime,
) -> Subject:
    kind = await _kind(connection, subject_id)
    revision = await _bump(connection, subject_id, kind, body.expected_revision)
    previous = (
        await _one(connection, REVISION_SQL, id=subject_id, revision=revision - 1)
        if revision > 1
        else None
    )
    content = dict(previous or EMPTY)
    changes = body.changes
    fields = changes.model_fields_set
    action = "rebase" if "rebase" in fields else "edit"
    evidence: tuple[str | None, str | None, list[UUID]] = (None, None, [])
    if kind == "video":
        if "event" in fields:
            raise Invalid("Event changes apply to events only")
        row = await _one(connection, VIDEO_SQL, id=subject_id)
        assert row is not None
        evidence = (row["media_sha256"], row["evidence_hash"], [])
        # The head already points at the revision being written, so the view's human columns
        # are empty here: apply its binding rule to the previous revision instead.
        human = content["description"]
        stale = human is not None and not (
            content["media_sha256"] == row["media_sha256"]
            and content["evidence_hash"] in (None, row["evidence_hash"])
        )
        # Clearing never needs a rebase; writing new text over stale evidence does.
        writes = "description" in fields and changes.description is not None
        if stale and writes and "rebase" not in fields:
            raise RebaseRequired("Media or evidence changed since the human revision")
        if "rebase" in fields and not stale:
            raise Invalid("Nothing to rebase")
        if "description" in fields or "rebase" in fields:
            written = changes.description if "description" in fields else human
            if written is None:
                content.update(description=None, media_sha256=None, evidence_hash=None)
            else:
                playable = not row["withdrawn"] and row["video_state"] in ("published", "partial")
                if not playable or row["media_sha256"] is None:
                    raise Invalid("No published rendition to describe")
                content.update(
                    description=written,
                    media_sha256=row["media_sha256"],
                    evidence_hash=row["evidence_hash"],
                )
    else:
        if "description" in fields:
            raise Invalid("Descriptions apply to videos only")
        current = await _one(connection, EVENT_SQL, id=subject_id)
        assert current is not None
        stale = current["withdrawal_reason"] == events.OVERRIDE_STALE
        if stale and "event" in fields and "rebase" not in fields:
            raise RebaseRequired("Evidence changed since the operator revision")
        if "rebase" in fields and not stale:
            raise Invalid("Nothing to rebase")
        if "event" in fields or "rebase" in fields:
            # A rebase re-applies the last operator decision to the current evidence unless the
            # request says otherwise; a plain correction starts from the current revision.
            base = current
            if "rebase" in fields:
                base = await _one(connection, HUMAN_EVENT_SQL, id=subject_id) or current
            change = changes.event
            label = change.label if change and change.label else base["label"]
            visibility = change.visibility if change and change.visibility else base["visibility"]
            content["event_revision"] = await events.override(
                connection,
                subject_id,
                author=actor,
                reason=body.reason,
                visibility=visibility,
                label=label,
                now=now,
            )
        else:
            content["event_revision"] = int(current["revision"])
        evidence = (None, current["evidence_hash"], list(current["evidence_ids"] or []))
    if "tags" in fields:
        public_tags = sorted(t.tag for t in changes.tags if t.visibility == "public")
        if kind == "event" and public_tags:
            raise Invalid("Event tags are private")
        content["public_tags"] = public_tags
        content["private_tags"] = sorted(t.tag for t in changes.tags if t.visibility == "private")
    if "favorite" in fields:
        content["favorite"] = bool(changes.favorite)
    if "note" in fields:
        content["note"] = changes.note
    before = previous or EMPTY
    # Only public content moves the public revision, the outbox projection and search.
    if kind == "video":
        public = any(
            content[name] != before[name]
            for name in ("description", "media_sha256", "evidence_hash", "public_tags")
        )
    else:
        public = "event" in fields or "rebase" in fields
    content["public_revision"] = int(before["public_revision"]) + (1 if public else 0)
    await _append(
        connection,
        subject_id,
        kind,
        revision,
        content,
        action=action,
        actor=actor,
        reason=body.reason,
        changes=changes.model_dump(mode="json", exclude_unset=True),
        evidence=evidence,
        public=public,
        now=now,
    )
    if kind == "video" and public:
        # Public text changes reach search in this transaction; old text stops matching now.
        await projection.project(connection, subject_id, profile_id=profile_id, now=now)
    return await read(connection, subject_id)


async def create_event(
    connection: AsyncConnection, body: ManualEvent, *, actor: str, now: datetime
) -> Subject:
    """A standalone manual annotation: labelled manual, attributed human, audited."""
    if body.end > now - MANUAL_EMBARGO:
        raise Invalid("A manual event must have ended at least 600 s ago")
    event_id, event_revision = await events.write_manual(
        connection,
        author=actor,
        reason=body.reason,
        label=body.label,
        basis=body.basis,
        start=body.start,
        end=body.end,
        camera=body.camera,
        evidence=tuple(body.evidence_ids),
        now=now,
    )
    await _bump(connection, event_id, "event", 0)
    await _append(
        connection,
        event_id,
        "event",
        1,
        {**EMPTY, "public_revision": 1, "event_revision": event_revision},
        action="manual_event",
        actor=actor,
        reason=body.reason,
        changes=body.model_dump(mode="json"),
        evidence=(None, None, list(body.evidence_ids)),
        public=True,
        now=now,
    )
    return await read(connection, event_id)
