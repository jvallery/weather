# SPDX-License-Identifier: MIT
"""Code-first document from the real shared router; runtime injects its source/key."""

from typing import Any

from fastapi import FastAPI
from pydantic.json_schema import models_json_schema

from .public_models import Lightning, Wind
from .public_routes import install_public_api
from .public_service import PublicWeatherService


def contract_app(service: PublicWeatherService | None = None) -> FastAPI:
    app = FastAPI(title="Weather public API", version="1", docs_url=None, redoc_url=None)
    app.openapi_version = "3.1.1"
    install_public_api(app, service)
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
