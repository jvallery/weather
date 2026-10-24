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
from weather.timemodel import ScheduleError, SiteConfig, load_site

from .cursors import CursorCodec
from .registry import cadences
from .routes import install
from .search import SearchReader, SearchService
from .service import WeatherService
from .source import WeatherReader
from .timelapses import Catalog, DatabaseSource, TimelapseCursor

CURSOR_KEY = Path("/var/run/weather-api-cursor/CURSOR_HMAC_KEY")
# Private site position for derived astronomy (#42), from the optional weather-site Secret.
SITE_FILE = Path("/var/run/weather-site/site.json")
# Four connections per pod, no overflow: two replicas plus a surge pod stay far below the
# weather_api role's limit of 40, and a saturated API pool cannot starve other roles.
POOL_SIZE = 4
log = logging.getLogger("weather.api")


def read_cursor_key(path: Path = CURSOR_KEY) -> bytes:
    key = path.read_bytes().strip()
    if len(key) < 32:
        raise ValueError("Cursor signing key is too short")
    return key


def read_site(path: Path = SITE_FILE) -> SiteConfig | None:
    """Astronomy is reported unavailable, never guessed, while the site is absent or invalid."""
    if not path.is_file():
        return None
    try:
        return load_site(path)
    except ScheduleError:
        # The message names no field values.
        log.warning("Private site configuration is invalid; astronomy is unavailable")
        return None


def build_app(
    service: WeatherService | None = None, search: SearchService | None = None
) -> FastAPI:
    """Without a service (no database configured) data routes answer 503."""
    app = FastAPI(title="Weather", version="1", docs_url=None, redoc_url=None)
    app.state.weather = service
    app.state.search = search

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
    from weather.search.cortex import load_embedder

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
    key = read_cursor_key()
    service = WeatherService(
        WeatherReader(engine), CursorCodec(key), cadences=cadences(settings),
        registry=registry, site=read_site(),
    )
    # Without Cortex configuration or its key, search stays available as lexical/numeric.
    embedder = load_embedder(
        str(settings.cortex_base_url) if settings.cortex_base_url else None,
        settings.search_profile,
    )
    search = SearchService(
        SearchReader(engine),
        embedder,
        profile_id=settings.search_profile,
        cadences=cadences(settings),
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
        search.start()
        try:
            yield
        finally:
            await search.stop()
            await service.stop()
            if embedder is not None:
                await embedder.aclose()
            await engine.dispose()

    app = build_app(service, search)
    app.state.timelapses = Catalog(DatabaseSource(engine), TimelapseCursor(key))
    app.router.lifespan_context = lifespan
    return app
