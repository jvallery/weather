# SPDX-License-Identifier: MIT
"""Metadata-only contract app. Production must adopt models with real database handlers."""

from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic.json_schema import models_json_schema

from .metric_registry import PublicInputError, router
from .public_models import (
    AggregateQuery,
    Aggregates,
    Current,
    History,
    Lightning,
    ObservationQuery,
    Problem,
    Wind,
)


def contract_app() -> FastAPI:
    app = FastAPI(title="Weather public API", version="1", docs_url=None, redoc_url=None)
    app.openapi_version = "3.1.1"
    app.include_router(router)
    errors: dict[int | str, dict[str, Any]] = {
        400: {"model": Problem, "description": "Invalid bounded public request."},
        503: {"model": Problem, "description": "Public data is unavailable."},
    }

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        # Never serialize ValidationError.input/ctx, SQL, headers or configuration.
        return JSONResponse(
            media_type="application/problem+json",
            status_code=400,
            content=Problem(
                code="invalid_request",
                message="Invalid public request parameters.",
                request_id=None,
            ).model_dump(mode="json"),
        )

    @app.exception_handler(PublicInputError)
    async def public_error(_request: Request, error: PublicInputError) -> JSONResponse:
        return JSONResponse(
            media_type="application/problem+json",
            status_code=503 if error.code == "unavailable" else 400,
            content=Problem(
                code=error.code,
                message=error.message,
                request_id=None,
                suggested_resolution_seconds=error.suggested_resolution_seconds,
            ).model_dump(mode="json"),
        )

    @app.get(
        "/api/v1/weather/current",
        response_model=Current,
        operation_id="getCurrent",
        responses=errors,
    )
    def current() -> Current:
        raise PublicInputError("unavailable", "Weather data handler is not commissioned.")

    @app.get(
        "/api/v1/weather/observations",
        response_model=History,
        operation_id="getObservations",
        responses=errors,
    )
    def observations(query: Annotated[ObservationQuery, Query()]) -> History:
        del query
        raise PublicInputError("unavailable", "Weather data handler is not commissioned.")

    @app.get(
        "/api/v1/weather/aggregates",
        response_model=Aggregates,
        operation_id="getAggregates",
        responses=errors,
    )
    def aggregates(query: Annotated[AggregateQuery, Query()]) -> Aggregates:
        del query
        raise PublicInputError("unavailable", "Weather data handler is not commissioned.")

    return app


def public_openapi() -> dict[str, Any]:
    """Generate all response types from Pydantic, including structured computation results."""
    document = contract_app().openapi()
    for path in document["paths"].values():
        for operation in path.values():
            operation.get("responses", {}).pop("422", None)
            for code in ("400", "503"):
                response = operation.get("responses", {}).get(code)
                if response and "application/json" in response.get("content", {}):
                    response["content"] = {
                        "application/problem+json": response["content"]["application/json"]
                    }
    schemas = document["components"]["schemas"]
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    _, extra = models_json_schema(
        [(Wind, "serialization"), (Lightning, "serialization")],
        ref_template="#/components/schemas/{model}",
    )
    for name, schema in extra.get("$defs", {}).items():
        if name in schemas and schemas[name] != schema:
            raise ValueError("Conflicting code-first public schema definitions")
        schemas[name] = schema
    return document
