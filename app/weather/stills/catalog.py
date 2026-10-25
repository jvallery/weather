# SPDX-License-Identifier: MIT
"""Selection rows for hourly stills. Every write uses the caller's transaction.

`record` writes one `archival_still` revision per (camera, slot) under the still product's
advisory lock (the same one #18's commit takes), so a selection and the publication that
archived its original commit together or not at all. A rerun with the same outcome is a no-op;
a different outcome (a correction) retires the active revision and its derivatives and adds the
next one, so exactly one revision is ever active.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db.days import ensure_days
from weather.pipeline.intake import utc
from weather.publish.model import Committed, Product
from weather.timemodel import HourSlot

from .policy import POLICY, Choice
from .sanitize import RECIPE

RETENTION_YEARS = 5


@dataclass(frozen=True)
class SegmentBinding:
    cycle_date: date
    kind: str
    revision: int


@dataclass(frozen=True)
class Original:
    """The archived native original: the TAR member's exact bytes in `weather-originals`."""

    sha256: str
    bytes: int
    width: int
    height: int


def product_key(slot: HourSlot) -> str:
    return Product.still(
        camera_id="identity",
        slot_start=slot.start,
        source_start=slot.start,
        source_end=slot.end,
        public_not_before=slot.end,
        files=(),
    ).key


async def lock(connection: AsyncConnection, camera: str, slot: HourSlot) -> None:
    # The identity #18's catalog locks for the still product; reentrant within a transaction.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"still:{camera}:{product_key(slot)}"},
    )


async def segment_for(
    connection: AsyncConnection, camera: str, instant: datetime
) -> SegmentBinding | None:
    """The camera's current #15 segment (and so its solar cycle) containing the instant."""
    row = (
        await connection.execute(
            text("""
        SELECT cycle_date,kind,revision FROM weather.segment
        WHERE current AND camera_id=:camera AND starts_at <= :at AND :at < ends_at
    """),
            {"camera": camera, "at": instant},
        )
    ).one_or_none()
    return SegmentBinding(row.cycle_date, str(row.kind), int(row.revision)) if row else None


async def selected(
    connection: AsyncConnection, camera: str, first: datetime, last: datetime
) -> set[datetime]:
    """Slot starts in [first, last] that already have an active selection."""
    rows = await connection.execute(
        text("""
        SELECT slot_starts_at FROM weather.archival_still
        WHERE active AND camera_id=:camera AND slot_starts_at BETWEEN :first AND :last
    """),
        {"camera": camera, "first": first, "last": last},
    )
    return set(rows.scalars())


async def reselectable(
    connection: AsyncConnection, camera: str, slots: list[datetime]
) -> set[datetime]:
    """Of `slots`, those with no active selection or only `selection_expired` (#19): no
    selection ever ran for them, so their sealed input is still pinned and may be selected.
    Every other outcome (a capture, `no_capture`, `no_eligible_capture`, `withdrawn`) is
    final here."""
    if not slots:
        return set()
    rows = await connection.execute(
        text("""
        SELECT s.slot FROM unnest(CAST(:slots AS timestamptz[])) AS s(slot)
        WHERE NOT EXISTS (
          SELECT 1 FROM weather.archival_still a
          WHERE a.active AND a.camera_id=:camera AND a.slot_starts_at = s.slot
            AND NOT (a.state = 'missing' AND a.reason = 'selection_expired'))
    """),
        {"camera": camera, "slots": slots},
    )
    return set(rows.scalars())


def retain_until(slot: HourSlot) -> date:
    day = slot.weather_day.local_date
    try:
        return day.replace(year=day.year + RETENTION_YEARS)
    except ValueError:  # 29 February
        return day.replace(year=day.year + RETENTION_YEARS, day=28)


async def record(
    connection: AsyncConnection,
    *,
    camera: str,
    slot: HourSlot,
    choice: Choice,
    segment: SegmentBinding | None = None,
    original: Original | None = None,
    committed: Committed | None = None,
) -> int:
    """Write the active selection for (camera, slot); returns its revision."""
    selected_capture = choice.winner is not None
    if selected_capture != (segment is not None and original is not None and committed is not None):
        raise ValueError("A selected capture needs its segment, original and publication")
    await lock(connection, camera, slot)
    local_date = slot.weather_day.local_date
    await ensure_days(connection, local_date, local_date)
    assets = committed.assets if committed is not None else {}
    current = (
        await connection.execute(
            text("""
        SELECT id,revision,state,reason,sha256,publication_id FROM weather.archival_still
        WHERE active AND camera_id=:camera AND slot_starts_at=:slot FOR UPDATE
    """),
            {"camera": camera, "slot": slot.start},
        )
    ).one_or_none()
    publication = committed.publication_id if committed is not None else None
    sha256 = original.sha256 if original is not None else None
    if current is not None:
        if (current.state, current.reason, current.sha256, current.publication_id) == (
            choice.state,
            choice.reason,
            sha256,
            publication,
        ):
            return int(current.revision)  # a rerun of the committed outcome
        # A correction: the old revision and its derivatives are retired, never deleted; its
        # original stays archived privately for its retention.
        await connection.execute(
            text("""
            UPDATE weather.archival_still SET active=false,retired_at=clock_timestamp()
            WHERE id=:id
        """),
            {"id": current.id},
        )
        await connection.execute(
            text("""
            UPDATE weather.still_derivative SET state='retired'
            WHERE archival_still_id=:id AND state<>'retired'
        """),
            {"id": current.id},
        )
    revision = 1 + int(
        (
            await connection.execute(
                text("""
            SELECT coalesce(max(revision),0) FROM weather.archival_still
            WHERE camera_id=:camera AND slot_starts_at=:slot
        """),
                {"camera": camera, "slot": slot.start},
            )
        ).scalar_one()
    )
    winner = choice.winner
    values: dict[str, Any] = {
        "camera": camera,
        "slot": slot.start,
        "local_date": local_date,
        "revision": revision,
        "state": choice.state,
        "reason": choice.reason,
        "policy": POLICY,
        "eligible": choice.eligible,
        "rejections": json.dumps(choice.rejections, sort_keys=True),
        "attempt_id": winner.attempt_id if winner else None,
        # The sealed chunk's name within its UTC-hour directory; private, never public.
        "chunk": f"{winner.chunk.parent.name}/{winner.chunk.name}" if winner else None,
        "member": winner.member if winner else None,
        "source_ts": utc(winner.time) if winner else None,
        "capture_upper": utc(winner.upper) if winner and winner.upper is not None else None,
        "offset_us": choice.offset_us,
        "cycle_date": segment.cycle_date if segment else None,
        "segment_kind": segment.kind if segment else None,
        "segment_revision": segment.revision if segment else None,
        "original_asset_id": assets.get("original"),
        "original_class": "original" if original else None,
        "sha256": sha256,
        "bytes": original.bytes if original else None,
        "width": original.width if original else None,
        "height": original.height if original else None,
        "publication": publication,
        "publication_product": "still" if publication is not None else None,
        "retain_until": retain_until(slot),
    }
    still_id = cast(
        UUID,
        (
            await connection.execute(
                text("""
        INSERT INTO weather.archival_still(camera_id,slot_starts_at,local_date,revision,state,
          reason,policy,eligible,rejections,attempt_id,chunk,member,source_ts,capture_upper,
          offset_us,cycle_date,segment_kind,segment_revision,original_asset_id,original_class,
          sha256,bytes,width,height,publication_id,publication_product,retain_until)
        VALUES (:camera,:slot,:local_date,:revision,:state,:reason,:policy,:eligible,
          CAST(:rejections AS jsonb),:attempt_id,:chunk,:member,:source_ts,:capture_upper,
          :offset_us,:cycle_date,:segment_kind,:segment_revision,:original_asset_id,
          :original_class,:sha256,:bytes,:width,:height,:publication,:publication_product,
          :retain_until)
        RETURNING id
    """),
                values,
            )
        ).scalar_one(),
    )
    derivatives = sorted(role for role in assets if role.startswith("still"))
    if derivatives:
        await connection.execute(
            text("""
            INSERT INTO weather.still_derivative(archival_still_id,role,asset_id,recipe)
            SELECT :still,role,asset_id,:recipe
            FROM unnest(CAST(:roles AS text[]),CAST(:ids AS uuid[])) AS d(role,asset_id)
        """),
            {
                "still": still_id,
                "recipe": RECIPE,
                "roles": derivatives,
                "ids": [assets[role] for role in derivatives],
            },
        )
    return revision
