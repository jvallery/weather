# SPDX-License-Identifier: MIT
"""Same-origin media responses (#20): request-time eligibility, one byte range, bounded streams.

`MediaOrigin` is a plain ASGI app. The api mounts it for `/media/` and `/downloads/` with the
catalog resolver and the Garage body; #39's `weather-preview` reuses it with its own resolver
and a file body (C17). It never takes a key, path or URL from the caller: a route's pattern
admits exactly one canonical UUID, and the resolver turns that into a private locator.

Every GET and HEAD resolves its target before any validator, length or byte is chosen, and
nothing about the decision is cached (P8). Every denial is the same 404 bytes. A catalog or
store failure is a 503 with `Retry-After` and no media headers. Conditional requests other
than If-Range are ignored: responses are `no-store`, so a 304 or 412 would do nothing.

Memory is bounded per stream: one upstream chunk is in flight and every write waits for the
client (uvicorn's flow control). A client disconnect cancels the upstream read at once; a
client that stops reading for `write_timeout` is cut off. After the headers, any failure
aborts the connection; nothing is ever appended to media bytes.

Breach detector (#49): a representation that carries its capture upper bound and the database
clock that authorized it is checked once more as its first body byte is written. Bytes served
less than 600 s after capture increment `weather_embargo_served_before_total{product}`; that
should be impossible, so any increment pages. A refusal is never a breach.
"""

import asyncio
import json
import re
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    MutableMapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

CHUNK = 64 * 1024
STREAMS = 32
WRITE_TIMEOUT = 60.0
RETRY_AFTER = b"5"
UUID_PATTERN = rb"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
SHA256 = re.compile(r"[0-9a-f]{64}")
MIME = re.compile(r"[a-z]+/[a-z0-9][a-z0-9.+-]{0,62}")
FILENAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
# Digits beyond this are a malformed request, never an allocation.
SPAN = re.compile(r"[ \t]*([0-9]{0,32})-([0-9]{0,32})[ \t]*")
# P14's minimum delay from capture; the breach detector's own floor, independent of any
# `public_not_before` (#49).
EMBARGO_FLOOR = timedelta(seconds=600)
EMBARGO_PRODUCTS = ("preview", "still", "keogram", "video", "poster")

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class Unavailable(Exception):
    """The catalog or the byte store cannot answer now (503); `source` labels metrics."""

    def __init__(self, source: str) -> None:
        super().__init__(f"Media {source} unavailable")
        self.source = source


class RangeError(Exception):
    """Malformed, reversed or multiple byte ranges: 400."""


class Unsatisfiable(Exception):
    """A well-formed single range that selects no byte: 416."""


@dataclass(frozen=True)
class Representation:
    """What one eligible ID serves. `locator` is private and never leaves the process."""

    locator: str
    length: int
    sha256: str
    content_type: str
    filename: str | None = None
    # For the breach detector: what is served, the newest capture instant in these bytes and
    # the database clock that authorized them. A representation without them is not checked.
    product: str | None = None
    captured_upper: datetime | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.length <= 0
            or not SHA256.fullmatch(self.sha256)
            or not MIME.fullmatch(self.content_type)
            or (self.filename is not None and not FILENAME.fullmatch(self.filename))
        ):
            raise ValueError("Invalid media representation")

    @property
    def etag(self) -> str:
        return f'"{self.sha256}"'

    @property
    def early(self) -> bool:
        """Less than 600 s from capture to the authorizing clock: serving it is a breach."""
        return (
            self.product is not None
            and self.captured_upper is not None
            and self.resolved_at is not None
            and self.resolved_at - self.captured_upper < EMBARGO_FLOOR
        )


class Body(Protocol):
    """Opens bytes `first`..`last` (inclusive). Opening raises Unavailable before any byte if
    the store cannot serve exactly that span; iteration raises Unavailable on a failure."""

    def open(
        self, representation: Representation, first: int, last: int
    ) -> AbstractAsyncContextManager[AsyncIterator[bytes]]: ...


Resolve = Callable[[UUID], Awaitable[Representation | None]]


@dataclass(frozen=True)
class Route:
    """`prefix` claims a path space; `pattern` must match the whole path with one group, which
    `key` turns into what `resolve` takes (a UUID, or a fixed name)."""

    label: str
    prefix: bytes
    pattern: re.Pattern[bytes]
    resolve: Callable[[Any], Awaitable[Representation | None]]
    key: Callable[[str], object] = UUID


def byte_range(
    ranges: Sequence[str], if_range: Sequence[str], etag: str, length: int
) -> tuple[int, int] | None:
    """The one span a GET returns, or None for the whole representation (RFC 9110 §14).

    A weak, dated or other If-Range ignores Range, as does an unknown unit. More than one
    range, reversed endpoints or bad syntax raise RangeError; a range starting at or past the
    end, or `bytes=-0`, raises Unsatisfiable. An oversized end or suffix is clamped.
    """
    if not ranges:
        return None
    if if_range and (len(if_range) != 1 or if_range[0].strip() != etag):
        return None
    if len(ranges) != 1:
        raise RangeError()
    unit, separator, spec = ranges[0].partition("=")
    if not separator:
        raise RangeError()
    if unit.strip().lower() != "bytes":
        return None
    match = SPAN.fullmatch(spec)
    if match is None or not (match[1] or match[2]):
        raise RangeError()
    if match[1]:
        first = int(match[1])
        if match[2] and int(match[2]) < first:
            raise RangeError()
        if first >= length:
            raise Unsatisfiable()
        return first, min(int(match[2]) if match[2] else length - 1, length - 1)
    suffix = int(match[2])
    if suffix == 0:
        raise Unsatisfiable()
    return max(0, length - suffix), length - 1


def _problem(code: str, message: str) -> bytes:
    return json.dumps({"code": code, "message": message}, separators=(",", ":")).encode()


NOT_FOUND = _problem("not_found", "No such media.")
INVALID_RANGE = _problem("invalid_range", "Send at most one valid byte range.")
UNSATISFIABLE = _problem("range_not_satisfiable", "The range selects no bytes.")
METHOD = _problem("method_not_allowed", "Use GET or HEAD.")
BASE = [(b"cache-control", b"no-store, no-transform"), (b"x-content-type-options", b"nosniff")]


class MediaMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.requests = Counter(
            "weather_media_requests_total",
            "Media responses by route, method and status",
            ["route", "method", "status"],
            registry=registry,
        )
        self.streams = Gauge(
            "weather_media_streams", "Media requests in progress", ["route"], registry=registry
        )
        self.bytes = Counter(
            "weather_media_bytes_total", "Media body bytes written", ["route"], registry=registry
        )
        self.denials = Counter(
            "weather_media_denials_total",
            "Refused media requests by reason",
            ["route", "reason"],
            registry=registry,
        )
        self.upstream_errors = Counter(
            "weather_media_upstream_errors_total",
            "Catalog or store failures, before (open) or after (stream) the headers",
            ["route", "source", "phase"],
            registry=registry,
        )
        self.cancels = Counter(
            "weather_media_cancels_total",
            "Streams ended early by a disconnect or a stalled client",
            ["route", "reason"],
            registry=registry,
        )
        self.served_before = Counter(
            "weather_embargo_served_before_total",
            "Media bodies whose first byte was served less than 600 s after capture; always 0",
            ["product"],
            registry=registry,
        )
        for product in EMBARGO_PRODUCTS:
            self.served_before.labels(product)
        self.ttfb = Histogram(
            "weather_media_ttfb_seconds",
            "Request start to the first body byte",
            ["route"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=registry,
        )


def _paths(scope: Message) -> tuple[bytes, bytes]:
    path = scope["path"].encode("utf-8", "replace")
    return scope.get("raw_path") or path, path


def _headers(scope: Message, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope["headers"] if key.lower() == name]


class MediaOrigin:
    def __init__(
        self,
        routes: Iterable[Route],
        body: Body | None,
        metrics: MediaMetrics,
        *,
        streams: int = STREAMS,
        write_timeout: float = WRITE_TIMEOUT,
    ) -> None:
        self.routes = tuple(routes)
        self._body, self._metrics = body, metrics
        self._streams, self._write_timeout = streams, write_timeout
        self._active = 0

    def route_for(self, scope: Message) -> Route | None:
        """The route whose prefix claims this path, raw or decoded; anything under a prefix
        is answered here, so every trick under it gets the same 404."""
        raw, path = _paths(scope)
        return next(
            (r for r in self.routes if raw.startswith(r.prefix) or path.startswith(r.prefix)),
            None,
        )

    async def __call__(self, scope: Message, receive: Receive, send: Send) -> None:
        started = time.monotonic()
        route = self.route_for(scope)
        if route is None:
            raise LookupError("Not a media path")
        raw, path = _paths(scope)
        # Both spellings must be the canonical one: no escapes, dots or extra segments.
        match = route.pattern.fullmatch(raw)
        identity = route.key(match[1].decode()) if match and raw == path else None
        method = scope["method"]
        status = await self._serve(route, identity, method, scope, receive, send, started)
        label = method if method in {"GET", "HEAD"} else "other"
        self._metrics.requests.labels(route.label, label, str(status)).inc()

    async def _serve(
        self,
        route: Route,
        identity: object | None,
        method: str,
        scope: Message,
        receive: Receive,
        send: Send,
        started: float,
    ) -> int:
        metrics, label = self._metrics, route.label
        head = method == "HEAD"
        if method not in {"GET", "HEAD"}:
            metrics.denials.labels(label, "method").inc()
            return await _error(send, 405, METHOD, head, [(b"allow", b"GET, HEAD")])
        if identity is None:
            metrics.denials.labels(label, "path").inc()
            return await _error(send, 404, NOT_FOUND, head)
        if self._active >= self._streams:
            metrics.denials.labels(label, "overload").inc()
            return await _unavailable(send)
        self._active += 1
        metrics.streams.labels(label).inc()
        try:
            return await self._respond(route, identity, method, scope, receive, send, started)
        finally:
            self._active -= 1
            metrics.streams.labels(label).dec()

    async def _respond(
        self,
        route: Route,
        identity: object,
        method: str,
        scope: Message,
        receive: Receive,
        send: Send,
        started: float,
    ) -> int:
        metrics, label = self._metrics, route.label
        try:
            representation = await route.resolve(identity)
        except Unavailable as error:
            metrics.upstream_errors.labels(label, error.source, "open").inc()
            return await _unavailable(send)
        if representation is None:
            metrics.denials.labels(label, "ineligible").inc()
            return await _error(send, 404, NOT_FOUND, method == "HEAD")
        length = representation.length
        if method == "HEAD":
            # HEAD ignores Range and If-Range entirely and never touches the store.
            await send(_start(200, representation, 0, length - 1))
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return 200
        try:
            span = byte_range(
                _headers(scope, b"range"),
                _headers(scope, b"if-range"),
                representation.etag,
                length,
            )
        except RangeError:
            metrics.denials.labels(label, "range").inc()
            return await _error(send, 400, INVALID_RANGE, False)
        except Unsatisfiable:
            metrics.denials.labels(label, "unsatisfiable").inc()
            return await _error(
                send, 416, UNSATISFIABLE, False, [(b"content-range", f"bytes */{length}".encode())]
            )
        first, last = span or (0, length - 1)
        status = 200 if span is None else 206
        if self._body is None:
            metrics.upstream_errors.labels(label, "store", "open").inc()
            return await _unavailable(send)
        sent = False
        try:
            async with self._body.open(representation, first, last) as chunks:
                await send(_start(status, representation, first, last))
                sent = True
                breach = representation.product if representation.early else None
                outcome = await self._stream(chunks, receive, send, label, started, breach)
        except Unavailable as error:
            metrics.upstream_errors.labels(label, error.source, "stream" if sent else "open").inc()
            if not sent:
                return await _unavailable(send)
            # Return without completing: the server closes the connection short of its
            # Content-Length, so the client sees a failed transfer, never extra bytes.
            return status
        if outcome != "complete":
            metrics.cancels.labels(label, outcome).inc()
        return status

    async def _stream(
        self,
        chunks: AsyncIterator[bytes],
        receive: Receive,
        send: Send,
        label: str,
        started: float,
        breach: str | None = None,
    ) -> str:
        """Copy chunks until done, a disconnect or a stall; returns which one happened.
        `breach` names the product when its first byte out is an embargo breach."""

        async def copy() -> str:
            first = True
            async for chunk in chunks:
                if not chunk:
                    continue
                try:
                    async with asyncio.timeout(self._write_timeout):
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                except TimeoutError:
                    return "stalled"
                if first:
                    self._metrics.ttfb.labels(label).observe(time.monotonic() - started)
                    if breach is not None:
                        self._metrics.served_before.labels(breach).inc()
                    first = False
                self._metrics.bytes.labels(label).inc(len(chunk))
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return "complete"

        async def disconnected() -> str:
            while (await receive())["type"] != "http.disconnect":
                pass
            return "disconnect"

        pump = asyncio.create_task(copy())
        watch = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (pump, watch):
                task.cancel()
            await asyncio.wait({pump, watch})
        if pump in done:
            return pump.result()
        return "disconnect"


def _start(status: int, representation: Representation, first: int, last: int) -> Message:
    headers = [
        (b"content-type", representation.content_type.encode()),
        (b"content-length", str(last - first + 1).encode()),
        (b"etag", representation.etag.encode()),
        (b"accept-ranges", b"bytes"),
        *BASE,
    ]
    if status == 206:
        headers.append(
            (b"content-range", f"bytes {first}-{last}/{representation.length}".encode())
        )
    if representation.filename is not None:
        headers.append(
            (b"content-disposition", f'attachment; filename="{representation.filename}"'.encode())
        )
    return {"type": "http.response.start", "status": status, "headers": headers}


async def _error(
    send: Send,
    status: int,
    body: bytes,
    head: bool,
    extra: list[tuple[bytes, bytes]] | None = None,
) -> int:
    """A fixed problem document; HEAD gets the same headers and no body."""
    headers = [
        (b"content-type", b"application/problem+json"),
        (b"content-length", str(len(body)).encode()),
        *BASE,
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else body, "more_body": False})
    return status


async def _unavailable(send: Send) -> int:
    """503 with no media headers and no body."""
    headers = [(b"content-length", b"0"), (b"retry-after", RETRY_AFTER), *BASE]
    await send({"type": "http.response.start", "status": 503, "headers": headers})
    await send({"type": "http.response.body", "body": b"", "more_body": False})
    return 503


def uuid_route(label: str, prefix: str, suffix: str, resolve: Resolve) -> Route:
    """A route for `<prefix><uuid><suffix>`: `/media/` + id, or `/downloads/` + id + `/native`."""
    raw = prefix.encode()
    pattern = re.compile(re.escape(raw) + UUID_PATTERN + re.escape(suffix.encode()))
    return Route(label, raw, pattern, resolve)


def named_route(
    label: str,
    prefix: str,
    names: Iterable[str],
    resolve: Callable[[str], Awaitable[Representation | None]],
) -> Route:
    """A route for `<prefix><name>` over a fixed vocabulary, such as `/media/latest/still`."""
    raw = prefix.encode()
    alternatives = b"|".join(re.escape(name.encode()) for name in sorted(names))
    pattern = re.compile(re.escape(raw) + b"(" + alternatives + b")")
    return Route(label, raw, pattern, resolve, str)
