# SPDX-License-Identifier: MIT
"""Public event feed (#41): exact-snapshot keyset pages, provenance and video links.

Pages order by (source start, event id) over the first page's PostgreSQL MVCC snapshot: an event
appears with the newest of its revisions that snapshot saw, and only if that revision is public.
Inserts and revisions committed later wait for a new feed, so pages never repeat or drop events.
Withdrawal is immediate for every read that starts after it commits.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any, cast, get_args
from uuid import UUID

from .models import (
    Camera,
    Coverage,
    Event,
    EventInterval,
    EventQuery,
    EventSeek,
    EventVideo,
)

CAMERAS: tuple[Camera, ...] = get_args(Camera)
LAST_ID = UUID(int=(1 << 128) - 1)
FIRST = datetime.fromisoformat("0001-01-01T00:00:00+00:00")
LAST = datetime.fromisoformat("9999-12-31T00:00:00+00:00")


def _visible(alias: str) -> str:
    return f"pg_visible_in_snapshot(CAST(CAST({alias}.recorded_xid AS text) AS xid8), s.snap)"


def events_sql(order: str) -> str:
    """One statement: the snapshot (given, or this statement's own), filters and the keyset."""
    descending = order == "desc"
    compare, direction = ("<", "DESC") if descending else (">", "ASC")
    return f"""WITH s AS (
  SELECT CAST(coalesce(CAST(:snapshot AS text), CAST(pg_current_snapshot() AS text))
    AS pg_snapshot) AS snap
), page AS (
  SELECT r.* FROM public_api.event_revisions r, s
  WHERE {_visible("r")}
    AND NOT EXISTS (SELECT 1 FROM public_api.event_revision_markers m
                    WHERE m.id=r.id AND m.revision>r.revision AND {_visible("m")})
    AND (CAST(:event_id AS uuid) IS NULL OR r.id=CAST(:event_id AS uuid))
    AND (CAST(:origin AS text) IS NULL OR r.origin=CAST(:origin AS text))
    AND (CAST(:camera AS text) IS NULL OR r.camera IS NULL OR r.camera=CAST(:camera AS text))
    AND (CAST(:day AS date) IS NULL OR EXISTS (SELECT 1 FROM public_api.event_days d
         WHERE d.id=r.id AND d.revision=r.revision AND d.local_date=CAST(:day AS date)))
    AND (r.starts_at, r.id) {compare} (CAST(:after_start AS timestamptz),
                                       CAST(:after_id AS uuid))
  ORDER BY r.starts_at {direction}, r.id {direction} LIMIT :rows
)
SELECT page.*,CAST(s.snap AS text) AS snapshot,
  ARRAY(SELECT d.local_date FROM public_api.event_days d
        WHERE d.id=page.id AND d.revision=page.revision ORDER BY d.local_date) AS local_dates
FROM page, s ORDER BY page.starts_at {direction}, page.id {direction}"""


def parameters(
    query: EventQuery | None,
    *,
    snapshot: str | None,
    after: tuple[datetime, UUID] | None,
    rows: int,
    event_id: UUID | None = None,
) -> dict[str, Any]:
    descending = query is not None and query.order == "desc"
    start, last = after or ((LAST, LAST_ID) if descending else (FIRST, UUID(int=0)))
    return {
        "snapshot": snapshot,
        "event_id": event_id,
        "origin": query.origin if query else None,
        "camera": query.camera if query else None,
        "day": query.day if query else None,
        "after_start": start,
        "after_id": last,
        "rows": rows,
    }


# ---------------------------------------------------------------------------------------------
# Video links: UTC overlap with each camera's published segments, seeks through the time map.


@dataclass(frozen=True)
class MapEntry:
    """Frames [frame_start, frame_end) represent source time [start, end) evenly."""

    frame_start: int
    frame_end: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PublishedSegment:
    camera: Camera
    segment: str
    cycle_date: date
    video_id: str
    start: datetime
    end: datetime
    fps: Fraction
    time_map: tuple[MapEntry, ...]


def _frame(segment: PublishedSegment, instant: datetime) -> int | None:
    for entry in segment.time_map:
        if entry.start <= instant < entry.end and entry.frame_end > entry.frame_start:
            # Exact integer arithmetic: float division can round the last instant past the end.
            frames = entry.frame_end - entry.frame_start
            offset = (instant - entry.start) // timedelta(microseconds=1)
            span = (entry.end - entry.start) // timedelta(microseconds=1)
            return entry.frame_start + offset * frames // span
    return None


def video_links(
    start: datetime,
    end: datetime,
    camera: Camera | None,
    segments: Sequence[PublishedSegment],
) -> list[EventVideo]:
    """Station-wide events link every camera. A seek's source interval is the overlap, so it
    lies inside both the event and the video; no footage means an explicit unavailable link."""
    links: list[EventVideo] = []
    for name in (camera,) if camera else CAMERAS:
        found = False
        for segment in sorted(segments, key=lambda item: item.start):
            lo, hi = max(start, segment.start), min(end, segment.end)
            if segment.camera != name or lo >= hi:
                continue
            first = _frame(segment, lo)
            final = _frame(segment, hi - timedelta(microseconds=1))
            if first is None or final is None:
                continue
            found = True
            links.append(
                EventVideo(
                    camera=name,
                    state="available",
                    video_id=segment.video_id,
                    cycle_date=segment.cycle_date,
                    segment=cast(Any, segment.segment),
                    seek=EventSeek(
                        start_seconds=float(first / segment.fps),
                        end_seconds=float((final + 1) / segment.fps),
                        source_interval=EventInterval(start=lo, end=hi),
                    ),
                )
            )
        if not found:
            links.append(
                EventVideo(
                    camera=name,
                    state="unavailable",
                    video_id=None,
                    cycle_date=None,
                    segment=None,
                    seek=None,
                )
            )
    return links


# ---------------------------------------------------------------------------------------------


def _label(row: Mapping[str, Any]) -> str:
    if row["label"]:
        return str(row["label"])
    kind = row["kind"]
    if kind == "precipitation_onset":
        return "Precipitation onset"
    if kind == "daily_gust_max":
        return "Daily maximum gust"
    if kind == "lightning_strike":
        return "Lightning strike reported"
    return "Lightning reported in a report interval"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def build_event(row: Mapping[str, Any], segments: Sequence[PublishedSegment] = ()) -> Event:
    measurement = cast(dict[str, Any], row["measurement"] or {})
    coverage = measurement.get("coverage")
    strikes = measurement.get("strikes")
    return Event(
        id=str(row["id"]),
        revision=int(row["revision"]),
        kind=row["kind"],
        label=_label(row),
        origin=row["origin"],
        basis=row["basis"],
        human_edited=bool(row["human_edited"]),
        rule_revision=str(row["rule_revision"]),
        precision=row["time_precision"],
        source_interval=EventInterval(start=row["starts_at"], end=row["ends_at"]),
        camera=row["camera"],
        local_dates=list(row["local_dates"]),
        evidence_ids=[str(item) for item in row["evidence_ids"]],
        gust_mps=_number(measurement.get("gust_mps")),
        strikes=strikes if type(strikes) is int else None,
        distance_km=_number(measurement.get("distance_km")),
        coverage=Coverage.model_validate(coverage) if isinstance(coverage, dict) else None,
        videos=video_links(row["starts_at"], row["ends_at"], row["camera"], segments),
        updated_at=row["updated_at"],
    )
