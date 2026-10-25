# SPDX-License-Identifier: MIT
"""Media origin HTTP semantics without a database or store (#20): ranges, denials, streaming."""

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from weather.api import build_app
from weather.media.catalog import (
    LATEST_SOURCES,
    download_name,
    latest_representation,
    media_routes,
    representation,
)
from weather.media.origin import (
    MediaMetrics,
    MediaOrigin,
    RangeError,
    Representation,
    Unavailable,
    Unsatisfiable,
    byte_range,
)

DATA = bytes((i * 7 + 3) % 251 for i in range(100))
SHA = hashlib.sha256(DATA).hexdigest()
ETAG = f'"{SHA}"'
ASSET, VIDEO, BROKEN = uuid4(), uuid4(), uuid4()
MEDIA = Representation(
    locator=f"video/{SHA}.mp4", length=len(DATA), sha256=SHA, content_type="video/mp4"
)
NATIVE = Representation(
    locator=MEDIA.locator,
    length=len(DATA),
    sha256=SHA,
    content_type="video/mp4",
    filename="weather-skycam_east-2026-10-01-daytime-native.mp4",
)


class FakeBody:
    def __init__(
        self,
        data: bytes = DATA,
        *,
        chunk: int = 16,
        fail_after: int | None = None,
        open_error: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.data, self.chunk, self.fail_after = data, chunk, fail_after
        self.open_error, self.delay = open_error, delay
        self.opened: list[tuple[int, int]] = []
        self.closed: list[float] = []

    async def _chunks(self, first: int, last: int) -> AsyncIterator[bytes]:
        sent = 0
        for offset in range(first, last + 1, self.chunk):
            if self.fail_after is not None and sent >= self.fail_after:
                raise Unavailable("store")
            if self.delay:
                await asyncio.sleep(self.delay)
            piece = self.data[offset : min(offset + self.chunk, last + 1)]
            sent += len(piece)
            yield piece

    @asynccontextmanager
    async def open(
        self, representation: Representation, first: int, last: int
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        if self.open_error:
            raise Unavailable("store")
        self.opened.append((first, last))
        try:
            yield self._chunks(first, last)
        finally:
            self.closed.append(time.monotonic())


class Resolver:
    def __init__(self, media: Representation = MEDIA) -> None:
        self.calls = 0
        self.down = False
        self.media = media

    async def asset(self, identity: UUID) -> Representation | None:
        self.calls += 1
        if self.down or identity == BROKEN:
            raise Unavailable("catalog")
        return self.media if identity == ASSET else None

    async def download(self, identity: UUID) -> Representation | None:
        self.calls += 1
        if self.down:
            raise Unavailable("catalog")
        return NATIVE if identity == VIDEO else None


@dataclass
class Result:
    status: int = 0
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)
    body: bytes = b""
    complete: bool = False
    seconds: float = 0.0

    def header(self, name: str) -> str | None:
        values = [v.decode() for k, v in self.headers if k == name.encode()]
        return values[0] if values else None


def origin_for(
    body: FakeBody | None = None, resolver: Resolver | None = None, **options: Any
) -> tuple[MediaOrigin, Resolver, CollectorRegistry]:
    resolver = resolver or Resolver()
    registry = CollectorRegistry()
    metrics = MediaMetrics(registry)
    origin = MediaOrigin(
        media_routes(resolver.asset, resolver.download),
        FakeBody() if body is None else body,
        metrics,
        **options,
    )
    return origin, resolver, registry


async def call(
    origin: MediaOrigin,
    raw_path: str,
    method: str = "GET",
    headers: Iterable[tuple[str, str]] = (),
    *,
    disconnect_after: int | None = None,
    stall_after: int | None = None,
) -> Result:
    result = Result()
    gone = asyncio.Event()
    stalled = asyncio.Event()
    requested = False

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        if message["type"] == "http.response.start":
            result.status, result.headers = message["status"], list(message["headers"])
            return
        if stall_after is not None and len(result.body) >= stall_after:
            await stalled.wait()
        result.body += message.get("body", b"")
        if not message.get("more_body", False):
            result.complete = True
        if disconnect_after is not None and len(result.body) >= disconnect_after:
            gone.set()

    scope = {
        "type": "http",
        "method": method,
        "path": unquote(raw_path),
        "raw_path": raw_path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    started = time.monotonic()
    await origin(scope, receive, send)
    result.seconds = time.monotonic() - started
    return result


def run(origin: MediaOrigin, path: str, method: str = "GET", **headers: str) -> Result:
    pairs = [(name.replace("_", "-"), value) for name, value in headers.items()]
    return asyncio.run(call(origin, path, method, pairs))


def sample(registry: CollectorRegistry, name: str, **labels: str) -> float:
    """The sum over every series of `name` that carries these labels."""
    return sum(
        float(item.value)
        for metric in registry.collect()
        for item in metric.samples
        if item.name == name and all(item.labels.get(k) == v for k, v in labels.items())
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=90-999", (90, 99)),
        ("bytes=-200", (0, 99)),
        ("bytes=-10", (90, 99)),
        ("bytes=0-", (0, 99)),
        ("bytes=99-99", (99, 99)),
        ("Bytes= 5-9 ", (5, 9)),
        ("bytes=0-99999999999999999999999999999999", (0, 99)),
        ("items=0-1", None),
        ("none=garbage", None),
    ],
)
def test_byte_range_selects_one_span(header: str, expected: tuple[int, int] | None) -> None:
    assert byte_range([header], [], ETAG, 100) == expected


@pytest.mark.parametrize(
    "header",
    ["bytes=5-3", "bytes=0-1,3-4", "bytes=0-1,", "bytes=", "bytes=-", "bytes=a-b", "garbage",
     "bytes=1-2-3", "bytes=0-" + "9" * 33, "bytes=--1"],
)
def test_byte_range_rejects_malformed(header: str) -> None:
    with pytest.raises(RangeError):
        byte_range([header], [], ETAG, 100)


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=-0", "bytes=100-200", "bytes=500-"])
def test_byte_range_unsatisfiable(header: str) -> None:
    with pytest.raises(Unsatisfiable):
        byte_range([header], [], ETAG, 100)


def test_if_range_and_repeated_headers() -> None:
    # Only the exact strong validator honours Range; anything else serves the whole body.
    for validator in ['"other"', f"W/{ETAG}", "Sat, 24 Oct 2026 00:00:00 GMT", ETAG + " x"]:
        assert byte_range(["bytes=100-"], [validator], ETAG, 100) is None
        assert byte_range(["bytes=5-3"], [validator], ETAG, 100) is None
    assert byte_range(["bytes=0-1"], [ETAG], ETAG, 100) == (0, 1)
    assert byte_range(["bytes=0-1"], [ETAG, ETAG], ETAG, 100) is None
    with pytest.raises(RangeError):
        byte_range(["bytes=0-1", "bytes=2-3"], [], ETAG, 100)
    assert byte_range([], [ETAG], ETAG, 100) is None


def test_full_get_headers_and_bytes() -> None:
    origin, _, _ = origin_for()
    result = run(origin, f"/media/{ASSET}")
    assert result.status == 200 and result.complete and result.body == DATA
    assert result.header("content-type") == "video/mp4"
    assert result.header("content-length") == "100"
    assert result.header("etag") == ETAG
    assert result.header("accept-ranges") == "bytes"
    assert result.header("cache-control") == "no-store, no-transform"
    assert result.header("x-content-type-options") == "nosniff"
    assert result.header("content-range") is None
    assert result.header("content-disposition") is None
    assert result.header("location") is None and result.header("last-modified") is None
    # Nothing private leaves: no locator, bucket or store host in any header.
    text = b"".join(k + v for k, v in result.headers).decode()
    for secret in (MEDIA.locator, "weather-media", "garage", "s3"):
        assert secret not in text


@pytest.mark.parametrize(
    ("headers", "status", "content_range", "body"),
    [
        ({"range": "bytes=0-0"}, 206, "bytes 0-0/100", DATA[:1]),
        ({"range": "bytes=90-999"}, 206, "bytes 90-99/100", DATA[90:]),
        ({"range": "bytes=-200"}, 206, "bytes 0-99/100", DATA),
        ({"range": "bytes=-5"}, 206, "bytes 95-99/100", DATA[95:]),
        ({"range": "bytes=10-"}, 206, "bytes 10-99/100", DATA[10:]),
        ({"range": "bytes=100-"}, 416, "bytes */100", None),
        ({"range": "bytes=-0"}, 416, "bytes */100", None),
        ({"range": "bytes=5-3"}, 400, None, None),
        ({"range": "bytes=0-1,3-4"}, 400, None, None),
        ({"range": "bytes=x"}, 400, None, None),
        ({"range": "frames=0-1"}, 200, None, DATA),
        ({"range": "bytes=100-", "if_range": '"other"'}, 200, None, DATA),
        ({"range": "bytes=0-1", "if_range": f"W/{ETAG}"}, 200, None, DATA),
        ({"range": "bytes=0-1", "if_range": "Sat, 24 Oct 2026 00:00:00 GMT"}, 200, None, DATA),
        ({"range": "bytes=0-1", "if_range": ETAG}, 206, "bytes 0-1/100", DATA[:2]),
        # Preconditions other than If-Range are ignored: never 304 or 412.
        ({"if_none_match": ETAG}, 200, None, DATA),
        ({"if_none_match": "*"}, 200, None, DATA),
        ({"if_match": '"other"'}, 200, None, DATA),
        ({"if_modified_since": "Sat, 24 Oct 2099 00:00:00 GMT"}, 200, None, DATA),
        ({"if_unmodified_since": "Sat, 24 Oct 1999 00:00:00 GMT"}, 200, None, DATA),
        ({"if_none_match": ETAG, "range": "bytes=100-"}, 416, "bytes */100", None),
    ],
)
def test_get_range_matrix(
    headers: dict[str, str], status: int, content_range: str | None, body: bytes | None
) -> None:
    origin, _, _ = origin_for()
    result = run(origin, f"/media/{ASSET}", **headers)
    assert result.status == status
    assert result.header("content-range") == content_range
    if body is not None:
        assert result.body == body and result.header("content-length") == str(len(body))
        assert result.header("etag") == ETAG
    else:
        assert result.header("etag") is None and result.header("accept-ranges") is None
        assert result.header("content-type") == "application/problem+json"


@pytest.mark.parametrize(
    "headers",
    [{}, {"range": "bytes=100-"}, {"range": "bytes=0-1,2-3"}, {"range": "nonsense"},
     {"range": "bytes=0-1", "if_range": ETAG}, {"if_range": "garbage"}],
)
def test_head_ignores_range_and_never_reads_bytes(headers: dict[str, str]) -> None:
    body = FakeBody()
    origin, _, _ = origin_for(body)
    result = run(origin, f"/media/{ASSET}", "HEAD", **headers)
    assert result.status == 200 and result.body == b"" and result.complete
    assert result.header("content-length") == "100" and result.header("etag") == ETAG
    assert result.header("content-range") is None
    assert body.opened == []


def test_every_denial_is_the_same_404() -> None:
    origin, resolver, metrics = origin_for()
    upper = str(ASSET).upper()
    paths = [
        f"/media/{uuid4()}",
        f"/media/{VIDEO}",
        f"/media/{upper}",
        f"/media/{ASSET}/",
        f"/media/{ASSET}/x",
        f"/media/{ASSET}.mp4",
        f"/media/%7B{ASSET}%7D",
        f"/media/{str(ASSET)[:-1]}%{str(ASSET)[-1]}0",
        f"/media/x/../{ASSET}",
        f"/media/../media/{ASSET}",
        f"/media/%2e%2e/media/{ASSET}",
        f"/media/%252e%252e/{ASSET}",
        f"/media/{ASSET.hex}",
        "/media/",
        "/media/video/" + SHA + ".mp4",
        "/media/weather-originals/original/" + SHA + ".jpg",
        "/media/..%2fapi%2fv1",
        f"/%6dedia/{ASSET}",
        f"/media/{ASSET}%00",
        f"/downloads/{ASSET}/native",
        f"/downloads/{uuid4()}/native",
        f"/downloads/{VIDEO}",
        f"/downloads/{VIDEO}/native/",
        f"/downloads/{VIDEO}/web",
        f"/downloads/{VIDEO}/%6eative",
    ]
    reference = run(origin, f"/media/{uuid4()}")
    assert reference.status == 404 and reference.header("etag") is None
    assert reference.header("content-length") == str(len(reference.body))
    assert reference.header("content-length") != "100"
    for path in paths:
        for method, headers in (
            ("GET", {}),
            ("GET", {"range": "bytes=0-1"}),
            ("GET", {"range": "bytes=5-3"}),
            ("GET", {"if_none_match": ETAG}),
            ("HEAD", {}),
            ("HEAD", {"range": "bytes=0-1"}),
        ):
            result = run(origin, path, method, **headers)
            assert (result.status, result.headers) == (reference.status, reference.headers), path
            assert result.body == (b"" if method == "HEAD" else reference.body), path
    # Malformed paths never reach the catalog.
    assert sample(metrics, "weather_media_denials_total", reason="path") > 0
    assert resolver.calls == sample(metrics, "weather_media_denials_total", reason="ineligible")


def test_catalog_outage_is_503_without_media_headers() -> None:
    resolver = Resolver()
    resolver.down = True
    origin, _, metrics = origin_for(resolver=resolver)
    for path in (f"/media/{ASSET}", f"/downloads/{VIDEO}/native"):
        for method, headers in (("GET", {}), ("HEAD", {}), ("GET", {"range": "bytes=0-1"})):
            result = run(origin, path, method, **headers)
            assert result.status == 503 and result.body == b""
            assert result.header("retry-after") == "5"
            assert result.header("content-length") == "0"
            for name in ("etag", "content-range", "content-type", "accept-ranges"):
                assert result.header(name) is None
    assert sample(
        metrics, "weather_media_upstream_errors_total", source="catalog", phase="open"
    ) == 6


def test_store_outage_before_headers_is_503() -> None:
    origin, _, metrics = origin_for(FakeBody(open_error=True))
    result = run(origin, f"/media/{ASSET}")
    assert result.status == 503 and result.header("retry-after") == "5"
    assert result.header("etag") is None and result.body == b""
    # HEAD still answers from the catalog.
    assert run(origin, f"/media/{ASSET}", "HEAD").status == 200
    disabled, _, _ = origin_for()
    disabled._body = None
    assert run(disabled, f"/media/{ASSET}").status == 503
    assert sample(metrics, "weather_media_upstream_errors_total", source="store") == 1


def test_mid_stream_failure_aborts_without_appending() -> None:
    body = FakeBody(fail_after=48)
    origin, _, metrics = origin_for(body)
    result = run(origin, f"/media/{ASSET}")
    assert result.status == 200 and result.header("content-length") == "100"
    assert not result.complete and result.body == DATA[:48]
    assert len(body.closed) == 1
    assert sample(metrics, "weather_media_upstream_errors_total", phase="stream") == 1


def test_disconnect_cancels_upstream_promptly() -> None:
    big = bytes(1024) * 1024
    body = FakeBody(big, chunk=1024, delay=0.01)
    served = Representation(
        locator="video/x.mp4",
        length=len(big),
        sha256=hashlib.sha256(big).hexdigest(),
        content_type="video/mp4",
    )
    origin, _, metrics = origin_for(body, Resolver(served))
    result = asyncio.run(call(origin, f"/media/{ASSET}", disconnect_after=4096))
    assert not result.complete and len(result.body) < 64 * 1024
    assert result.seconds < 1 and len(body.closed) == 1
    assert sample(metrics, "weather_media_cancels_total", reason="disconnect") == 1
    assert sample(metrics, "weather_media_streams", route="media") == 0


def test_stalled_client_is_cut_off() -> None:
    body = FakeBody(chunk=10)
    origin, _, metrics = origin_for(body, write_timeout=0.2)
    result = asyncio.run(call(origin, f"/media/{ASSET}", stall_after=20))
    assert not result.complete and result.body == DATA[:20]
    assert result.seconds < 1 and len(body.closed) == 1
    assert sample(metrics, "weather_media_cancels_total", reason="stalled") == 1


def test_saturated_origin_answers_503_before_any_lookup() -> None:
    body = FakeBody(chunk=1, delay=0.01)
    origin, resolver, metrics = origin_for(body, streams=1)

    async def scenario() -> tuple[Result, Result, Result]:
        slow = asyncio.create_task(call(origin, f"/media/{ASSET}"))
        await asyncio.sleep(0.05)
        calls = resolver.calls
        refused = await call(origin, f"/media/{ASSET}")
        assert resolver.calls == calls
        first = await slow
        return first, refused, await call(origin, f"/media/{ASSET}", "HEAD")

    first, refused, after = asyncio.run(scenario())
    assert first.status == 200 and first.body == DATA
    assert refused.status == 503 and refused.header("retry-after") == "5"
    assert refused.header("etag") is None and refused.body == b""
    assert after.status == 200
    assert sample(metrics, "weather_media_denials_total", reason="overload") == 1


def test_safari_sequence_and_concatenated_ranges() -> None:
    origin, _, _ = origin_for()
    probe = run(origin, f"/media/{ASSET}", range="bytes=0-1")
    assert probe.status == 206 and probe.body == DATA[:2]
    assert probe.header("content-range") == "bytes 0-1/100"
    whole = run(origin, f"/media/{ASSET}")
    assert whole.status == 200 and hashlib.sha256(whole.body).hexdigest() == SHA
    seek = run(origin, f"/media/{ASSET}", range="bytes=90-", if_range=ETAG)
    assert seek.status == 206 and seek.body == DATA[90:]
    parts = [run(origin, f"/media/{ASSET}", range=f"bytes={a}-{b}") for a, b in
             ((0, 32), (33, 65), (66, 99))]
    assert hashlib.sha256(b"".join(p.body for p in parts)).hexdigest() == SHA


def test_download_is_an_attachment_full_or_partial() -> None:
    origin, _, _ = origin_for()
    expected = 'attachment; filename="weather-skycam_east-2026-10-01-daytime-native.mp4"'
    full = run(origin, f"/downloads/{VIDEO}/native")
    assert full.status == 200 and full.body == DATA
    assert full.header("content-disposition") == expected
    resumed = run(origin, f"/downloads/{VIDEO}/native", range="bytes=40-", if_range=ETAG)
    assert resumed.status == 206 and resumed.body == DATA[40:]
    assert resumed.header("content-disposition") == expected
    assert run(origin, f"/downloads/{VIDEO}/native", "HEAD").header(
        "content-disposition"
    ) == expected


def test_other_methods_are_refused_without_lookup() -> None:
    origin, resolver, _ = origin_for()
    for method in ("POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
        result = run(origin, f"/media/{ASSET}", method)
        assert result.status == 405 and result.header("allow") == "GET, HEAD"
        assert result.header("etag") is None
    assert resolver.calls == 0


def test_representation_and_catalog_rows_are_validated() -> None:
    for change in (
        {"content_type": "video/mp4\r\nset-cookie: x=1"},
        {"content_type": "text/html; charset=utf-8"},
        {"sha256": "A" * 64},
        {"length": 0},
        {"filename": 'x"; y'},
    ):
        values: dict[str, Any] = {
            "locator": "video/x.mp4",
            "length": 1,
            "sha256": SHA,
            "content_type": "video/mp4",
            **change,
        }
        with pytest.raises(ValueError):
            Representation(**values)
    row = {"class": "poster", "sha256": SHA, "extension": "jpg", "bytes": 10,
           "content_type": "image/jpeg"}
    served = representation(row)
    assert served is not None and served.locator == f"poster/{SHA}.jpg"
    bad: list[dict[str, Any]] = [{"class": "original"}, {"extension": "../x"},
                                 {"extension": "MP4"}, {"sha256": "../" + SHA[3:]},
                                 {"bytes": None}]
    for broken in bad:
        assert representation({**row, **broken}) is None
    video = {"camera_id": "skycam_east", "cycle_date": date(2026, 10, 1), "segment": "overnight",
             "extension": "mp4"}
    assert download_name(video) == "weather-skycam_east-2026-10-01-overnight-native.mp4"
    assert download_name({**video, "camera_id": "garage\r\nx"}) is None
    assert download_name({**video, "segment": "noon"}) is None


def test_api_dispatches_media_ahead_of_other_routes() -> None:
    origin, _, _ = origin_for()
    client = TestClient(build_app(media=origin))
    response = client.get(f"/media/{ASSET}", headers={"range": "bytes=0-9"})
    assert response.status_code == 206 and response.content == DATA[:10]
    assert client.get(f"/media/{uuid4()}").status_code == 404
    assert client.get("/api/healthz").json()["status"] == "ok"
    assert client.get(f"/downloads/{VIDEO}/native").content == DATA


# Breach detector and probe target (#49) ------------------------------------------------------

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
STILL = Representation(
    locator=f"still/{SHA}.jpg", length=len(DATA), sha256=SHA, content_type="image/jpeg",
    product="still", captured_upper=NOW - timedelta(seconds=900), resolved_at=NOW,
)
EARLY = replace(STILL, captured_upper=NOW - timedelta(seconds=599))


class Latest:
    def __init__(self, found: Representation | None = STILL) -> None:
        self.found = found
        self.kinds: list[str] = []

    async def __call__(self, kind: str) -> Representation | None:
        self.kinds.append(kind)
        return self.found


def latest_origin(
    found: Representation | None = STILL, body: FakeBody | None = None
) -> tuple[MediaOrigin, Latest, CollectorRegistry]:
    resolver, latest, registry = Resolver(), Latest(found), CollectorRegistry()
    origin = MediaOrigin(
        media_routes(resolver.asset, resolver.download, latest),
        FakeBody() if body is None else body,
        MediaMetrics(registry),
    )
    return origin, latest, registry


def breaches(registry: CollectorRegistry, **labels: str) -> float:
    return sample(registry, "weather_embargo_served_before_total", **labels)


def test_breach_counter_starts_at_zero_for_every_product() -> None:
    registry = CollectorRegistry()
    MediaMetrics(registry)
    products = {
        item.labels["product"]
        for metric in registry.collect()
        for item in metric.samples
        if item.name == "weather_embargo_served_before_total"
    }
    assert products == {"preview", "still", "keogram", "video", "poster"}
    assert breaches(registry) == 0


def test_bytes_served_inside_600_s_of_capture_count_as_a_breach() -> None:
    assert EARLY.early and not STILL.early
    assert replace(STILL, captured_upper=NOW - timedelta(seconds=600)).early is False
    assert replace(EARLY, product=None).early is False  # not checked without a product
    origin, _, registry = latest_origin(EARLY)
    # HEAD writes no body, a range is still bytes served.
    assert run(origin, "/media/latest/still", "HEAD").status == 200
    assert breaches(registry) == 0
    assert run(origin, "/media/latest/still", range="bytes=0-0").status == 206
    assert breaches(registry, product="still") == 1
    assert run(origin, "/media/latest/still").body == DATA
    assert breaches(registry, product="still") == 2 and breaches(registry) == 2


def test_no_breach_without_bytes_or_inside_the_embargo() -> None:
    # Store fails before the headers: a 503, nothing served.
    origin, _, registry = latest_origin(EARLY, FakeBody(open_error=True))
    assert run(origin, "/media/latest/still").status == 503
    # A refusal is not a breach.
    refused, _, refused_registry = latest_origin(None)
    assert run(refused, "/media/latest/still").status == 404
    # Eligible and past 600 s: served, not counted.
    served, _, served_registry = latest_origin(STILL)
    assert run(served, "/media/latest/still").status == 200
    assert breaches(registry) == breaches(refused_registry) == breaches(served_registry) == 0


def test_latest_is_a_fixed_cache_safe_head_able_target() -> None:
    origin, latest, registry = latest_origin()
    result = run(origin, "/media/latest/still")
    assert result.status == 200 and result.body == DATA and result.complete
    assert result.header("cache-control") == "no-store, no-transform"
    assert result.header("etag") == ETAG and result.header("location") is None
    head = run(origin, "/media/latest/still", "HEAD")
    assert head.status == 200 and head.body == b"" and head.header("content-length") == "100"
    # Re-resolved on every request; nothing is cached.
    assert latest.kinds == ["still", "still"]
    assert sample(registry, "weather_media_requests_total", route="latest", status="200") == 2
    # Unknown kinds, other spellings and extra segments are the same 404, with no lookup.
    for path in ("/media/latest/original", "/media/latest/", "/media/latest/still/",
                 "/media/latest/Still", "/media/latest/%73till", "/media/latest/still/x"):
        denied = run(origin, path)
        assert denied.status == 404 and denied.body == run(origin, "/media/nope").body, path
    assert latest.kinds == ["still", "still"]
    # Nothing qualifies yet: 404.
    empty, _, _ = latest_origin(None)
    assert run(empty, "/media/latest/video").status == 404
    assert run(empty, "/media/latest/video", "HEAD").status == 404
    # Asset IDs still resolve beside the fixed space.
    assert run(origin, f"/media/{ASSET}").status == 200
    assert set(LATEST_SOURCES) == {"video", "poster", "keogram", "still"}


def test_latest_row_carries_the_capture_bound() -> None:
    row = {"class": "keogram", "sha256": SHA, "extension": "png", "bytes": 10,
           "content_type": "image/png", "captured_upper": NOW - timedelta(seconds=700),
           "now": NOW}
    found = latest_representation(row, "keogram")
    assert found is not None and found.locator == f"keogram/{SHA}.png"
    assert (found.product, found.captured_upper, found.resolved_at) == (
        "keogram", NOW - timedelta(seconds=700), NOW)
    assert not found.early
    assert latest_representation(row, "still") is None
    assert latest_representation({**row, "captured_upper": None}, "keogram") is None
    assert latest_representation({**row, "class": "original"}, "keogram") is None
    # Stable bounds that reach the base tables; the public still scan stays short.
    for kind in ("video", "poster", "keogram"):
        assert "now() - interval '14 days'" in LATEST_SOURCES[kind][1]
    still = LATEST_SOURCES["still"][1]
    assert "now() - interval '2 days'" in still and "local_date >= current_date - 3" in still


def test_breach_is_counted_before_the_first_body_send() -> None:
    origin, _, registry = latest_origin(EARLY)
    seen: list[float] = []

    async def send(message: Any) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            seen.append(breaches(registry, product="still"))

    async def receive() -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "GET", "path": "/media/latest/still",
             "raw_path": b"/media/latest/still", "query_string": b"", "headers": []}
    asyncio.run(origin(scope, receive, send))
    assert seen and seen[0] == 1 and breaches(registry) == 1
