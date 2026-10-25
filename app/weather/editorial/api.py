# SPDX-License-Identifier: MIT
"""The operator listener (#44): `/api/v1/operator/*` on its own port, behind Authentik.

Traefik routes `/api/v1/operator/*` here only through `apps-strip-auth-headers` then
`network-infra/ak-forwardauth`, and a NetworkPolicy admits this port only from traefik-apps,
so the `X-authentik-*` identity can only have come from Authentik. The public listener has no
operator routes (404) and never reads identity headers.

Every request: exactly one non-empty `X-authentik-username` and `X-authentik-meta-app`, checked
against the database allowlist on each request (401 without an identity, 403 when not listed).
Every mutation additionally needs the exact `Origin`, a same-origin fetch and a JSON body (CSRF;
the app sets no cookies). Every response is `no-store`. Query errors are 400; body errors are
422 (unknown, actor or measured-value fields, empty or over-long text), never truncated.
"""

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, CollectorRegistry, Counter
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.api.registry import PublicError

from . import receipts, store
from .models import (
    ManualEvent,
    Operator,
    OperatorProblem,
    ReceiptQuery,
    Receipts,
    Subject,
    SubjectPatch,
)

PREFIX = "/api/v1/operator"
EDITOR_CREDENTIALS = Path("/var/run/weather-db-editor")
APPLICATION = "weather-editor"
MAX_BODY = 64 * 1024
IDENTITY = re.compile(r"^[A-Za-z0-9@._+-]{1,256}$")
LOG = logging.getLogger("weather.editorial")
PRIVATE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": OperatorProblem, "description": description}
    for status, description in (
        (400, "Invalid query."),
        (401, "No authenticated identity."),
        (403, "Not an allowlisted operator, or a cross-origin mutation."),
        (404, "No such subject."),
        (503, "The editor is temporarily unavailable."),
    )
}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **ERRORS,
    409: {"model": OperatorProblem, "description": "Revision conflict or rebase required."},
    415: {"model": OperatorProblem, "description": "The body must be JSON."},
    422: {"model": OperatorProblem, "description": "Invalid change."},
}


class Unavailable(RuntimeError):
    pass


@dataclass
class OperatorMetrics:
    registry: CollectorRegistry = field(default=REGISTRY)

    def __post_init__(self) -> None:
        self.requests = Counter(
            "weather_operator_requests_total",
            "Operator API responses by route and status",
            ["route", "status"],
            registry=self.registry,
        )
        self.denied = Counter(
            "weather_operator_denied_total",
            "Operator requests refused before any handler",
            ["reason"],
            registry=self.registry,
        )
        for reason in ("identity", "allowlist", "origin", "content_type", "body"):
            self.denied.labels(reason).inc(0)


class EditorialService:
    """Database work for the operator routes, as weather_editor; failures are sanitized."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        cursor_key: bytes,
        profile_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.cursor_key, self.profile_id, self.clock = (
            engine,
            cursor_key,
            profile_id,
            clock,
        )

    async def _run(self, work: Callable[[Any], Awaitable[Any]], *, write: bool = False) -> Any:
        try:
            if write:
                async with self.engine.begin() as connection:
                    return await work(connection)
            async with self.engine.connect() as connection:
                return await work(connection)
        except store.NotFound, store.Conflict, store.RebaseRequired, store.Invalid, PublicError:
            raise
        except DBAPIError as error:
            code = str(getattr(error.orig, "sqlstate", "") or "")
            if code.startswith("23"):
                # A database CHECK refused the change (e.g. a public automatic event without
                # evidence); the transaction rolled back.
                raise store.Invalid("The change violates a catalog rule") from None
            LOG.warning("Operator database operation failed (%s)", type(error).__name__)
            raise Unavailable("Editor unavailable") from None
        except Exception as error:
            LOG.warning("Operator operation failed (%s)", type(error).__name__)
            raise Unavailable("Editor unavailable") from None

    async def allowed(self, username: str, application: str) -> bool:
        return bool(await self._run(lambda c: store.allowed(c, username, application)))

    async def subject(self, subject_id: UUID) -> Subject:
        return await self._run(lambda c: store.read(c, subject_id))  # type: ignore[no-any-return]

    async def patch(self, subject_id: UUID, body: SubjectPatch, actor: str) -> Subject:
        return await self._run(  # type: ignore[no-any-return]
            lambda c: store.patch(
                c, subject_id, body, actor=actor, profile_id=self.profile_id, now=self.clock()
            ),
            write=True,
        )

    async def manual_event(self, body: ManualEvent, actor: str) -> Subject:
        return await self._run(  # type: ignore[no-any-return]
            lambda c: store.create_event(c, body, actor=actor, now=self.clock()), write=True
        )

    async def receipts(self, query: ReceiptQuery) -> Receipts:
        return await self._run(  # type: ignore[no-any-return]
            lambda c: receipts.page(c, self.cursor_key, query)
        )


def _problem(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers=PRIVATE_HEADERS,
        content=OperatorProblem(code=code, message=message, **extra).model_dump(mode="json"),
    )


def _service(request: Request) -> EditorialService:
    service = getattr(request.app.state, "editorial", None)
    if not isinstance(service, EditorialService):
        raise Unavailable("Editor unavailable")
    return service


def _subject_id(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError:
        raise store.NotFound("No such subject") from None
    if str(parsed) != value:
        raise store.NotFound("No such subject")
    return parsed


def build_operator_app(
    service: EditorialService | None,
    *,
    origin: str,
    registry: CollectorRegistry = REGISTRY,
) -> FastAPI:
    """`origin` is the exact public origin, e.g. https://weather.vallery.net (no slash)."""
    app = FastAPI(
        title="Weather operator API", version="1", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.editorial = service
    metrics = OperatorMetrics(registry)

    @app.get(
        f"{PREFIX}/session", response_model=Operator, operation_id="getOperator", responses=ERRORS
    )
    async def session(request: Request) -> Operator:
        return Operator(username=request.state.actor)

    @app.get(
        f"{PREFIX}/subjects/{{subject_id}}",
        response_model=Subject,
        operation_id="getSubject",
        responses=ERRORS,
    )
    async def get_subject(request: Request, subject_id: str) -> Subject:
        return await _service(request).subject(_subject_id(subject_id))

    @app.patch(
        f"{PREFIX}/subjects/{{subject_id}}",
        response_model=Subject,
        operation_id="patchSubject",
        responses=WRITE_ERRORS,
    )
    async def patch_subject(request: Request, subject_id: str, body: SubjectPatch) -> Subject:
        identity = _subject_id(subject_id)
        return await _service(request).patch(identity, body, request.state.actor)

    @app.post(
        f"{PREFIX}/events",
        response_model=Subject,
        status_code=201,
        operation_id="createManualEvent",
        responses=WRITE_ERRORS,
    )
    async def manual_event(request: Request, body: ManualEvent) -> Subject:
        return await _service(request).manual_event(body, request.state.actor)

    @app.get(
        f"{PREFIX}/telemetry/receipts",
        response_model=Receipts,
        operation_id="getReceipts",
        responses=ERRORS,
    )
    async def get_receipts(request: Request, query: Annotated[ReceiptQuery, Query()]) -> Receipts:
        return await _service(request).receipts(query)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, error: RequestValidationError) -> JSONResponse:
        if any(item.get("loc", ("",))[0] == "body" for item in error.errors()):
            return _problem(422, "invalid_change", "Invalid change.")
        return _problem(400, "invalid_request", "Invalid request parameters.")

    @app.exception_handler(PublicError)
    async def public(_request: Request, error: PublicError) -> JSONResponse:
        return _problem(400, error.code, error.message)

    @app.exception_handler(store.NotFound)
    async def missing(_request: Request, _error: store.NotFound) -> JSONResponse:
        return _problem(404, "not_found", "No such subject.")

    @app.exception_handler(store.Conflict)
    async def conflict(_request: Request, error: store.Conflict) -> JSONResponse:
        return _problem(
            409,
            "revision_conflict",
            "The subject changed; reload it.",
            current_revision=error.current,
        )

    @app.exception_handler(store.RebaseRequired)
    async def rebase(_request: Request, _error: store.RebaseRequired) -> JSONResponse:
        return _problem(
            409, "rebase_required", "Media or evidence changed; review it and rebase explicitly."
        )

    @app.exception_handler(store.Invalid)
    async def refused(_request: Request, error: store.Invalid) -> JSONResponse:
        return _problem(422, "invalid_change", str(error))

    @app.exception_handler(Unavailable)
    async def down(_request: Request, _error: Unavailable) -> JSONResponse:
        response = _problem(503, "unavailable", "The editor is temporarily unavailable.")
        response.headers["Retry-After"] = "5"
        return response

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await _guarded(request, call_next)
        response.headers.update(PRIVATE_HEADERS)
        route = request.scope.get("route")
        metrics.requests.labels(
            getattr(route, "name", None) or "none", str(response.status_code)
        ).inc()
        return response

    async def _guarded(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith(PREFIX + "/"):
            return _problem(404, "not_found", "Not found.")
        if request.method not in ("GET", "HEAD"):
            # CSRF: the exact origin, a same-origin fetch and a JSON body.
            if (
                request.headers.getlist("origin") != [origin]
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
            ):
                metrics.denied.labels("origin").inc()
                return _problem(403, "origin_rejected", "Cross-origin request refused.")
            media = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if media != "application/json":
                metrics.denied.labels("content_type").inc()
                return _problem(415, "unsupported_media_type", "The body must be JSON.")
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > MAX_BODY:
                metrics.denied.labels("body").inc()
                return _problem(413, "body_too_large", "Invalid request body.")
        users = request.headers.getlist("x-authentik-username")
        apps = request.headers.getlist("x-authentik-meta-app")
        if len(users) != 1 or len(apps) != 1 or not IDENTITY.match(users[0]) or not apps[0]:
            metrics.denied.labels("identity").inc()
            return _problem(401, "unauthenticated", "Sign in through the editor.")
        if apps[0] != APPLICATION:
            metrics.denied.labels("allowlist").inc()
            return _problem(403, "forbidden", "Not an allowlisted operator.")
        try:
            permitted = await _service(request).allowed(users[0], apps[0])
        except Unavailable:
            response = _problem(503, "unavailable", "The editor is temporarily unavailable.")
            response.headers["Retry-After"] = "5"
            return response
        if not permitted:
            metrics.denied.labels("allowlist").inc()
            return _problem(403, "forbidden", "Not an allowlisted operator.")
        request.state.actor = users[0]
        return await call_next(request)

    return app


def operator_openapi() -> dict[str, Any]:
    """The generated operator contract: problems as application/problem+json, no FastAPI 422."""
    app = build_operator_app(
        None, origin="https://weather.vallery.net", registry=CollectorRegistry()
    )
    document = get_openapi(title="Weather operator API", version="1", routes=app.routes)
    for path in document["paths"].values():
        for operation in path.values():
            responses = operation.get("responses", {})
            if "HTTPValidationError" in json.dumps(responses.get("422", {})):
                responses.pop("422")
            for response in responses.values():
                content = response.get("content", {})
                if "OperatorProblem" in json.dumps(content.get("application/json", {})):
                    response["content"] = {"application/problem+json": content["application/json"]}
    schemas = document["components"]["schemas"]
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    return document


def create_operator_app(
    engine: AsyncEngine | None,
    *,
    origin: str,
    cursor_key: bytes,
    profile_id: str,
    registry: CollectorRegistry = REGISTRY,
) -> FastAPI:
    """Without the editor credentials (engine None) every operator route answers 503."""
    service = (
        EditorialService(engine, cursor_key=cursor_key, profile_id=profile_id)
        if engine is not None
        else None
    )
    app = build_operator_app(service, origin=origin, registry=registry)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app.router.lifespan_context = lifespan
    return app
