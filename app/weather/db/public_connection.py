# SPDX-License-Identifier: MIT
"""Lazy, bounded API-only connections with required CA and hostname verification."""

import math
import ssl
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class PublicDatabaseConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class PublicDatabaseBudget:
    pool_size: int = 4
    pool_timeout_seconds: float = 0.25
    statement_timeout_ms: int = 1500
    connect_timeout_seconds: float = 2

    def __post_init__(self) -> None:
        if (
            type(self.pool_size) is not int
            or not 1 <= self.pool_size <= 4
            or type(self.statement_timeout_ms) is not int
            or not 1 <= self.statement_timeout_ms <= 2000
            or type(self.pool_timeout_seconds) not in {int, float}
            or not math.isfinite(self.pool_timeout_seconds)
            or not 0 < self.pool_timeout_seconds <= 0.5
            or type(self.connect_timeout_seconds) not in {int, float}
            or not math.isfinite(self.connect_timeout_seconds)
            or not 0 < self.connect_timeout_seconds <= 2
        ):
            raise PublicDatabaseConfigurationError("Invalid public database budgets")


DEFAULT_BUDGET = PublicDatabaseBudget()


def _credential(path: Path) -> str:
    with path.open("rb") as stream:
        value = stream.read(4097)
    if not value or len(value) > 4096:
        raise ValueError("credential size")
    result = value.decode("utf8").strip()
    if not result or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("credential format")
    return result


def create_public_database_engine(
    directory: Path = Path("/var/run/weather-db"),
    *,
    budget: PublicDatabaseBudget = DEFAULT_BUDGET,
) -> AsyncEngine:
    """Create an engine without opening a connection or accepting a URL override.

    Four connections per API process, zero overflow: two replicas use eight,
    and a third rolling replica uses twelve of weather_api's limit of forty.
    Never reuse this read-only pool for later operator write endpoints.
    """
    try:
        username = _credential(directory / "username")
        password = _credential(directory / "password")
        if username != "weather_api" or password.lower() in {"changeme", "placeholder", "password"}:
            raise ValueError("public role")
        tls = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(directory / "ca.crt"))
        tls.verify_mode = ssl.CERT_REQUIRED
        tls.check_hostname = True
    except OSError, ValueError, UnicodeError, ssl.SSLError:
        raise PublicDatabaseConfigurationError(
            "Invalid API database credential or CA files"
        ) from None
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
        pool_size=budget.pool_size,
        max_overflow=0,
        pool_timeout=budget.pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "ssl": tls,
            "timeout": budget.connect_timeout_seconds,
            "command_timeout": budget.statement_timeout_ms / 1000,
            "server_settings": {
                "application_name": "weather-api-reader",
                "timezone": "UTC",
                "default_transaction_read_only": "on",
                "statement_timeout": str(budget.statement_timeout_ms),
                "lock_timeout": "500",
                "idle_in_transaction_session_timeout": "3000",
            },
        },
    )
