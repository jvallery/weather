# SPDX-License-Identifier: MIT
"""`weather preview`: the `weather-preview` Deployment (C17).

The only public-path process that reads the staging share, and only its read-only `previews/`
mount. It serves two things, each authorized on every request against `public_api.previews`
with the database clock (P8, P14), and re-checked here with that same clock:

- `GET /api/v1/cameras/{camera}/preview`: the newest eligible generation, classified fresh,
  stale or unavailable by `now - captured_lower`. A pointer whose file is missing or differs
  from its hash is unavailable, so it never advertises bytes it cannot serve.
- `GET`/`HEAD` `/media/previews/{id}`: #20's media origin with a file body; ranges, the single
  404 for every denial and `no-store` come from `origin.py`. Bytes stay servable until
  `expires_at`, after the pointer has stopped calling them current.

File reads run on a small dedicated thread pool with a deadline. A stalled NFS read answers
unavailable within that deadline; once every reader is stuck, requests answer at once instead
of queueing. The api never imports this module's routes and never mounts staging, so a NAS
outage cannot reach `/api/v1/weather/current`.
"""

import asyncio
import errno
import hashlib
import logging
import os
import re
import stat
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, CollectorRegistry, Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException

from weather.api.models import Preview, PreviewImage, PreviewInterval, PreviewReason, Problem
from weather.logs import configure
from weather.media.origin import (
    CHUNK,
    MediaMetrics,
    MediaOrigin,
    Representation,
    Unavailable,
    uuid_route,
)

from . import policy

LOG = logging.getLogger("weather.preview")
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
PATH = re.compile(r"previews/(skycam_east|skycam_west|frontyard_1)/[0-9]{1,16}-(large|small)\.jpg")
MAX_BYTES = 2 * 1024 * 1024
READ_SECONDS = 2.0
READERS = 4
COLUMNS = (
    "id,camera_id,generation,variant,width,height,bytes,sha256,path,captured_lower,"
    "captured_upper,public_not_before,first_published_at,expires_at"
)
LATEST = f"""
    SELECT n.now,p.* FROM (SELECT clock_timestamp() AS now) n
    LEFT JOIN LATERAL (
      SELECT {COLUMNS} FROM public_api.previews
      WHERE camera_id = :camera AND generation = (
        SELECT max(generation) FROM public_api.previews WHERE camera_id = :camera)
    ) p ON true
"""
ONE = f"""
    SELECT clock_timestamp() AS now,{COLUMNS} FROM public_api.previews
    WHERE id = CAST(:id AS uuid)
"""
NO_STORE = {"Cache-Control": "no-store"}
router = APIRouter()


class PreviewMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.pointers = Counter(
            "weather_preview_pointer_total",
            "Preview pointer responses by camera and state",
            ["camera", "state"],
            registry=registry,
        )
        self.violations = Counter(
            "weather_preview_embargo_violations_total",
            "Rows the database view admitted that failed the request-time re-check; always "
            "denied, so served-before-embargo stays 0",
            ["route"],
            registry=registry,
        )
        self.files = Counter(
            "weather_preview_file_failures_total",
            "Preview file reads that failed: missing, mismatch, timeout, busy or error",
            ["reason"],
            registry=registry,
        )


@dataclass(frozen=True)
class Row:
    id: UUID
    camera_id: str
    generation: int
    variant: str
    width: int
    height: int
    bytes: int
    sha256: str
    path: str
    timing: policy.Timing

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> Row:
        return cls(
            id=row["id"],
            camera_id=row["camera_id"],
            generation=int(row["generation"]),
            variant=row["variant"],
            width=int(row["width"]),
            height=int(row["height"]),
            bytes=int(row["bytes"]),
            sha256=row["sha256"],
            path=row["path"],
            timing=policy.Timing(
                captured_lower=row["captured_lower"],
                captured_upper=row["captured_upper"],
                public_not_before=row["public_not_before"],
                first_published_at=row["first_published_at"],
                expires_at=row["expires_at"],
            ),
        )


class Source:
    """Reads only `public_api.previews` as the API role, read-only, with a statement deadline."""

    def __init__(self, engine: AsyncEngine, *, timeout_ms: int = 1500) -> None:
        self._engine, self._timeout_ms = engine, timeout_ms

    async def _rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            async with (
                asyncio.timeout(self._timeout_ms / 1000 + 1),
                self._engine.connect() as connection,
                connection.begin(),
            ):
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(
                    text(f"SET LOCAL statement_timeout = {int(self._timeout_ms)}")
                )
                result = await connection.execute(text(sql), params)
                return [dict(row) for row in result.mappings()]
        except Exception:
            # Never retain or log driver errors, SQL or parameters.
            raise Unavailable("catalog") from None

    async def latest(self, camera: str) -> tuple[datetime, list[Row]]:
        rows = await self._rows(LATEST, {"camera": camera})
        now = rows[0]["now"]
        return now, [Row.from_mapping(r) for r in rows if r["id"] is not None]

    async def one(self, identity: UUID) -> tuple[datetime, Row] | None:
        rows = await self._rows(ONE, {"id": identity})
        return (rows[0]["now"], Row.from_mapping(rows[0])) if rows else None


class Files:
    """Bounded reads of verified preview bytes from the read-only `previews/` mount."""

    def __init__(
        self,
        root: Path,
        metrics: PreviewMetrics,
        *,
        timeout: float = READ_SECONDS,
        readers: int = READERS,
        read: Callable[[Path, int], bytes | None] | None = None,
    ) -> None:
        self.root, self.metrics, self.timeout, self.readers = root, metrics, timeout, readers
        self._executor = ThreadPoolExecutor(max_workers=readers, thread_name_prefix="preview")
        self._busy = 0
        self._lock = threading.Lock()
        self._read = read or _read_file

    async def read(self, path: str, size: int, sha256: str) -> bytes | None:
        """The verified bytes; None for a missing or different file; Unavailable on a stall."""
        if not PATH.fullmatch(path) or not 0 < size <= MAX_BYTES:
            self.metrics.files.labels("missing").inc()
            return None
        with self._lock:
            if self._busy >= self.readers:
                # Every reader is stuck on the share: answer now, never queue behind it.
                self.metrics.files.labels("busy").inc()
                raise Unavailable("store")
            self._busy += 1
        future = self._executor.submit(self._read, self.root / path.removeprefix("previews/"), size)

        def release(_: object) -> None:
            with self._lock:
                self._busy -= 1

        future.add_done_callback(release)
        try:
            async with asyncio.timeout(self.timeout):
                data = await asyncio.wrap_future(future)
        except TimeoutError:
            self.metrics.files.labels("timeout").inc()
            raise Unavailable("store") from None
        except OSError:
            self.metrics.files.labels("error").inc()
            raise Unavailable("store") from None
        if data is None:
            self.metrics.files.labels("missing").inc()
            return None
        if hashlib.sha256(data).hexdigest() != sha256:
            self.metrics.files.labels("mismatch").inc()
            return None
        return data

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _read_file(path: Path, size: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return None
        raise
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            return None
        data = handle.read(size + 1)
    return data if len(data) == size else None


@dataclass(frozen=True)
class Loaded(Representation):
    """A preview's verified bytes, held for this one response."""

    data: bytes = field(default=b"", repr=False)


class FileBody:
    @asynccontextmanager
    async def open(
        self, representation: Representation, first: int, last: int
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        if not isinstance(representation, Loaded):
            raise Unavailable("store")
        data = representation.data

        async def chunks() -> AsyncGenerator[bytes]:
            for offset in range(first, last + 1, CHUNK):
                yield data[offset : min(offset + CHUNK, last + 1)]

        yield chunks()


class PreviewService:
    def __init__(
        self,
        source: Any,
        files: Files,
        metrics: PreviewMetrics,
        *,
        skew_margin_seconds: int = int(policy.DEFAULT_SKEW.total_seconds()),
    ) -> None:
        self.source, self.files, self.metrics = source, files, metrics
        self.skew_margin_seconds = skew_margin_seconds

    def _servable(self, row: Row, now: datetime, route: str) -> bool:
        if policy.servable(row.timing, now):
            return True
        self.metrics.violations.labels(route).inc()
        return False

    async def resolve(self, identity: UUID) -> Representation | None:
        """The bytes route: any servable row, current or not, until `expires_at`."""
        found = await self.source.one(identity)
        if found is None or not self._servable(found[1], found[0], "media"):
            return None
        now, row = found
        data = await self.files.read(row.path, row.bytes, row.sha256)
        if data is None:
            return None
        # The capture bound and database clock let the origin's breach detector re-check (#49).
        return Loaded(
            locator=row.path,
            length=row.bytes,
            sha256=row.sha256,
            content_type="image/jpeg",
            product="preview",
            captured_upper=row.timing.captured_upper,
            resolved_at=now,
            data=data,
        )

    def _response(self, camera: str, now: datetime, reason: PreviewReason) -> Preview:
        return self._preview(camera, now, None, "unavailable", reason, [])

    def _preview(
        self,
        camera: str,
        now: datetime,
        row: Row | None,
        state: policy.State,
        reason: PreviewReason | None,
        images: list[PreviewImage],
    ) -> Preview:
        timing = row.timing if row is not None else None
        self.metrics.pointers.labels(camera, state).inc()
        return Preview(
            camera=camera,  # type: ignore[arg-type]
            generated_at=now,
            state=state,
            reason=reason,
            refresh_seconds=policy.REFRESH_SECONDS,
            fresh_seconds=int(policy.FRESH.total_seconds()),
            stale_seconds=int(policy.STALE.total_seconds()),
            minimum_delay_seconds=int(policy.MIN_DELAY.total_seconds()),
            skew_margin_seconds=self.skew_margin_seconds,
            generation=row.generation if row is not None else None,
            source_interval=PreviewInterval(start=timing.captured_lower, end=timing.captured_upper)
            if timing is not None
            else None,
            exposure_upper_bound=timing.captured_upper if timing is not None else None,
            public_not_before=timing.public_not_before if timing is not None else None,
            first_published_at=timing.first_published_at if timing is not None else None,
            expires_at=timing.expires_at if timing is not None else None,
            age_seconds=(now - timing.captured_lower).total_seconds() if timing else None,
            images=images,
        )

    async def pointer(self, camera: str) -> Preview:
        now, rows = await self.source.latest(camera)
        rows = [row for row in rows if self._servable(row, now, "pointer")]
        capture = {(r.generation, r.timing.captured_lower, r.timing.captured_upper) for r in rows}
        if len(capture) != 1:
            return self._response(camera, now, "no_preview")
        state = policy.state(rows[0].timing.captured_lower, now)
        if state == "unavailable":
            return self._response(camera, now, "too_old")
        rows = sorted(rows, key=lambda r: r.variant)
        # Both sizes at once, so a stalled share costs one read deadline, not one per size.
        found = await asyncio.gather(
            *(self.files.read(r.path, r.bytes, r.sha256) for r in rows), return_exceptions=True
        )
        if any(data is None or isinstance(data, BaseException) for data in found):
            return self._response(camera, now, "media_unavailable")
        images = []
        for row in rows:
            images.append(
                PreviewImage(
                    variant=row.variant,
                    url=f"/media/previews/{row.id}",
                    content_type="image/jpeg",
                    width=row.width,
                    height=row.height,
                    bytes=row.bytes,
                )
            )
        return self._preview(camera, now, rows[0], state, None, images)


def _problem(status: int, code: str, message: str) -> JSONResponse:
    headers = dict(NO_STORE)
    if status == 503:
        headers["Retry-After"] = "5"
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
        content=Problem(code=code, message=message).model_dump(mode="json"),
    )


ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": Problem, "description": "No such camera."},
    503: {"model": Problem, "description": "The preview catalog is temporarily unavailable."},
}


@router.head("/api/v1/cameras/{camera}/preview", include_in_schema=False)
@router.get(
    "/api/v1/cameras/{camera}/preview",
    response_model=Preview,
    operation_id="getCameraPreview",
    responses=ERRORS,
)
async def get_preview(request: Request, camera: str) -> Any:
    service: PreviewService | None = getattr(request.app.state, "previews", None)
    if camera not in CAMERAS:
        return _problem(404, "not_found", "No such camera.")
    if service is None:
        return _problem(503, "unavailable", "Previews are temporarily unavailable.")
    try:
        preview = await service.pointer(camera)
    except Unavailable:
        return _problem(503, "unavailable", "Previews are temporarily unavailable.")
    return JSONResponse(preview.model_dump(mode="json"), headers=NO_STORE)


def preview_routes(resolve: Callable[[UUID], Awaitable[Representation | None]]) -> list[Any]:
    return [uuid_route("preview", "/media/previews/", "", resolve)]


def build_preview_app(service: PreviewService | None, origin: MediaOrigin | None) -> FastAPI:
    from weather.api import MediaDispatch

    app = FastAPI(title="Weather previews", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.previews = service
    app.state.media = origin

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        # Never touches the database or the share: an outage makes previews unavailable,
        # never restarts or unreadies the pod.
        return {"status": "ok", "service": "weather-preview"}

    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return _problem(400, "invalid_request", "Invalid request parameters.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        # 404 and 405 from routing: the same no-store problem shape as every other answer.
        response = _problem(
            error.status_code,
            "not_found" if error.status_code == 404 else "method_not_allowed",
            "No such resource." if error.status_code == 404 else "Use GET or HEAD.",
        )
        if error.status_code == 405:
            response.headers["Allow"] = "GET, HEAD"
        return response

    app.add_middleware(MediaDispatch, state=app.state)
    return app


def create_preview_app(
    registry: CollectorRegistry = REGISTRY, environ: dict[str, str] | None = None
) -> FastAPI:
    from weather.db.connection import DatabaseUnavailable, create_database_engine, wait_for_database

    env = dict(os.environ if environ is None else environ)
    root = Path(env.get("WEATHER_PREVIEW_ROOT", "/previews"))
    skew = int(env.get("WEATHER_PREVIEW_SKEW_SECONDS", "30"))
    if not root.is_absolute() or skew < int(policy.DEFAULT_SKEW.total_seconds()):
        raise ValueError("Invalid preview configuration")
    engine = create_database_engine(
        pool_size=4,
        pool_timeout=2,
        server_settings={
            "application_name": "weather-preview",
            "default_transaction_read_only": "on",
            "statement_timeout": "5000",
            "idle_in_transaction_session_timeout": "15000",
        },
    )
    metrics = PreviewMetrics(registry)
    files = Files(root, metrics)
    service = PreviewService(Source(engine), files, metrics, skew_margin_seconds=skew)
    origin = MediaOrigin(preview_routes(service.resolve), FileBody(), MediaMetrics(registry))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await wait_for_database(engine)
        except DatabaseUnavailable:
            LOG.warning("Database unreachable at startup; previews answer 503 until it does")
        try:
            yield
        finally:
            files.close()
            await engine.dispose()

    app = build_preview_app(service, origin)
    app.router.lifespan_context = lifespan
    return app


def serve() -> None:
    """`weather preview`: metrics on METRICS_PORT, the app on PORT."""
    import uvicorn
    from prometheus_client import start_http_server

    configure("preview")
    port = int(os.environ.get("PORT", "8000"))
    metrics_port = int(os.environ.get("METRICS_PORT", "9000"))
    if port == metrics_port:
        raise SystemExit(2)
    start_http_server(metrics_port)
    uvicorn.run(create_preview_app(), host="0.0.0.0", port=port, access_log=False)
