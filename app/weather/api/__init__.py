# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from prometheus_client import REGISTRY, CollectorRegistry

if TYPE_CHECKING:
    from weather.config import Settings


def build_application(settings: Settings, *, registry: CollectorRegistry = REGISTRY) -> FastAPI:
    from .public_routes import install_public_api
    from .runtime import public_lifespan

    app = FastAPI(
        title="Weather",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=public_lifespan(settings, registry) if settings.api_data_enabled else None,
    )

    @app.get("/api/healthz")
    @app.get("/healthz")
    @app.get("/readyz")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "weather-api"}

    if settings.api_data_enabled:
        install_public_api(app)
    return app


def create_app() -> FastAPI:
    from weather.config import load_settings

    return build_application(load_settings())
