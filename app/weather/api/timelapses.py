# SPDX-License-Identifier: MIT
"""`/api/v1/timelapses`: the public video catalog, read only from the `public_api` views.

At most 100 entries per page, newest first, filtered by camera, segment, state and either a
cycle date, a month or a date range. The next-page cursor is signed with CURSOR_HMAC_KEY and
bound to the filters; a tampered or re-filtered cursor is rejected. Responses carry public
asset IDs and same-origin URLs only, never a bucket, object key or hash.
"""

import calendar
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.ai.effective import (
    EDITOR_LABEL,
    LABEL,
    STATION_LABEL,
    effective_description,
    public_author,
    public_model,
)

from .cursors import invalid_cursor, seal, unseal
from .models import Problem
from .registry import PublicError, unavailable

PAGE = 100
MAX_RANGE_DAYS = 366
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
SEGMENTS = ("daytime", "overnight")
# The web archive offers lifecycle states the catalog may never hold; they match nothing.
STATES = (
    "capturing",
    "queued",
    "processing",
    "published",
    "partial",
    "unavailable",
    "expired",
    "withdrawn",
)
MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")

router = APIRouter()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Asset(Model):
    asset_id: UUID
    asset_class: Literal["video", "poster", "keogram", "sidecar"] = Field(alias="class")
    url: str
    mime: str
    bytes: int
    width: int | None
    height: int | None


class Rendition(Model):
    role: Literal["native", "web", "mobile"]
    asset: Asset


class Interval(Model):
    start: datetime
    end: datetime


Label = Literal[
    "AI-generated · sampled-frame analysis",
    "Station measurements only",
    "Editor-written description",
]


class AnalysisClaim(Model):
    text: str = Field(min_length=1, max_length=240)
    basis: Literal["visual", "instrument"]


class AnalysisCoverage(Model):
    """Reviewed source time: hour windows whose sampled frames passed validation."""

    algorithm_revision: str
    reviewed_seconds: float = Field(ge=0)
    expected_seconds: float = Field(ge=0)
    fraction: float = Field(ge=0, le=1)
    windows_reviewed: int = Field(ge=0)
    windows_total: int = Field(ge=0)
    gaps: list[Interval] = Field(max_length=48)


class AnalysisProvenance(Model):
    model_route: str
    model: str
    prompt_revision: str
    schema_revision: str
    refresh: Literal["full", "measurements"]
    evidence_count: int = Field(ge=0)
    frames_sampled: int = Field(ge=0)
    keogram_used: bool


class Analysis(Model):
    """The labelled description of the current media; never model-written text (C18).

    `author` is `ai` for template text rendered from the model's validated observations,
    `station` when no hour passed visual review (the text is station data only, with no AI
    label or model provenance), and `editor` for a human (#44) revision; the label says which."""

    label: Label = LABEL
    author: Literal["ai", "station", "editor"] = "ai"
    state: Literal["pending", "complete", "degraded", "failed", "unavailable"]
    reason: str | None
    stale: bool
    revision: int | None
    generated_at: datetime | None
    description: str | None = Field(max_length=8000)
    claims: list[AnalysisClaim] = Field(max_length=16)
    limitations: list[str] = Field(max_length=8)
    coverage: AnalysisCoverage | None
    provenance: AnalysisProvenance | None


class Timelapse(Model):
    schema_version: Literal[1] = 1
    video_id: UUID
    camera: str
    cycle_date: date
    segment: Literal["daytime", "overnight"]
    state: Literal["published", "partial", "unavailable", "withdrawn"]
    reason: str | None
    withdrawn: bool
    revision: int
    updated_at: datetime
    source_interval: Interval
    allocated_frames: int
    renditions: list[Rendition]
    poster: Asset | None
    sidecar: Asset | None
    native_download_url: str | None
    keogram_state: Literal["pending", "published", "partial", "unavailable", "withdrawn"]
    keogram: Asset | None
    analysis: Analysis


class TimelapsePage(Model):
    generated_at: datetime
    items: list[Timelapse] = Field(max_length=PAGE)
    next_cursor: str | None


class CatalogUnavailable(RuntimeError):
    pass


def invalid(code: str, message: str) -> PublicError:
    return PublicError(code, message)


@dataclass(frozen=True)
class CatalogQuery:
    camera: str | None = None
    segment: str | None = None
    state: str | None = None
    first: date | None = None
    last: date | None = None
    limit: int = PAGE

    def digest(self) -> str:
        values = [self.camera, self.segment, self.state, self.first, self.last, self.limit]
        return hashlib.sha256(json.dumps([str(v) for v in values]).encode()).hexdigest()


class TimelapseCursor:
    """The position (starts_at, id), sealed with CURSOR_HMAC_KEY and bound to the filters."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("Cursor signing key is too short")
        self._key = key

    def encode(self, query: CatalogQuery, starts_at: datetime, video_id: UUID) -> str:
        return seal(
            self._key, {"f": query.digest(), "t": starts_at.isoformat(), "i": str(video_id)}
        )

    def decode(self, token: str, query: CatalogQuery) -> tuple[datetime, UUID]:
        data = unseal(self._key, token)
        try:
            if data.get("f") != query.digest():
                raise ValueError("filters")
            position = datetime.fromisoformat(str(data["t"]))
            if position.tzinfo is None:
                raise ValueError("time")
            return position, UUID(str(data["i"]))
        except ValueError, KeyError:
            raise invalid_cursor() from None


def _date(value: str, name: str) -> date:
    try:
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise invalid("invalid_request", f"Invalid {name}.") from None


def parse_query(params: dict[str, str]) -> tuple[CatalogQuery, str | None]:
    allowed = {"camera", "segment", "state", "cycle_date", "month", "from", "to", "limit", "cursor"}
    if set(params) - allowed or any(len(value) > 2048 for value in params.values()):
        raise invalid("invalid_request", "Unknown or oversized catalog filter.")
    camera, segment, state = params.get("camera"), params.get("segment"), params.get("state")
    if camera is not None and camera not in CAMERAS:
        raise invalid("invalid_request", "Unknown camera.")
    if segment is not None and segment not in SEGMENTS:
        raise invalid("invalid_request", "Unknown segment.")
    if state is not None and state not in STATES:
        raise invalid("invalid_request", "Unknown state.")
    ranges = [name for name in ("cycle_date", "month") if name in params]
    if "from" in params or "to" in params:
        ranges.append("range")
    if len(ranges) > 1:
        raise invalid("invalid_request", "Use one of cycle_date, month or from/to.")
    first = last = None
    if "cycle_date" in params:
        first = last = _date(params["cycle_date"], "cycle_date")
    elif "month" in params:
        match = MONTH.fullmatch(params["month"])
        if not match:
            raise invalid("invalid_request", "Invalid month; use YYYY-MM.")
        year, month = int(match[1]), int(match[2])
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
    elif ranges:
        if "from" not in params or "to" not in params:
            raise invalid("invalid_request", "A date range needs both from and to.")
        first, last = _date(params["from"], "from"), _date(params["to"], "to")
        if last < first or (last - first).days >= MAX_RANGE_DAYS:
            raise invalid("range_too_large", "Date ranges cover 1 to 366 days.")
    try:
        limit = int(params.get("limit", PAGE))
    except ValueError:
        raise invalid("invalid_request", "Invalid limit.") from None
    if not 1 <= limit <= PAGE:
        raise invalid("invalid_request", "Limit is 1 to 100.")
    query = CatalogQuery(camera, segment, state, first, last, limit)
    return query, params.get("cursor")


class Source(Protocol):
    async def videos(
        self, query: CatalogQuery, after: tuple[datetime, UUID] | None, video_id: UUID | None
    ) -> list[dict[str, Any]]: ...

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]: ...

    async def analyses(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]: ...


VIDEO_COLUMNS = (
    "id,camera_id,cycle_date,segment,starts_at,ends_at,frames,revision,committed_at,withdrawn,"
    "state,reason,native_asset_id,web_asset_id,mobile_asset_id,poster_asset_id,"
    "sidecar_asset_id,keogram_asset_id,keogram_state"
)


class DatabaseSource:
    """Reads only `public_api.videos` and `public_api.assets` with the API role."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def videos(
        self, query: CatalogQuery, after: tuple[datetime, UUID] | None, video_id: UUID | None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": query.limit + 1}
        filters: dict[str, Any] = {
            "id": video_id,
            "camera_id": query.camera,
            "segment": query.segment,
            "state": query.state,
        }
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column}=:{column}")
                params[column] = value
        if query.first is not None:
            clauses.append("cycle_date BETWEEN :first AND :last")
            params.update(first=query.first, last=query.last)
        if after is not None:
            clauses.append(
                "(starts_at,id) < (CAST(:after_t AS timestamptz),CAST(:after_id AS uuid))"
            )
            params.update(after_t=after[0], after_id=after[1])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            f"SELECT {VIDEO_COLUMNS} FROM public_api.videos{where} "
            "ORDER BY starts_at DESC,id DESC LIMIT :limit"
        )
        try:
            async with self._engine.connect() as connection:
                rows = await connection.execute(text(sql), params)
                return [dict(row._mapping) for row in rows]
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

    async def analyses(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        if not ids:
            return {}
        try:
            async with self._engine.connect() as connection:
                rows = await connection.execute(
                    text(f"""
                    SELECT {ANALYSIS_COLUMNS} FROM public_api.video_analyses
                    WHERE video_id = ANY(CAST(:ids AS uuid[]))
                """),
                    {"ids": ids},
                )
                return {row.video_id: dict(row._mapping) for row in rows}
        except Exception:
            raise CatalogUnavailable("Catalog unavailable") from None


LABELS: dict[str, Label] = {"ai": LABEL, "station": STATION_LABEL, "editor": EDITOR_LABEL}
ANALYSIS_COLUMNS = (
    "video_id,state,reason,stale,updated_at,revision,revision_state,refresh,model_route,model,"
    "prompt_sha256,schema_sha256,evidence_count,summary,claims,limitations,coverage,inputs,"
    "author_kind,generated_at"
)


def _no_analysis(state: Literal["pending", "unavailable"], reason: str | None) -> Analysis:
    return Analysis(
        state=state,
        reason=reason,
        stale=False,
        revision=None,
        generated_at=None,
        description=None,
        claims=[],
        limitations=[],
        coverage=None,
        provenance=None,
    )


def build_analysis(row: dict[str, Any] | None, playable: bool) -> Analysis:
    """Pending until intel reports; unavailable without playable media. Content appears only
    with a validated revision of the current media; the state is the head's own."""
    if not playable:
        return _no_analysis("unavailable", "media_unavailable")
    if row is None:
        return _no_analysis("pending", None)
    content = row["revision_state"] in ("complete", "degraded")
    state = row["state"]
    if state in ("complete", "degraded") and not content:
        state = "pending"
    coverage = row["coverage"] if content else None
    inputs = row["inputs"] if content else {}
    author: Literal["ai", "station", "editor"] = (
        public_author(row.get("author_kind", "model")) if content else "ai"
    )
    if author == "ai" and coverage is not None and not coverage.get("windows_reviewed"):
        author = "station"
    return Analysis(
        label=LABELS[author],
        author=author,
        state=state,
        # Fixed identifiers from the worker's own vocabulary, never provider text.
        reason=row["reason"],
        stale=bool(row["stale"]) and content,
        revision=row["revision"] if content else None,
        generated_at=row["generated_at"] if content else None,
        description=effective_description(row),
        claims=[
            AnalysisClaim(text=claim["text"], basis=claim["basis"])
            for claim in (row["claims"] if content else [])
            # Fail closed: a claim of any other basis is never served.
            if claim.get("basis") in ("visual", "instrument")
        ],
        limitations=list(row["limitations"]) if content else [],
        coverage=AnalysisCoverage.model_validate(
            {
                **{k: coverage[k] for k in AnalysisCoverage.model_fields if k != "gaps"},
                "gaps": coverage["gaps"][:48],
            }
        )
        if coverage
        else None,
        provenance=AnalysisProvenance(
            model_route=row["model_route"],
            model=public_model(row["model"], row["model_route"]),
            prompt_revision=row["prompt_sha256"][:16],
            schema_revision=row["schema_sha256"][:16],
            refresh=row["refresh"],
            evidence_count=row["evidence_count"],
            frames_sampled=int(inputs.get("frames", 0)),
            keogram_used=bool(inputs.get("keogram", False)),
        )
        if content and author == "ai"
        else None,
    )


def _asset(row: dict[str, Any] | None) -> Asset | None:
    if row is None:
        return None
    return Asset.model_validate(
        {
            "asset_id": row["id"],
            "class": row["class"],
            "url": f"/media/{row['id']}",
            "mime": row["content_type"],
            "bytes": row["bytes"],
            "width": row["width"],
            "height": row["height"],
        }
    )


def build(
    row: dict[str, Any],
    assets: dict[UUID, dict[str, Any]],
    analysis: dict[str, Any] | None = None,
) -> Timelapse:
    def pick(column: str) -> Asset | None:
        return _asset(assets.get(row[column])) if row[column] is not None else None

    renditions = [
        Rendition(role=role, asset=asset)  # type: ignore[arg-type]
        for role in ("native", "web", "mobile")
        if (asset := pick(f"{role}_asset_id")) is not None
    ]
    poster = pick("poster_asset_id")
    state, reason = row["state"], row["reason"]
    # The view already degrades incomplete media; this covers a race between the two reads.
    playable = state in {"published", "partial"} and len(renditions) == 3 and poster is not None
    if state in {"published", "partial"} and not playable:
        state, reason = "unavailable", "media_unavailable"
    keogram = pick("keogram_asset_id")
    return Timelapse(
        video_id=row["id"],
        camera=row["camera_id"],
        cycle_date=row["cycle_date"],
        segment=row["segment"],
        state=state,
        reason=reason,
        withdrawn=row["withdrawn"],
        revision=row["revision"],
        updated_at=row["committed_at"],
        source_interval=Interval(start=row["starts_at"], end=row["ends_at"]),
        allocated_frames=row["frames"],
        renditions=renditions if playable else [],
        poster=poster if playable else None,
        sidecar=pick("sidecar_asset_id"),
        native_download_url=f"/downloads/{row['id']}/native" if playable else None,
        # A visible keogram state needs its image; a race between the two reads degrades it.
        keogram_state="unavailable"
        if row["keogram_state"] in {"published", "partial"} and keogram is None
        else row["keogram_state"],
        keogram=keogram,
        analysis=build_analysis(analysis, playable),
    )


async def _hydrate(source: Source, rows: list[dict[str, Any]]) -> list[Timelapse]:
    ids = [
        row[column]
        for row in rows
        for column in (
            "native_asset_id",
            "web_asset_id",
            "mobile_asset_id",
            "poster_asset_id",
            "sidecar_asset_id",
            "keogram_asset_id",
        )
        if row[column] is not None
    ]
    assets = await source.assets(ids)
    analyses = await source.analyses([row["id"] for row in rows])
    return [build(row, assets, analyses.get(row["id"])) for row in rows]


class Catalog:
    """The API role's catalog source and cursor; the app factory binds both (#14)."""

    def __init__(self, source: Source | None = None, cursor: TimelapseCursor | None = None) -> None:
        self.source, self.cursor = source, cursor

    def ready(self) -> tuple[Source, TimelapseCursor]:
        if self.source is None or self.cursor is None:
            raise unavailable()
        return self.source, self.cursor


def _catalog(request: Request) -> Catalog:
    catalog: Catalog | None = getattr(request.app.state, "timelapses", None)
    return catalog or Catalog()


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
        content=Problem(code="not_found", message="No such timelapse.").model_dump(mode="json"),
    )


ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Invalid request."},
    503: {"model": Problem, "description": "The catalog is temporarily unavailable."},
}


@router.get(
    "/api/v1/timelapses",
    response_model=TimelapsePage,
    response_model_by_alias=True,
    responses=ERRORS,
    operation_id="listTimelapses",
)
async def list_timelapses(
    request: Request,
    response: Response,
    camera: str | None = None,
    segment: str | None = None,
    state: str | None = None,
    cycle_date: str | None = None,
    month: str | None = Query(None, description="YYYY-MM"),
    start: str | None = Query(None, alias="from", description="First cycle date, inclusive"),
    end: str | None = Query(None, alias="to", description="Last cycle date, inclusive"),
    limit: int = Query(PAGE, ge=1, le=PAGE),
    cursor: str | None = Query(None, max_length=2048),
) -> TimelapsePage:
    query, token = parse_query(dict(request.query_params))
    source, codec = _catalog(request).ready()
    after = codec.decode(token, query) if token else None
    try:
        rows = await source.videos(query, after, None)
        page, more = rows[: query.limit], len(rows) > query.limit
        items = await _hydrate(source, page)
    except CatalogUnavailable:
        raise unavailable() from None
    last = page[-1] if page else None
    response.headers["Cache-Control"] = "no-store"
    return TimelapsePage(
        generated_at=datetime.now(UTC),
        items=items,
        next_cursor=codec.encode(query, last["starts_at"], last["id"]) if more and last else None,
    )


@router.get(
    "/api/v1/timelapses/{video_id}",
    response_model=Timelapse,
    response_model_by_alias=True,
    responses={**ERRORS, 404: {"model": Problem, "description": "No such timelapse."}},
    operation_id="getTimelapse",
)
async def get_timelapse(request: Request, response: Response, video_id: str) -> Any:
    try:
        identity = UUID(video_id)
    except ValueError:
        return _not_found()
    source, _ = _catalog(request).ready()
    try:
        items = await _hydrate(
            source, (await source.videos(CatalogQuery(limit=1), None, identity))[:1]
        )
    except CatalogUnavailable:
        raise unavailable() from None
    if not items:
        return _not_found()
    response.headers["Cache-Control"] = "no-store"
    return items[0]
