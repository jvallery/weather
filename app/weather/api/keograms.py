# SPDX-License-Identifier: MIT
"""`/api/v1/keograms`: a solar cycle's keograms, read only from `public_api.keograms`.

One request is one cycle date, optionally narrowed by camera and segment, so a response holds
at most six keograms (three cameras, two segments). Each carries its segment identity, the
exact UTC bin map (1,440 equal half-open bins), its gap runs, lineage digest, recipe and region
version, same-origin asset URLs, and a seek target per bin: the video ID plus the output time
of the frame whose source time contains the bin's midpoint, from the video's time map. Seek
targets are null unless the video is playable. Nothing shows before `ends_at + 630 s`, and no
asset or metadata before the keogram's embargo (enforced in the view).
"""

import re
from datetime import UTC, date, datetime
from fractions import Fraction
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Problem
from .registry import PublicError, unavailable
from .timelapses import CAMERAS, SEGMENTS, Asset, CatalogUnavailable, Interval, _asset

BINS = 1440
FPS = 60
PAGE = len(CAMERAS) * len(SEGMENTS)

router = APIRouter()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Rational(Model):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)


class BinMap(Model):
    """Bin i is [start + i * step_seconds, start + (i + 1) * step_seconds); column i of the
    image is bin i."""

    count: Literal[1440] = 1440
    start: datetime
    end: datetime
    step_seconds: Rational


class Seek(Model):
    """Per-bin seek targets into the video: output seconds of the mapped frame's midpoint."""

    video_id: UUID
    fps: Literal[60] = 60
    output_seconds: list[float] = Field(min_length=BINS, max_length=BINS)


class Keogram(Model):
    schema_version: Literal[1] = 1
    keogram_id: UUID
    camera: str
    cycle_date: date
    segment: Literal["daytime", "overnight"]
    state: Literal["pending", "published", "partial", "unavailable", "failed", "withdrawn"]
    reason: str | None
    revision: int | None
    updated_at: datetime | None
    source_interval: Interval
    bins: BinMap
    observed_bins: int | None = Field(ge=0, le=BINS)
    gaps: list[tuple[int, int]] = Field(max_length=BINS)
    lineage_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    recipe: str | None
    region_version: str | None
    image: Asset | None
    thumbnail: Asset | None
    sidecar: Asset | None
    video_id: UUID | None
    seek: Seek | None


class KeogramPage(Model):
    generated_at: datetime
    items: list[Keogram] = Field(max_length=PAGE)


COLUMNS = (
    "id,camera_id,cycle_date,segment,starts_at,ends_at,state,reason,revision,committed_at,"
    "image_asset_id,thumbnail_asset_id,sidecar_asset_id,observed,gaps,lineage_sha256,"
    "region_version,video_id,video_state,video_starts_at,video_ends_at,video_frames"
)


class Source(Protocol):
    async def keograms(
        self, cycle_date: date, camera: str | None, segment: str | None
    ) -> list[dict[str, Any]]: ...

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]: ...


class DatabaseSource:
    """Reads only `public_api.keograms` and `public_api.assets` with the API role."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def keograms(
        self, cycle_date: date, camera: str | None, segment: str | None
    ) -> list[dict[str, Any]]:
        clauses, params = ["cycle_date=:cycle_date"], {"cycle_date": cycle_date, "limit": PAGE}
        for column, value in (("camera_id", camera), ("segment", segment)):
            if value is not None:
                clauses.append(f"{column}=:{column}")
                params[column] = value
        sql = (
            f"SELECT {COLUMNS} FROM public_api.keograms WHERE {' AND '.join(clauses)} "
            "ORDER BY camera_id,segment LIMIT :limit"
        )
        try:
            async with self._engine.connect() as connection:
                return [dict(row._mapping) for row in await connection.execute(text(sql), params)]
        except Exception:
            raise CatalogUnavailable("Catalog unavailable") from None

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        if not ids:
            return {}
        try:
            async with self._engine.connect() as connection:
                rows = await connection.execute(
                    text("""
                    SELECT id,class,content_type,bytes,width,height FROM public_api.assets
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                """),
                    {"ids": ids},
                )
                return {row.id: dict(row._mapping) for row in rows}
        except Exception:
            raise CatalogUnavailable("Catalog unavailable") from None


def _micros(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def seek_targets(
    start: datetime, end: datetime, video_start: datetime, video_end: datetime, frames: int
) -> list[float] | None:
    """Output seconds of the frame whose source interval holds each bin's midpoint, at the
    frame's own midpoint (k + 1/2)/60, so a player lands on it. None unless every target lies
    inside the video's source interval."""
    a, length = _micros(start), _micros(end) - _micros(start)
    va, vlength = _micros(video_start), _micros(video_end) - _micros(video_start)
    if length <= 0 or vlength <= 0 or frames <= 0:
        return None
    targets = []
    for index in range(BINS):
        midpoint = a + Fraction((2 * index + 1) * length, 2 * BINS)
        if not va <= midpoint < va + vlength:
            return None
        frame = int((midpoint - va) * frames // vlength)
        targets.append(round((frame + 0.5) / FPS, 6))
    return targets


def build(row: dict[str, Any], assets: dict[UUID, dict[str, Any]]) -> Keogram:
    def pick(column: str) -> Asset | None:
        return _asset(assets.get(row[column])) if row[column] is not None else None

    image, thumbnail, sidecar = (
        pick("image_asset_id"),
        pick("thumbnail_asset_id"),
        pick("sidecar_asset_id"),
    )
    state, reason = row["state"], row["reason"]
    shown = state in {"published", "partial"}
    # A visible state needs its images; a race between the two reads degrades it.
    if shown and (image is None or thumbnail is None or sidecar is None):
        state, reason, shown = "unavailable", "media_unavailable", False
    step = Fraction(_micros(row["ends_at"]) - _micros(row["starts_at"]), BINS * 1_000_000)
    seek = None
    if row["video_id"] is not None and row["video_state"] in {"published", "partial"}:
        targets = seek_targets(
            row["starts_at"],
            row["ends_at"],
            row["video_starts_at"],
            row["video_ends_at"],
            row["video_frames"],
        )
        if targets is not None:
            seek = Seek(video_id=row["video_id"], output_seconds=targets)
    return Keogram(
        keogram_id=row["id"],
        camera=row["camera_id"],
        cycle_date=row["cycle_date"],
        segment=row["segment"],
        state=state,
        reason=reason,
        revision=row["revision"] if shown else None,
        updated_at=row["committed_at"] if shown else None,
        source_interval=Interval(start=row["starts_at"], end=row["ends_at"]),
        bins=BinMap(
            start=row["starts_at"],
            end=row["ends_at"],
            step_seconds=Rational(numerator=step.numerator, denominator=step.denominator),
        ),
        observed_bins=row["observed"] if shown else None,
        gaps=[(int(a), int(b)) for a, b in row["gaps"] or []] if shown else [],
        lineage_digest=row["lineage_sha256"] if shown else None,
        recipe="keogram/v1" if shown else None,
        region_version=row["region_version"] if shown else None,
        image=image if shown else None,
        thumbnail=thumbnail if shown else None,
        sidecar=sidecar if shown else None,
        video_id=row["video_id"],
        seek=seek,
    )


async def _hydrate(source: Source, rows: list[dict[str, Any]]) -> list[Keogram]:
    ids = [
        row[column]
        for row in rows
        for column in ("image_asset_id", "thumbnail_asset_id", "sidecar_asset_id")
        if row[column] is not None
    ]
    assets = await source.assets(ids)
    return [build(row, assets) for row in rows]


def _source(request: Request) -> Source:
    source: Source | None = getattr(request.app.state, "keograms", None)
    if source is None:
        raise unavailable()
    return source


def parse(params: dict[str, str]) -> tuple[date, str | None, str | None]:
    if set(params) - {"cycle_date", "camera", "segment"} or any(
        len(value) > 64 for value in params.values()
    ):
        raise PublicError("invalid_request", "Unknown or oversized keogram filter.")
    camera, segment = params.get("camera"), params.get("segment")
    if camera is not None and camera not in CAMERAS:
        raise PublicError("invalid_request", "Unknown camera.")
    if segment is not None and segment not in SEGMENTS:
        raise PublicError("invalid_request", "Unknown segment.")
    raw = params.get("cycle_date", "")
    try:
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", raw):
            raise ValueError
        cycle_date = date.fromisoformat(raw)
    except ValueError:
        raise PublicError("invalid_request", "A cycle_date (YYYY-MM-DD) is required.") from None
    return cycle_date, camera, segment


@router.get(
    "/api/v1/keograms",
    response_model=KeogramPage,
    responses={
        400: {"model": Problem, "description": "Invalid request."},
        503: {"model": Problem, "description": "The catalog is temporarily unavailable."},
    },
    operation_id="listKeograms",
)
async def list_keograms(
    request: Request,
    response: Response,
    cycle_date: str = Query(..., description="Solar cycle date, YYYY-MM-DD"),
    camera: str | None = None,
    segment: str | None = None,
) -> KeogramPage:
    day, camera, segment = parse(dict(request.query_params))
    source = _source(request)
    try:
        items = await _hydrate(source, await source.keograms(day, camera, segment))
    except CatalogUnavailable:
        raise unavailable() from None
    order = {(c, s): n for n, (c, s) in enumerate((c, s) for c in CAMERAS for s in SEGMENTS)}
    items.sort(key=lambda item: order[(item.camera, item.segment)])
    response.headers["Cache-Control"] = "no-store"
    return KeogramPage(generated_at=datetime.now(UTC), items=items)
