# SPDX-License-Identifier: MIT
"""The one search projection of a video's effective description (#25 documents).

The AI path (#23), the editor and the `editorial.changed` handler (#44) all call `project`
inside their own transaction. It reads `weather.video_editorial_state`, the single definition
of which text is public: an active human description (bound to the current web rendition and
evidence), else the current validated analysis, else nothing. Stale text is withdrawn in the
same transaction, before any reindex. Favorites, notes and private tags never reach here.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.ai import render
from weather.ai.effective import LABEL, STATION_LABEL
from weather.ai.validate import Observation
from weather.ai.windows import FPS, hour_windows
from weather.search import documents
from weather.search.documents import StaleRevision, VideoText, WindowText

LOG = logging.getLogger("weather.editorial")
CAMERA_LABELS = {
    "skycam_east": "East sky camera",
    "skycam_west": "West sky camera",
    "frontyard_1": "Front yard camera",
}
HUMAN_MODE = "human_edited"
HUMAN_QUALITY = "Human-edited description of the current rendition"
Outcome = Literal["replaced", "withdrawn", "unchanged"]

STATE_SQL = """SELECT s.video_id,s.video_revision,s.withdrawn,s.analysis_revision,s.automated,
  s.public_revision,s.human,s.human_active,v.camera_id,v.segment,v.starts_at,v.ends_at,
  v.frames,v.public_not_before,v.state AS video_state,v.publication_id,
  coalesce(a.revision,0) AS head_revision
FROM weather.video_editorial_state s JOIN weather.video v ON v.id=s.video_id
LEFT JOIN weather.analysis a ON a.video_id=s.video_id
WHERE s.video_id=:video"""
REVISION_SQL = """SELECT state,coverage,inputs,observations FROM weather.analysis_revision
WHERE video_id=:video AND revision=:revision"""
POSTER_SQL = """SELECT asset_id FROM weather.publication_asset
WHERE publication_id=:publication AND role='poster'"""


def _instant(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def video_text(
    row: Mapping[str, Any],
    *,
    description_revision: int,
    editorial_revision: int,
    poster: UUID | None,
    state: str = "complete",
    coverage: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    observations: Sequence[Mapping[str, Any]] = (),
    summary: str | None = None,
    human: str | None = None,
) -> VideoText:
    """The search document of one video. An active human description replaces the automated
    text and its per-hour windows, so no AI sentence it corrected stays searchable."""
    base: dict[str, Any] = {
        "video_id": row["video_id"],
        "camera_id": row["camera_id"],
        "camera_label": CAMERA_LABELS.get(row["camera_id"], row["camera_id"]),
        "segment": row["segment"],
        "video_start": row["starts_at"],
        "video_end": row["ends_at"],
        "public_not_before": row["public_not_before"],
        "media_revision": row["video_revision"],
        "description_revision": description_revision,
        "editorial_revision": editorial_revision,
        "media_seconds": row["frames"] / FPS,
        "poster_asset_id": poster,
    }
    if human is not None:
        return VideoText(
            **base,
            analysis_mode=HUMAN_MODE,
            observed_conditions=human,
            evidence_quality=HUMAN_QUALITY,
        )
    coverage = coverage or {}
    keogram = "with keogram" if (inputs or {}).get("keogram") else "frames only"
    label = LABEL if coverage.get("windows_reviewed") else STATION_LABEL
    quality = (
        f"{label}; {state}; {coverage.get('windows_reviewed', 0)} of "
        f"{coverage.get('windows_total', 0)} hour windows reviewed, {keogram}"
    )
    by_window: dict[datetime, list[Observation]] = {}
    for item in observations:
        by_window.setdefault(_instant(item["start"]), []).append(Observation.from_record(item))
    windows = []
    for window in hour_windows(row["starts_at"], row["ends_at"], row["frames"]):
        note = render.window_text(by_window.get(window.start, ()))
        if not window.available or note is None:
            continue
        windows.append(
            WindowText(
                window.start,
                window.end,
                note,
                "Sampled-frame visual observations",
                window.first_frame / FPS,
                window.end_frame / FPS,
            )
        )
    return VideoText(
        **base,
        analysis_mode="sampled_frames",
        observed_conditions=summary or "No supported description.",
        evidence_quality=quality,
        windows=tuple(windows),
    )


async def project(
    connection: AsyncConnection, video_id: UUID, *, profile_id: str, now: datetime
) -> Outcome:
    """Make the video's search documents match its effective description, or withdraw them."""
    found = (
        (await connection.execute(text(STATE_SQL), {"video": video_id})).mappings().one_or_none()
    )
    if found is None:
        return "unchanged"
    row = dict(found)
    human = row["human"] if row["human_active"] else None
    playable = not row["withdrawn"] and row["video_state"] in ("published", "partial")
    if not playable or (human is None and row["analysis_revision"] is None):
        return "withdrawn" if await documents.withdraw(connection, video_id) else "unchanged"
    poster = (
        await connection.execute(text(POSTER_SQL), {"publication": row["publication_id"]})
    ).scalar_one_or_none()
    common: dict[str, Any] = {
        "description_revision": int(row["head_revision"]),
        # The public revision: private edits (favorite, note, private tags) never move it.
        "editorial_revision": int(row["public_revision"] or 0),
        "poster": poster,
    }
    if human is not None:
        video = video_text(row, human=human, **common)
    else:
        stored = (
            (
                await connection.execute(
                    text(REVISION_SQL),
                    {"video": video_id, "revision": row["analysis_revision"]},
                )
            )
            .mappings()
            .one()
        )
        video = video_text(
            row,
            state=stored["state"],
            coverage=stored["coverage"],
            inputs=stored["inputs"],
            observations=stored["observations"],
            summary=row["automated"],
            **common,
        )
    try:
        await documents.replace(connection, video, profile_id=profile_id, now=now)
    except StaleRevision:
        LOG.info("Search document already newer; not replaced")
        return "unchanged"
    return "replaced"
