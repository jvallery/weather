# SPDX-License-Identifier: MIT
"""Public Weather API. Liveness never touches the database; readiness does (#14)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, CollectorRegistry

from weather.config import Settings

from .cursors import CursorCodec
from .registry import cadences
from .routes import install
from .service import WeatherService
from .source import WeatherReader

CURSOR_KEY = Path("/var/run/weather-api-cursor/CURSOR_HMAC_KEY")
# Four connections per pod, no overflow: two replicas plus a surge pod stay far below the
# weather_api role's limit of 40, and a saturated API pool cannot starve other roles.
POOL_SIZE = 4
log = logging.getLogger("weather.api")


def read_cursor_key(path: Path = CURSOR_KEY) -> bytes:
    key = path.read_bytes().strip()
    if len(key) < 32:
        raise ValueError("Cursor signing key is too short")
    return key


def build_app(service: WeatherService | None = None) -> FastAPI:
    """Without a service (contract generation, tests) data routes answer 503."""
    app = FastAPI(title="Weather", version="1", docs_url=None, redoc_url=None)
    app.state.weather = service

    @app.get("/healthz", include_in_schema=False)
    @app.get("/api/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "weather-api"}

    @app.get("/readyz", include_in_schema=False)
    def ready() -> JSONResponse:
        current = app.state.weather
        ok = isinstance(current, WeatherService) and current.ready()
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    install(app)
    return app


def create_app(settings: Settings, registry: CollectorRegistry = REGISTRY) -> FastAPI:
    from weather.db.connection import (
        DatabaseUnavailable,
        create_database_engine,
        wait_for_database,
    )

    engine = create_database_engine(
        pool_size=POOL_SIZE,
        pool_timeout=2,
        server_settings={
            "application_name": "weather-api",
            "default_transaction_read_only": "on",
            "statement_timeout": "10000",
            "idle_in_transaction_session_timeout": "15000",
        },
    )
    service = WeatherService(
        WeatherReader(engine), CursorCodec(read_cursor_key()), cadences=cadences(settings),
        registry=registry,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await wait_for_database(engine)
        except DatabaseUnavailable:
            # Stay up but unready; the refresh loop keeps retrying.
            log.warning("Database unreachable at startup; serving 503 until it answers")
        service.start()
        try:
            yield
        finally:
            await service.stop()
            await engine.dispose()

    app = build_app(service)
    app.router.lifespan_context = lifespan
    return app
