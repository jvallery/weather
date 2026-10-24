# SPDX-License-Identifier: MIT
"""Public read-only routes. Errors are sanitized problem documents; nothing private is echoed."""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import (
    AggregateQuery,
    Aggregates,
    Almanac,
    AlmanacQuery,
    Current,
    MetricRegistry,
    ObservationQuery,
    Observations,
    Problem,
)
from .registry import PublicError, unavailable
from .service import WeatherService
from .timelapses import router as timelapses

ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": Problem, "description": "Invalid request."},
    503: {"model": Problem, "description": "Weather data is temporarily unavailable."},
}
ROUTES = {
    "/api/v1/weather/current": "current",
    "/api/v1/weather/metrics": "metrics",
    "/api/v1/weather/observations": "observations",
    "/api/v1/weather/aggregates": "aggregates",
    "/api/v1/weather/almanac": "almanac",
    "/api/v1/timelapses": "timelapses",
}
router = APIRouter()


def weak_etag(current: Current) -> str:
    data = current.model_dump(mode="json", exclude={"generated_at"})
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return 'W/"' + hashlib.sha256(encoded).hexdigest()[:32] + '"'


def etag_matches(header: str | None, tag: str) -> bool:
    if not header:
        return False
    wanted = tag.removeprefix("W/")
    return any(item.strip().removeprefix("W/") in (wanted, "*") for item in header.split(","))


def _service(request: Request) -> WeatherService:
    service = getattr(request.app.state, "weather", None)
    if not isinstance(service, WeatherService):
        raise unavailable()
    return service


@router.get("/api/v1/weather/metrics", response_model=MetricRegistry, operation_id="getMetrics")
def get_metrics(request: Request) -> MetricRegistry:
    return _service(request).registry_document


@router.get(
    "/api/v1/weather/current",
    response_model=Current,
    operation_id="getCurrent",
    responses={**ERRORS, 304: {"description": "Unchanged since the given ETag."}},
)
async def get_current(
    request: Request,
    response: Response,
    if_none_match: Annotated[str | None, Header(max_length=1024)] = None,
) -> Current | Response:
    service = _service(request)
    current = service.current()
    tag = weak_etag(current)
    now = current.generated_at
    remaining = [
        (item.valid_until - now).total_seconds()
        for item in current.metrics
        if item.freshness == "fresh" and item.valid_until is not None
    ]
    seconds = 0 if current.data_state == "cached" else int(min([5.0, *remaining]))
    headers = {"ETag": tag, "Cache-Control": f"public, max-age={max(0, seconds)}"}
    if etag_matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return current


@router.get(
    "/api/v1/weather/observations",
    response_model=Observations,
    operation_id="getObservations",
    responses=ERRORS,
)
async def get_observations(
    request: Request, response: Response, query: Annotated[ObservationQuery, Query()]
) -> Observations:
    result = await _service(request).observations(query)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/api/v1/weather/aggregates",
    response_model=Aggregates,
    operation_id="getAggregates",
    responses=ERRORS,
)
async def get_aggregates(
    request: Request, response: Response, query: Annotated[AggregateQuery, Query()]
) -> Aggregates:
    result = await _service(request).aggregates(query)
    response.headers["Cache-Control"] = "public, max-age=60"
    return result


@router.get(
    "/api/v1/weather/almanac",
    response_model=Almanac,
    operation_id="getAlmanac",
    responses=ERRORS,
)
async def get_almanac(
    request: Request, response: Response, query: Annotated[AlmanacQuery, Query()]
) -> Almanac:
    result = await _service(request).almanac(query)
    response.headers["Cache-Control"] = "public, max-age=60"
    return result


def _problem(status: int, problem: Problem) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
        content=problem.model_dump(mode="json"),
    )


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.include_router(timelapses)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return _problem(400, Problem(code="invalid_request", message="Invalid request parameters."))

    @app.exception_handler(PublicError)
    async def public(_request: Request, error: PublicError) -> JSONResponse:
        return _problem(
            503 if error.code == "unavailable" else 400,
            Problem(
                code=error.code,
                message=error.message,
                suggested_resolution_seconds=error.suggested_resolution_seconds,
            ),
        )

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.monotonic()
        response = await call_next(request)
        service = getattr(request.app.state, "weather", None)
        route = ROUTES.get(request.url.path)
        if isinstance(service, WeatherService) and route:
            service.metrics.latency.labels(route, str(response.status_code)).observe(
                time.monotonic() - started
            )
        return response


def public_openapi() -> dict[str, Any]:
    """The generated contract: problem responses as application/problem+json, no 422."""
    app = FastAPI(title="Weather public API", version="1", docs_url=None, redoc_url=None)
    install(app)
    document = app.openapi()
    for path in document["paths"].values():
        for operation in path.values():
            responses = operation.get("responses", {})
            responses.pop("422", None)
            for code in ("400", "503"):
                if code in responses and "content" in responses[code]:
                    responses[code]["content"] = {
                        "application/problem+json": responses[code]["content"]["application/json"]
                    }
    schemas = document["components"]["schemas"]
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    return document
