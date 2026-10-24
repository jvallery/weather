# SPDX-License-Identifier: MIT
"""Media bytes from Garage `weather-media` with the read-only key (#20).

Only the api uses this body; the key it reads is the bucket's reader, so even a wrong locator
could never reach `weather-originals`. Store errors become `Unavailable("store")` and carry no
URL, header or credential.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path

import httpx2

from weather.publish.objects import CREDENTIAL_ROOT, ObjectStore, StoreError

from .origin import CHUNK, STREAMS, Representation, Unavailable

BUCKET = "weather-media"
log = logging.getLogger("weather.media")


async def _guarded(chunks: AsyncIterator[bytes]) -> AsyncGenerator[bytes]:
    try:
        async for chunk in chunks:
            yield chunk
    except StoreError:
        raise Unavailable("store") from None


class GarageBody:
    def __init__(self, store: ObjectStore, *, chunk: int = CHUNK) -> None:
        self.store, self._chunk = store, chunk

    @asynccontextmanager
    async def open(
        self, representation: Representation, first: int, last: int
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        try:
            span = await self.store.open_span(
                representation.locator, first, last, representation.length
            )
        except StoreError:
            raise Unavailable("store") from None
        try:
            async with aclosing(_guarded(span.chunks(self._chunk))) as chunks:
                yield chunks
        finally:
            # Closing drops the upstream connection; shielded so a cancelled request still
            # releases it at once.
            await asyncio.shield(span.aclose())


def media_store() -> ObjectStore | None:
    """The reader for `weather-media`, or None while its optional Secret is absent."""
    directory = Path(os.environ.get("GARAGE_CREDENTIAL_DIRECTORY", CREDENTIAL_ROOT)) / BUCKET
    if not (directory / "access_key_id").is_file():
        log.warning("Garage media reader key is absent; media GET answers 503")
        return None
    client = httpx2.AsyncClient(
        timeout=httpx2.Timeout(30.0, connect=5.0),
        limits=httpx2.Limits(max_connections=STREAMS, max_keepalive_connections=8),
        follow_redirects=False,
    )
    try:
        return ObjectStore.from_environment(BUCKET, client)
    except StoreError, ValueError:
        log.warning("Garage media reader key is absent or invalid; media GET answers 503")
        return None
