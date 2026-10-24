# SPDX-License-Identifier: MIT
"""One real router for runtime adoption and OpenAPI generation; no default data/key."""

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .metric_registry import MetricRegistry, PublicInputError, discovery
from .public_models import AggregateQuery, Aggregates, Current, History, ObservationQuery, Problem
from .public_service import PublicWeatherService, unavailable

router = APIRouter()
CURRENT_HEADERS = {
    "ETag": {
        "description": "Weak validator of values, source timing, quality and data state.",
        "schema": {"type": "string"},
    },
    "Cache-Control": {
        "description": "At most five seconds, bounded by remaining freshness.",
        "schema": {"type": "string"},
    },
}
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Invalid bounded public request."},
    503: {"model": Problem, "description": "Public data is unavailable."},
}


def weak_etag(current: Current) -> str:
    data = current.model_dump(mode="json", exclude={"generated_at"})
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return 'W/"' + hashlib.sha256(encoded).hexdigest() + '"'


def matches_etag(header: str | None, entity: str) -> bool:
    if header is None:
        return False
    if len(header) > 4096:
        raise PublicInputError("invalid_request", "Conditional request header is too large.")
    if header.strip() == "*":
        return True
    expected = entity.removeprefix("W/")
    for token in header.split(","):
        token = token.strip()
        if re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e]*"', token):
            if token.removeprefix("W/") == expected:
                return True
    return False


def _service(request: Request) -> PublicWeatherService:
    service = getattr(request.app.state, "weather_public_service", None)
    if not isinstance(service, PublicWeatherService):
        raise unavailable()
    return service


@router.get(
    "/api/v1/weather/metrics", response_model=MetricRegistry, operation_id="getMetricRegistry"
)
def get_metric_registry(request: Request) -> MetricRegistry:
    service = getattr(request.app.state, "weather_public_service", None)
    return service.metric_registry if isinstance(service, PublicWeatherService) else discovery()


@router.get(
    "/api/v1/weather/current",
    response_model=Current,
    operation_id="getCurrent",
    responses={
        **ERRORS,
        200: {"headers": CURRENT_HEADERS},
        304: {
            "description": "Unchanged semantic current representation.",
            "headers": CURRENT_HEADERS,
        },
    },
)
async def current(
    request: Request,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match", max_length=4096)] = None,
) -> Current | Response:
    service = _service(request)
    data = await service.current()
    tag = weak_etag(data)
    now = service.now()
    remaining = [
        (item.valid_until - now).total_seconds()
        for item in data.metrics
        if item.freshness == "fresh" and item.valid_until is not None
    ]
    seconds = min(5, max(0, int(min(remaining)))) if remaining else 5
    if data.data_state == "cached":
        seconds = 0
    headers = {"ETag": tag, "Cache-Control": f"public, max-age={seconds}, must-revalidate"}
    if matches_etag(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return data


@router.get(
    "/api/v1/weather/observations",
    response_model=History,
    operation_id="getObservations",
    responses=ERRORS,
)
async def observations(
    request: Request, response: Response, query: Annotated[ObservationQuery, Query()]
) -> History:
    data = await _service(request).observations(query)
    response.headers["Cache-Control"] = "no-store"
    return data


@router.get(
    "/api/v1/weather/aggregates",
    response_model=Aggregates,
    operation_id="getAggregates",
    responses=ERRORS,
)
async def aggregates(
    request: Request, response: Response, query: Annotated[AggregateQuery, Query()]
) -> Aggregates:
    data = await _service(request).aggregates(query)
    response.headers["Cache-Control"] = "no-store"
    return data


def install_public_api(app: FastAPI, service: PublicWeatherService | None = None) -> None:
    """Runtime owner supplies the commissioned source and signing key through the service."""
    app.state.weather_public_service = service
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            media_type="application/problem+json",
            headers={"Cache-Control": "no-store"},
            content=Problem(
                code="invalid_request",
                message="Invalid public request parameters.",
                request_id=None,
            ).model_dump(mode="json"),
        )

    @app.exception_handler(PublicInputError)
    async def public_error(_request: Request, error: PublicInputError) -> JSONResponse:
        return JSONResponse(
            status_code=503 if error.code == "unavailable" else 400,
            media_type="application/problem+json",
            headers={"Cache-Control": "no-store"},
            content=Problem(
                code=error.code,
                message=error.message,
                request_id=None,
                suggested_resolution_seconds=error.suggested_resolution_seconds,
            ).model_dump(mode="json"),
        )

    @app.middleware("http")
    async def public_signals(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.monotonic()
        response: Response = await call_next(request)
        if response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
        active = getattr(request.app.state, "weather_public_service", None)
        if isinstance(active, PublicWeatherService):
            routes = {
                "/api/v1/weather/current": "current",
                "/api/v1/weather/metrics": "metrics",
                "/api/v1/weather/observations": "observations",
                "/api/v1/weather/aggregates": "aggregates",
            }
            active.metrics.http.labels(
                routes.get(request.url.path, "other"), str(response.status_code)
            ).observe(time.monotonic() - started)
        return response
