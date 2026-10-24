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


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def create_database_engine() -> AsyncEngine:
    # Only the cluster integration fixture may use its isolated test endpoint.
    test_url = os.environ.get("WEATHER_CI_DATABASE_URL")
    if test_url:
        if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
            raise DatabaseConfigurationError("Test database requires in-cluster CI")
        return create_async_engine(
            test_url,
            echo=False,
            pool_size=5,
            max_overflow=0,
            connect_args={"server_settings": {"timezone": "UTC"}},
        )
    directory = Path(
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
        pool_size=5,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"ssl": tls, "server_settings": {"timezone": "UTC"}},
    )


async def wait_for_database(engine: AsyncEngine, timeout: float = FIRST_CONNECT_SECONDS) -> None:
    """Retry the first connection with bounded backoff; never log the underlying error."""
    deadline = time.monotonic() + timeout
    delay = 0.5
    while True:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return
        except Exception:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DatabaseUnavailable("Database unreachable at startup") from None
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, 5)
