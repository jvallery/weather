# SPDX-License-Identifier: MIT
"""Create TLS database connections without logging a credential-bearing URL."""

import os
import ssl
from pathlib import Path

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class DatabaseConfigurationError(ValueError):
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
