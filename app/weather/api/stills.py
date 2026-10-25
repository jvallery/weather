# SPDX-License-Identifier: MIT
"""`/api/v1/stills`: hourly comparison stills, read only from `public_api.stills`/`assets`.

One calendar day (every UTC hour slot of it: 23, 24 or 25), optionally one local clock hour of
it, or one local clock hour of one camera across up to 31 days. Slots come from the pinned time
model, so a slot without a visible selection still appears: `pending` while the pipeline may
still select it, `missing` (`not_selected`) after that. Each item carries the calendar date
(not the solar cycle date, which is separate), stable slot and day IDs, the offset-bearing
local label, the UTC slot and source intervals, the offset from :30, the policy, the state and
same-origin image URLs. Never a bucket, object key, hash, original or capture location.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.timemodel import HourSlot, hour_slots

from .models import Problem
from .registry import PublicError, unavailable

CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
MAX_RANGE_DAYS = 31
MAX_ITEMS = 100
# Matches the pipeline's hourly lookback: an unselected slot older than this stays missing.
SELECTION_WINDOW = timedelta(hours=12)
DATE = re.compile(r"\d{4}-\d\d-\d\d")
HOUR = re.compile(r"\d{1,2}")
COLUMNS = (
    "camera_id,slot_starts_at,local_date,revision,policy,state,reason,source_ts,capture_upper,"
    "offset_us,cycle_date,segment_kind,asset_ids"
)

router = APIRouter()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Interval(Model):
    start: datetime
    end: datetime


class StillImage(Model):
    asset_id: UUID
    url: str
    mime: Literal["image/jpeg", "image/webp"]
    bytes: int
    width: int
    height: int


class Still(Model):
    schema_version: Literal[1] = 1
    camera: str
    slot_id: str = Field(description="The slot's UTC start, e.g. 2026-11-01T07:00:00Z")
    day_id: date = Field(description="The calendar day the slot belongs to")
    calendar_date: date
    cycle_date: date | None = Field(description="The winner's solar cycle; not the calendar day")
    segment: Literal["daytime", "overnight"] | None
    local_label: str = Field(description="Local slot start with its UTC offset")
    utc_offset_minutes: int
    fold: int = Field(description="1 for the repeated hour after the fall change")
    slot_interval: Interval
    state: Literal["on_target", "off_target", "missing", "pending", "withdrawn"]
    reason: str | None
    policy: str | None
    revision: int | None
    source_interval: Interval | None = Field(description="Source time to capture upper bound")
    offset_seconds: float | None = Field(description="Source time minus slot start + 30 min")
    images: list[StillImage] = Field(max_length=6)


class StillPage(Model):
    generated_at: datetime
    items: list[Still] = Field(max_length=MAX_ITEMS)


class CatalogUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StillQuery:
    first: date
    last: date
    camera: str | None = None
    hour: int | None = None


def invalid(message: str) -> PublicError:
    return PublicError("invalid_request", message)


def _date(value: str, name: str) -> date:
    try:
        if not DATE.fullmatch(value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise invalid(f"Invalid {name}.") from None


def parse_query(params: dict[str, str]) -> StillQuery:
    if set(params) - {"camera", "date", "from", "to", "hour"} or any(
        len(value) > 64 for value in params.values()
    ):
        raise invalid("Unknown or oversized stills filter.")
    camera = params.get("camera")
    if camera is not None and camera not in CAMERAS:
        raise invalid("Unknown camera.")
    hour = None
    if "hour" in params:
        if not HOUR.fullmatch(params["hour"]) or not 0 <= int(params["hour"]) <= 23:
            raise invalid("Hour is a local clock hour, 0 to 23.")
        hour = int(params["hour"])
    ranged = "from" in params or "to" in params
    if ("date" in params) == ranged:
        raise invalid("Use either date or from/to.")
    if not ranged:
        day = _date(params["date"], "date")
        return StillQuery(day, day, camera, hour)
    if "from" not in params or "to" not in params:
        raise invalid("A date range needs both from and to.")
    if camera is None or hour is None:
        raise invalid("A date range compares one camera's local clock hour.")
    first, last = _date(params["from"], "from"), _date(params["to"], "to")
    if last < first or (last - first).days >= MAX_RANGE_DAYS:
        raise PublicError("range_too_large", "Date ranges cover 1 to 31 days.")
    return StillQuery(first, last, camera, hour)


class Source(Protocol):
    async def stills(self, query: StillQuery) -> list[dict[str, Any]]: ...

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]: ...


class DatabaseSource:
    """Reads only `public_api.stills` and `public_api.assets` with the API role."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def stills(self, query: StillQuery) -> list[dict[str, Any]]:
        clauses = ["local_date BETWEEN :first AND :last"]
        params: dict[str, Any] = {"first": query.first, "last": query.last}
        if query.camera is not None:
            clauses.append("camera_id=:camera")
            params["camera"] = query.camera
        if query.hour is not None:
            # The local clock hour, from the slot's own whole-hour offset; no tz database.
            clauses.append(
                "extract(hour FROM (slot_starts_at AT TIME ZONE 'UTC')"
                " + make_interval(mins => utc_offset_minutes)) = :hour"
            )
            params["hour"] = query.hour
        sql = (
            f"SELECT {COLUMNS} FROM public_api.stills WHERE {' AND '.join(clauses)} "
            "ORDER BY slot_starts_at,camera_id LIMIT 2400"
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
                    WHERE id = ANY(CAST(:ids AS uuid[])) AND class='still'
                """),
                    {"ids": ids},
                )
                return {row.id: dict(row._mapping) for row in rows}
        except Exception:
            raise CatalogUnavailable("Catalog unavailable") from None


def slots(query: StillQuery) -> list[HourSlot]:
    result: list[HourSlot] = []
    day = query.first
    while day <= query.last:
        result.extend(
            slot for slot in hour_slots(day) if query.hour is None or slot.local_hour == query.hour
        )
        day += timedelta(days=1)
    return result


def _utc(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(
    slot: HourSlot,
    camera: str,
    row: dict[str, Any] | None,
    assets: dict[UUID, dict[str, Any]],
    now: datetime,
) -> Still:
    common: dict[str, Any] = {
        "camera": camera,
        "slot_id": _utc(slot.start),
        "day_id": slot.weather_day.local_date,
        "calendar_date": slot.weather_day.local_date,
        "local_label": slot.local_label,
        "utc_offset_minutes": slot.utc_offset_minutes,
        "fold": slot.fold,
        "slot_interval": Interval(start=slot.start, end=slot.end),
    }
    if row is None:
        late = slot.end + SELECTION_WINDOW <= now
        return Still(
            **common,
            cycle_date=None,
            segment=None,
            state="missing" if late else "pending",
            reason="not_selected" if late else None,
            policy=None,
            revision=None,
            source_interval=None,
            offset_seconds=None,
            images=[],
        )
    images = sorted(
        (
            StillImage(
                asset_id=asset["id"],
                url=f"/media/{asset['id']}",
                mime=asset["content_type"],
                bytes=asset["bytes"],
                width=asset["width"],
                height=asset["height"],
            )
            for asset_id in row["asset_ids"] or []
            if (asset := assets.get(asset_id)) is not None
        ),
        key=lambda image: (-image.width, image.mime),
    )
    source = (
        Interval(start=row["source_ts"], end=row["capture_upper"])
        if row["source_ts"] is not None
        else None
    )
    return Still(
        **common,
        cycle_date=row["cycle_date"],
        segment=row["segment_kind"],
        state=row["state"],
        reason=row["reason"],
        policy=row["policy"],
        revision=row["revision"],
        source_interval=source,
        offset_seconds=row["offset_us"] / 1e6 if row["offset_us"] is not None else None,
        images=images,
    )


async def assemble(source: Source, query: StillQuery, now: datetime) -> list[Still]:
    rows = await source.stills(query)
    by_key = {(row["camera_id"], row["slot_starts_at"]): row for row in rows}
    ids = [asset for row in rows for asset in row["asset_ids"] or []]
    assets = await source.assets(ids)
    cameras = [query.camera] if query.camera else list(CAMERAS)
    return [
        build(slot, camera, by_key.get((camera, slot.start)), assets, now)
        for slot in slots(query)
        for camera in cameras
    ]


def _source(request: Request) -> Source:
    source: Source | None = getattr(request.app.state, "stills", None)
    if source is None:
        raise unavailable()
    return source


@router.get(
    "/api/v1/stills",
    response_model=StillPage,
    responses={
        400: {"model": Problem, "description": "Invalid request."},
        503: {"model": Problem, "description": "The catalog is temporarily unavailable."},
    },
    operation_id="listStills",
)
async def list_stills(
    request: Request,
    response: Response,
    camera: str | None = None,
    day: str | None = Query(None, alias="date", description="Calendar date (America/Denver)"),
    start: str | None = Query(None, alias="from", description="First calendar date"),
    end: str | None = Query(None, alias="to", description="Last calendar date, inclusive"),
    hour: str | None = Query(None, description="Local clock hour, 0 to 23"),
) -> StillPage:
    query = parse_query(dict(request.query_params))
    source = _source(request)
    try:
        items = await assemble(source, query, datetime.now(UTC))
    except CatalogUnavailable:
        raise unavailable() from None
    response.headers["Cache-Control"] = "no-store"
    return StillPage(generated_at=datetime.now(UTC), items=items)
