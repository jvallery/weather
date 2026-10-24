# SPDX-License-Identifier: MIT
"""Bounded first-connect retry and migration timeouts; no database is started here."""

import asyncio
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from weather.db import connection
from weather.db.connection import DatabaseUnavailable, wait_for_database

ROOT = Path(__file__).resolve().parents[2]


class Engine:
    """Refuses `failures` connects (or hangs) before accepting one."""

    def __init__(self, failures: int, *, hang: bool = False) -> None:
        self.failures, self.hang, self.attempts = failures, hang, 0

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Any]:
        self.attempts += 1
        if self.attempts <= self.failures:
            if self.hang:
                await asyncio.sleep(3600)
            raise ConnectionRefusedError("refused")

        class Connection:
            async def execute(self, statement: Any) -> None:
                return None

        yield Connection()


def test_first_connect_retries_until_the_database_accepts() -> None:
    engine = Engine(3)
    asyncio.run(wait_for_database(engine, timeout=10))  # type: ignore[arg-type]
    assert engine.attempts == 4


def test_first_connect_fails_within_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection, "ATTEMPT_SECONDS", 0.2)
    for engine in (Engine(10**6), Engine(10**6, hang=True)):
        started = time.monotonic()
        with pytest.raises(DatabaseUnavailable):
            asyncio.run(wait_for_database(engine, timeout=0.6))  # type: ignore[arg-type]
        # A hung attempt is cut off too, so the whole wait stays near its budget.
        assert time.monotonic() - started < 1.5


def test_migration_sets_lock_and_statement_timeouts_in_its_transaction() -> None:
    sql = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    begin = sql.index("BEGIN;")
    assert begin < sql.index("SET LOCAL lock_timeout = '10s'") < sql.index("CREATE TABLE")
    assert begin < sql.index("SET LOCAL statement_timeout = '60s'") < sql.index("CREATE TABLE")
    assert sql.count("COMMIT;") == 1
