# SPDX-License-Identifier: MIT
"""Public Weather API. Liveness never touches the database; readiness does (#14)."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, CollectorRegistry
from starlette.datastructures import State
from starlette.types import ASGIApp, Receive, Scope, Send

from weather.config import Settings
from weather.media.catalog import CatalogResolver, media_routes
from weather.media.garage import GarageBody, media_store
from weather.media.origin import MediaMetrics, MediaOrigin
from weather.timemodel import ScheduleError, SiteConfig, load_site

from .cursors import CursorCodec
from .keograms import DatabaseSource as KeogramSource
from .registry import cadences
from .routes import install
from .search import SearchReader, SearchService
from .service import WeatherService
from .source import WeatherReader
from .stills import DatabaseSource as StillSource
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


class MediaDispatch:
    """Hands `/media/` and `/downloads/` to the media origin (#20) ahead of the other
    middleware, so the stream is never wrapped or buffered."""

    def __init__(self, app: ASGIApp, state: State) -> None:
        self.app, self.state = app, state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin: MediaOrigin | None = getattr(self.state, "media", None)
        if scope["type"] == "http" and origin is not None and origin.route_for(scope):
            await origin(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app(
    service: WeatherService | None = None,
    search: SearchService | None = None,
    media: MediaOrigin | None = None,
) -> FastAPI:
    """Without a service (no database configured) data routes answer 503."""
    app = FastAPI(title="Weather", version="1", docs_url=None, redoc_url=None)
    app.state.weather = service
    app.state.search = search
    app.state.media = media

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
    # Added last, so it is the outermost user middleware.
    app.add_middleware(MediaDispatch, state=app.state)
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
    # The read-only weather-media key is optional: without it media GET answers 503.
    store = media_store()
    resolver = CatalogResolver(engine)
    origin = MediaOrigin(
        media_routes(resolver.asset, resolver.download, resolver.latest),
        GarageBody(store) if store is not None else None,
        MediaMetrics(registry),
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
            if store is not None:
                await store.aclose()
            await engine.dispose()

    app = build_app(service, search, origin)
    app.state.timelapses = Catalog(DatabaseSource(engine), TimelapseCursor(key))
    app.state.stills = StillSource(engine)
    app.state.keograms = KeogramSource(engine)
    app.router.lifespan_context = lifespan
    return app


def create_operator(settings: Settings, registry: CollectorRegistry = REGISTRY) -> FastAPI:
    """The operator listener (#44) as weather_editor; 503 until the editor credentials exist."""
    from weather.db.connection import DatabaseConfigurationError, create_database_engine
    from weather.editorial.api import EDITOR_CREDENTIALS, create_operator_app

    try:
        engine = create_database_engine(
            pool_size=2,
            pool_timeout=2,
            directory=EDITOR_CREDENTIALS,
            server_settings={
                "application_name": "weather-editor",
                "statement_timeout": "10000",
                "idle_in_transaction_session_timeout": "15000",
            },
        )
    except DatabaseConfigurationError:
        log.warning("Editor credentials are absent or invalid; the operator API answers 503")
        engine = None
    return create_operator_app(
        engine,
        origin=str(settings.public_origin).rstrip("/"),
        cursor_key=read_cursor_key(),
        profile_id=settings.search_profile,
        registry=registry,
    )


class _Server(uvicorn.Server):
    """One process serves both listeners; the shared handler below stops them together."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def serve(settings: Settings) -> None:
    """The public listener on PORT and the operator listener on OPERATOR_PORT."""
    servers = [
        _Server(uvicorn.Config(app, host="0.0.0.0", port=port, access_log=False))
        for app, port in (
            (create_app(settings), settings.port),
            (create_operator(settings), settings.operator_port),
        )
    ]

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(server.serve() for server in servers))
