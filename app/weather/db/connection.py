# SPDX-License-Identifier: MIT
"""Create TLS database connections without logging a credential-bearing URL."""

import asyncio
import os
import ssl
import time
from pathlib import Path

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# kube-router programs a new pod's NetworkPolicy a few seconds after start, so the first
# connection is refused briefly; every DB client waits this long before failing.
FIRST_CONNECT_SECONDS = 60
# A dropped SYN or stalled DNS must not hold one attempt past the overall budget.
ATTEMPT_SECONDS = 5


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def create_database_engine(
    *,
    pool_size: int = 5,
    pool_timeout: float = 30,
    server_settings: dict[str, str] | None = None,
    directory: Path | None = None,
) -> AsyncEngine:
    """TLS verify-full engine for the mounted role; the API passes a smaller read-only budget.

    The API's operator listener passes the separately mounted weather_editor directory (#44).
    """
    settings = {"timezone": "UTC", **(server_settings or {})}
    # Only the cluster integration fixture may use its isolated test endpoint.
    test_url = os.environ.get("WEATHER_CI_DATABASE_URL")
    if test_url:
        if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
            raise DatabaseConfigurationError("Test database requires in-cluster CI")
        return create_async_engine(
            test_url,
            echo=False,
            pool_size=pool_size,
            max_overflow=0,
            pool_timeout=pool_timeout,
            connect_args={"server_settings": settings},
        )
    directory = directory or Path(
        os.environ.get("DATABASE_CREDENTIAL_DIRECTORY", "/var/run/weather-db")
    )
    try:
        username = (directory / "username").read_text().strip()
        password = (directory / "password").read_text().strip()
        tls = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    except (OSError, ValueError, ssl.SSLError):
        raise DatabaseConfigurationError(
            "Invalid database credential or CA files"
        ) from None
    if username not in {
        "weather_migrator",
        "weather_ingest",
        "weather_worker",
        "weather_api",
        "weather_editor",
    }:
        raise DatabaseConfigurationError("Invalid database username")
    if not password or password.lower() in {"changeme", "placeholder", "password"}:
        raise DatabaseConfigurationError("Invalid database password")
    url = URL.create(
        "postgresql+asyncpg",
        username=username,
        password=password,
        host="weather-pg-rw",
        port=5432,
        database="weather",
    )
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        connect_args={"ssl": tls, "server_settings": settings},
    )


def attempt_seconds(deadline: float) -> float:
    """Bound one connection attempt by ATTEMPT_SECONDS and what is left of the deadline."""
    return max(0.1, min(ATTEMPT_SECONDS, deadline - time.monotonic()))


async def wait_for_database(engine: AsyncEngine, timeout: float = FIRST_CONNECT_SECONDS) -> None:
    """Retry the first connection with bounded backoff; never log the underlying error."""
    deadline = time.monotonic() + timeout
    delay = 0.5
    while True:
        try:
            async with asyncio.timeout(attempt_seconds(deadline)):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            return
        except Exception:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DatabaseUnavailable("Database unreachable at startup") from None
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, 5)
