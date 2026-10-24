# SPDX-License-Identifier: MIT
"""Explicit source-owned job handlers and outbox routing; unknown work fails closed."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.db.jobs import Job, LeaseLost, complete, fenced_transaction


@dataclass
class JobContext:
    engine: AsyncEngine
    job: Job
    committed: bool = False

    async def commit(
        self,
        write: Callable[[AsyncConnection], Awaitable[None]] | None = None,
        *,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        """Commit catalog changes and completion together, or roll both back.

        Prepare external immutable artifacts before this bounded transaction.
        Do not open independent transactions for handler catalog writes.
        """
        if self.committed:
            raise RuntimeError("Job was already committed")
        async with fenced_transaction(self.engine, self.job) as connection:
            if write is not None:
                await write(connection)
            if not await complete(connection, self.job, result):
                raise LeaseLost("Job expired before completion")
        self.committed = True


Handler = Callable[[JobContext], Awaitable[Mapping[str, Any] | None]]


class UnregisteredJob(ValueError):
    pass


class Registry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._topics: dict[str, str] = {}

    def register(
        self, kind: str, handler: Handler, *, topics: tuple[str, ...] = ()
    ) -> None:
        if kind in self._handlers or not 1 <= len(kind) <= 128:
            raise ValueError("Invalid or duplicate handler")
        if any(topic in self._topics for topic in topics):
            raise ValueError("Duplicate outbox route")
        self._handlers[kind] = handler
        for topic in topics:
            self._topics[topic] = kind

    def handler(self, kind: str) -> Handler:
        try:
            return self._handlers[kind]
        except KeyError:
            raise UnregisteredJob("Unregistered job type") from None

    def topic_kind(self, topic: str) -> str:
        try:
            return self._topics[topic]
        except KeyError:
            raise UnregisteredJob("Unregistered outbox topic") from None
