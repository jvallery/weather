# SPDX-License-Identifier: MIT
"""Request-time eligibility from the committed catalog (#20, P8).

Reads only `public_api.assets` and `public_api.videos` (migration 0003) as `weather_api` on the
primary, with the database clock, once per request; nothing is cached. The views admit only
public classes in `weather-media` that are available, not withdrawn and past their embargo, so
originals, embargoed, withdrawn and unknown IDs all resolve to None. The object key is rebuilt
from the content address the row carries, never taken from the request.

`/media/latest/{kind}` is the fixed probe target (#49): the newest public asset of one kind
whose source ended within a short window, chosen through the same views, so it inherits every
embargo and withdrawal rule and never reaches a camera. It carries its capture upper bound, so
the origin's breach detector checks it.
"""

import asyncio
import dataclasses
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .origin import Representation, Resolve, Route, Unavailable, named_route, uuid_route

PUBLIC_CLASSES = frozenset({"video", "poster", "keogram", "still", "sidecar"})
CAMERAS = frozenset({"skycam_east", "skycam_west", "frontyard_1"})
SEGMENTS = frozenset({"daytime", "overnight"})
EXTENSION = re.compile(r"[a-z0-9]{1,8}")
ASSET = """
    SELECT class,sha256,extension,bytes,content_type FROM public_api.assets
    WHERE id = CAST(:id AS uuid)
"""
# The view lists a withdrawn or incomplete video with no native asset, so the join drops it.
DOWNLOAD = """
    SELECT a.class,a.sha256,a.extension,a.bytes,a.content_type,v.camera_id,v.cycle_date,v.segment
    FROM public_api.videos v JOIN public_api.assets a ON a.id = v.native_asset_id
    WHERE v.id = CAST(:id AS uuid)
"""
# The route is public, so each source bounds the security-barrier view scan before LIMIT 1.
# `now()` and `current_date` (stable, not the volatile clock) let the bounds reach the base
# tables. Videos are a few rows a day per camera; stills are 24, so their window is short and
# `local_date` (lagging UTC by at most a day) reaches the partial index on active stills.
VIDEO_WINDOW = "interval '14 days'"
STILL_WINDOW = "interval '2 days'"
# kind -> (asset class, source subquery giving asset_id and the capture upper bound).
_VIDEO = """SELECT v.{column} AS asset_id,v.ends_at AS captured_upper FROM public_api.videos v
      WHERE v.ends_at > now() - {window} AND v.{column} IS NOT NULL
      ORDER BY v.ends_at DESC,v.id LIMIT 1"""
LATEST_SOURCES = {
    "video": ("video", _VIDEO.format(column="web_asset_id", window=VIDEO_WINDOW)),
    "poster": ("poster", _VIDEO.format(column="poster_asset_id", window=VIDEO_WINDOW)),
    "keogram": ("keogram", _VIDEO.format(column="keogram_asset_id", window=VIDEO_WINDOW)),
    "still": (
        "still",
        f"""SELECT s.asset_ids[1] AS asset_id,s.capture_upper AS captured_upper
      FROM public_api.stills s
      WHERE s.local_date >= current_date - 3 AND s.capture_upper > now() - {STILL_WINDOW}
        AND cardinality(s.asset_ids) > 0
      ORDER BY s.capture_upper DESC,s.camera_id LIMIT 1""",
    ),
}
LATEST = """
    SELECT clock_timestamp() AS now,a.class,a.sha256,a.extension,a.bytes,a.content_type,
      l.captured_upper
    FROM ({source}) l JOIN public_api.assets a ON a.id = l.asset_id
"""


def representation(row: dict[str, Any], filename: str | None = None) -> Representation | None:
    """A catalog row as a servable representation; anything malformed is not served."""
    try:
        if row["class"] not in PUBLIC_CLASSES or not EXTENSION.fullmatch(str(row["extension"])):
            return None
        return Representation(
            locator=f"{row['class']}/{row['sha256']}.{row['extension']}",
            length=int(row["bytes"]),
            sha256=str(row["sha256"]),
            content_type=str(row["content_type"]),
            filename=filename,
        )
    except KeyError, TypeError, ValueError:
        return None


def download_name(row: dict[str, Any]) -> str | None:
    camera, day, segment = row["camera_id"], row["cycle_date"], row["segment"]
    if camera not in CAMERAS or segment not in SEGMENTS or not isinstance(day, date):
        return None
    return f"weather-{camera}-{day.isoformat()}-{segment}-native.{row['extension']}"


class CatalogResolver:
    def __init__(self, engine: AsyncEngine, *, timeout_ms: int = 1500) -> None:
        self._engine, self._timeout_ms = engine, timeout_ms

    async def _row(self, sql: str, identity: UUID | None = None) -> dict[str, Any] | None:
        try:
            # One deadline covers the pool wait and the statement.
            async with (
                asyncio.timeout(self._timeout_ms / 1000 + 1),
                self._engine.connect() as connection,
                connection.begin(),
            ):
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(
                    text(f"SET LOCAL statement_timeout = {int(self._timeout_ms)}")
                )
                params = {"id": identity} if identity is not None else {}
                row = (await connection.execute(text(sql), params)).mappings().first()
                return dict(row) if row is not None else None
        except Exception:
            # Never retain or log driver errors, SQL or parameters.
            raise Unavailable("catalog") from None

    async def asset(self, identity: UUID) -> Representation | None:
        row = await self._row(ASSET, identity)
        return representation(row) if row is not None else None

    async def download(self, identity: UUID) -> Representation | None:
        row = await self._row(DOWNLOAD, identity)
        if row is None or row["class"] != "video" or (name := download_name(row)) is None:
            return None
        return representation(row, name)

    async def latest(self, kind: str) -> Representation | None:
        if kind not in LATEST_SOURCES:
            return None
        expected, source = LATEST_SOURCES[kind]
        row = await self._row(LATEST.format(source=source))
        return latest_representation(row, expected) if row is not None else None


def latest_representation(row: dict[str, Any], expected: str) -> Representation | None:
    """The probe target's row, carrying what the breach detector checks."""
    found = representation(row)
    captured, now = row.get("captured_upper"), row.get("now")
    if found is None or row["class"] != expected:
        return None
    if not isinstance(captured, datetime) or not isinstance(now, datetime):
        return None
    return dataclasses.replace(found, product=expected, captured_upper=captured, resolved_at=now)


def media_routes(
    asset: Resolve,
    download: Resolve,
    latest: Callable[[str], Awaitable[Representation | None]] | None = None,
) -> list[Route]:
    # The fixed `/media/latest/` space comes first: it would otherwise fall under `/media/`.
    fixed = [named_route("latest", "/media/latest/", LATEST_SOURCES, latest)] if latest else []
    return [
        *fixed,
        uuid_route("media", "/media/", "", asset),
        uuid_route("download", "/downloads/", "/native", download),
    ]
