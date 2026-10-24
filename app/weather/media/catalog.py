# SPDX-License-Identifier: MIT
"""Request-time eligibility from the committed catalog (#20, P8).

Reads only `public_api.assets` and `public_api.videos` (migration 0003) as `weather_api` on the
primary, with the database clock, once per request; nothing is cached. The views admit only
public classes in `weather-media` that are available, not withdrawn and past their embargo, so
originals, embargoed, withdrawn and unknown IDs all resolve to None. The object key is rebuilt
from the content address the row carries, never taken from the request.
"""

import asyncio
import re
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .origin import Representation, Resolve, Route, Unavailable, uuid_route

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

    async def _row(self, sql: str, identity: UUID) -> dict[str, Any] | None:
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
                row = (await connection.execute(text(sql), {"id": identity})).mappings().first()
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


def media_routes(asset: Resolve, download: Resolve) -> list[Route]:
    return [
        uuid_route("media", "/media/", "", asset),
        uuid_route("download", "/downloads/", "/native", download),
    ]
