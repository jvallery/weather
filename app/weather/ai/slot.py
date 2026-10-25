# SPDX-License-Identifier: MIT
"""The one Cortex call in flight across every `intel` process: a Postgres advisory lock.

The lock is session-scoped on a dedicated AUTOCOMMIT connection, so no transaction or snapshot
stays open while Cortex works. If the client gave up on a call (timeout), the server may still
be running it, so the slot is held for a short grace before it is released.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LOCK = "SELECT pg_try_advisory_lock(hashtextextended('weather.cortex', 23))"
UNLOCK = "SELECT pg_advisory_unlock(hashtextextended('weather.cortex', 23))"
POLL_SECONDS = 0.25
UNCERTAIN_GRACE_SECONDS = 30.0


class SlotBusy(RuntimeError):
    """Another process held the inference slot for the whole wait."""


class Uncertain:
    """Set by the holder when the server may still be working on an abandoned call."""

    def __init__(self) -> None:
        self.value = False


class CortexSlot:
    def __init__(self, engine: AsyncEngine, *, grace: float = UNCERTAIN_GRACE_SECONDS) -> None:
        self._engine, self._grace = engine, grace

    @asynccontextmanager
    async def hold(self, wait: float) -> AsyncIterator[Uncertain]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(wait, 0.0)
        async with self._engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="AUTOCOMMIT")
            while not (await connection.execute(text(LOCK))).scalar_one():
                if loop.time() >= deadline:
                    raise SlotBusy("Inference slot busy")
                await asyncio.sleep(POLL_SECONDS)
            uncertain = Uncertain()
            try:
                yield uncertain
            finally:
                if uncertain.value:
                    await asyncio.shield(asyncio.sleep(self._grace))
                await asyncio.shield(connection.execute(text(UNLOCK)))
