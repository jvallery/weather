# SPDX-License-Identifier: MIT
"""The media origin against real PostgreSQL, Garage and uvicorn (#20): eligibility, ranges,
downloads, a 1 GiB slow client, disconnects, mid-stream and dependency failures.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from prometheus_client import CollectorRegistry
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.api import build_app
from weather.api.timelapses import Catalog, DatabaseSource, TimelapseCursor
from weather.db.models import metadata
from weather.media.catalog import CatalogResolver, media_routes
from weather.media.garage import GarageBody
from weather.media.origin import MediaMetrics, MediaOrigin, Representation
from weather.publish import catalog
from weather.publish.model import Product, ProductFile
from weather.publish.objects import ObjectStore
from weather.publish.publisher import Publisher

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)
END = NOW - timedelta(hours=3)
DEADLINE = NOW + timedelta(hours=2)
GARAGE = "http://127.0.0.1:3900"
KEY = b"k" * 64
MIB = 1024 * 1024
GIB = 1024 * MIB


def engine(role: str = "postgres", *, host: str | None = None) -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Integration requires fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket_dir = Path(str(url.query.get("host", "")))
    if url.database != "weather_ci" or not socket_dir.parent.name.startswith("weather-schema-ci."):
        pytest.fail("Integration requires its disposable socket-only database")
    if host is not None:
        url = url.update_query_dict({"host": host})
    return create_async_engine(url, echo=False, pool_size=4, max_overflow=0, pool_timeout=2)


def store(
    bucket: str = "weather-media", *, endpoint: str = GARAGE, part: int = 5 * MIB
) -> ObjectStore:
    if os.environ["WEATHER_CI_S3_ENDPOINT"] != GARAGE or not endpoint.startswith(
        "http://127.0.0.1:"
    ):
        pytest.fail("Integration requires its disposable loopback Garage")
    return ObjectStore(
        endpoint=endpoint,
        bucket=bucket,
        access_key=os.environ["WEATHER_CI_S3_ACCESS_KEY"],
        secret_key=os.environ["WEATHER_CI_S3_SECRET_KEY"],
        part_size=part,
        multipart_threshold=part,
    )


@pytest.fixture(autouse=True)
def clean_test_database() -> Iterator[None]:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await connection.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
        finally:
            await db.dispose()

    asyncio.run(clean())
    yield


# ---- an in-process uvicorn on loopback, with a raw HTTP/1.1 client --------------------------


class Recording:
    """Wraps the Garage body and records when each upstream span is closed."""

    def __init__(self, inner: GarageBody) -> None:
        self.inner = inner
        self.closed: list[float] = []

    @asynccontextmanager
    async def open(
        self, representation: Representation, first: int, last: int
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        try:
            async with self.inner.open(representation, first, last) as chunks:
                yield chunks
        finally:
            self.closed.append(time.monotonic())


@dataclass
class Running:
    port: int
    body: Recording | None = None
    loop: asyncio.AbstractEventLoop | None = None
    extra: dict[str, Any] = field(default_factory=dict)


Setup = Callable[[Running], Awaitable[None]]


@contextmanager
def serving(
    *,
    endpoint: str = GARAGE,
    database_host: str | None = None,
    timeout_ms: int = 1500,
    setup: Setup | None = None,
) -> Iterator[Running]:
    """The api app (catalog plus media origin) on a free loopback port, in its own loop."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    running = Running(port=listener.getsockname()[1])
    ready, failed = threading.Event(), []
    holder: dict[str, uvicorn.Server] = {}

    async def main() -> None:
        running.loop = asyncio.get_running_loop()
        if setup is not None:
            await setup(running)
        api = engine("weather_api", host=database_host)
        media = store(endpoint=running.extra.get("endpoint", endpoint))
        running.body = Recording(GarageBody(media))
        resolver = CatalogResolver(api, timeout_ms=timeout_ms)
        origin = MediaOrigin(
            media_routes(resolver.asset, resolver.download),
            running.body,
            MediaMetrics(CollectorRegistry()),
        )
        app = build_app(media=origin)
        app.state.timelapses = Catalog(DatabaseSource(api), TimelapseCursor(KEY))
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", http="h11", log_level="warning")
        )
        holder["server"] = server
        task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started and not task.done():
            await asyncio.sleep(0.01)
        ready.set()
        try:
            await task
        finally:
            await media.aclose()
            await api.dispose()

    def thread_main() -> None:
        try:
            asyncio.run(main())
        except BaseException as error:  # surfaced to the test below
            failed.append(error)
            ready.set()

    thread = threading.Thread(target=thread_main, daemon=True)
    thread.start()
    assert ready.wait(30) and not failed, failed
    try:
        yield running
    finally:
        if "server" in holder:
            holder["server"].should_exit = True
        thread.join(30)
        listener.close()


@dataclass
class Reply:
    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> str | None:
        values = [value for key, value in self.headers if key == name]
        return values[0] if values else None

    def stable(self) -> tuple[int, list[tuple[str, str]], bytes]:
        """Everything but the per-second Date header."""
        return self.status, [h for h in self.headers if h[0] != "date"], self.body


def _send(port: int, method: str, target: str, headers: dict[str, str]) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=60)
    lines = [f"{method} {target} HTTP/1.1", "Host: weather.vallery.net", "Connection: close"]
    lines += [f"{name}: {value}" for name, value in headers.items()]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    return sock


def _head(sock: socket.socket) -> tuple[int, list[tuple[str, str]], bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("Connection closed before the headers")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status, *lines = head.decode("latin-1").split("\r\n")
    headers = [(k.strip().lower(), v.strip()) for k, _, v in (x.partition(":") for x in lines)]
    return int(status.split()[1]), headers, rest


def http(port: int, target: str, method: str = "GET", **headers: str) -> Reply:
    """One request with an exact target (no client normalization), read until close."""
    named = {name.replace("_", "-"): value for name, value in headers.items()}
    with _send(port, method, target, named) as sock:
        status, parsed, body = _head(sock)
        with suppress(ConnectionResetError):
            while chunk := sock.recv(65536):
                body += chunk
    return Reply(status, parsed, body)


def assert_media(reply: Reply, row: dict[str, Any], status: int = 200) -> None:
    assert reply.status == status
    assert reply.header("etag") == f'"{row["sha256"]}"'
    assert reply.header("content-type") == row["content_type"]
    assert reply.header("accept-ranges") == "bytes"
    assert reply.header("cache-control") == "no-store, no-transform"
    assert reply.header("x-content-type-options") == "nosniff"
    assert reply.header("location") is None
    private = ("weather-media", "weather-originals", "127.0.0.1:", "garage", row["object_key"])
    for _, value in reply.headers:
        assert not any(word in value for word in private), value


def assert_unavailable(reply: Reply) -> None:
    assert reply.status == 503 and reply.body == b""
    assert reply.header("retry-after") == "5" and reply.header("content-length") == "0"
    for name in ("etag", "content-range", "content-type", "accept-ranges"):
        assert reply.header(name) is None


# ---- catalog fixtures -----------------------------------------------------------------------


def write(directory: Path, name: str, size: int) -> Path:
    path = directory / name
    path.write_bytes(secrets.token_bytes(size))
    return path


def faststart_mp4(directory: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("Integration requires the pinned FFmpeg")
    path = directory / "native.mp4"
    subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc2=duration=3:size=320x240:rate=15", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(path)],
        check=True,
        timeout=120,
    )
    data = path.read_bytes()
    assert 0 < data.find(b"moov") < data.find(b"mdat")
    return path


@dataclass
class Seeded:
    video_id: UUID
    cycle_date: date
    video: dict[str, UUID]
    still: dict[str, UUID]
    keogram: dict[str, UUID]


async def seed(directory: Path) -> Seeded:
    db = engine("weather_worker")
    buckets = {b: store(b) for b in ("weather-media", "weather-originals")}
    publisher = Publisher(db, buckets)
    day = (END - timedelta(hours=10)).date()
    try:
        video = await publisher.publish(
            Product.video(
                camera_id="skycam_east",
                cycle_date=day,
                segment="daytime",
                starts_at=END - timedelta(hours=10),
                ends_at=END,
                frames=4200,
                public_not_before=END + timedelta(seconds=630),
                state="published",
                reason=None,
                files=(
                    ProductFile(role="native", extension="mp4", path=faststart_mp4(directory),
                                width=320, height=240),
                    ProductFile(role="web", extension="mp4",
                                path=write(directory, "web.mp4", 200_000), width=320, height=240),
                    ProductFile(role="mobile", extension="mp4",
                                path=write(directory, "mobile.mp4", 100_000), width=320,
                                height=240),
                    ProductFile(role="poster", extension="jpg",
                                path=write(directory, "poster.jpg", 30_000), width=320,
                                height=240),
                    ProductFile.sidecar({"schema_version": 1, "segment": "daytime",
                                         "camera": "skycam_east"}),
                ),
            ),
            expected_revision=0,
            deadline=DEADLINE,
        )
        slot = END.replace(minute=0, second=0)
        still = await publisher.publish(
            Product.still(
                camera_id="skycam_west",
                slot_start=slot,
                source_start=slot + timedelta(minutes=29),
                source_end=slot + timedelta(minutes=30),
                public_not_before=slot + timedelta(minutes=30, seconds=630),
                files=(
                    ProductFile(role="original", extension="jpg",
                                path=write(directory, "original.jpg", 40_000)),
                    ProductFile(role="still", extension="jpg",
                                path=write(directory, "still.jpg", 20_000), width=320,
                                height=240),
                ),
            ),
            expected_revision=0,
            deadline=DEADLINE,
        )
        keogram = await publisher.publish(
            Product.keogram(
                camera_id="skycam_east",
                cycle_date=day,
                segment="daytime",
                source_start=END - timedelta(hours=10),
                source_end=END,
                public_not_before=END + timedelta(seconds=630),
                files=(
                    ProductFile(role="keogram", extension="png",
                                path=write(directory, "keogram.png", 10_000), width=1440,
                                height=512),
                ),
            ),
            expected_revision=0,
            deadline=DEADLINE,
        )
    finally:
        for each in buckets.values():
            await each.aclose()
        await db.dispose()
    assert video.video_id is not None
    return Seeded(video.video_id, day, video.assets, still.assets, keogram.assets)


async def rows() -> dict[UUID, dict[str, Any]]:
    db = engine()
    try:
        async with db.connect() as connection:
            result = await connection.execute(text("SELECT * FROM weather.media_asset"))
            return {row.id: dict(row._mapping) for row in result}
    finally:
        await db.dispose()


async def put_asset(
    data_or_path: bytes | Path,
    *,
    asset_class: str = "poster",
    extension: str = "jpg",
    content_type: str = "image/jpeg",
    embargo: str = "clock_timestamp() - interval '1 hour'",
    published: str = "clock_timestamp() - interval '1 hour'",
    state: str = "available",
    sha256: str | None = None,
    size: int | None = None,
) -> UUID:
    """Upload bytes and write a catalog row directly, for cases the publisher never makes."""
    if isinstance(data_or_path, Path):
        assert sha256 is not None and size is not None
        path, data = data_or_path, None
    else:
        path, data = None, data_or_path
        sha256, size = hashlib.sha256(data).hexdigest(), len(data)
    key = f"{asset_class}/{sha256}.{extension}"
    media = store(part=8 * MIB)
    try:
        await media.put(key, sha256=sha256, size=size, content_type=content_type, path=path,
                        data=data)
    finally:
        await media.aclose()
    db = engine()
    try:
        async with db.begin() as connection:
            return UUID(str((await connection.execute(text(f"""
                INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,
                  object_key,bytes,content_type,public_not_before,published_at,state)
                VALUES (:class,'weather-media','public',:sha,:ext,:key,:bytes,:mime,
                  {embargo},{published},:state) RETURNING id
            """), {"class": asset_class, "sha": sha256, "ext": extension, "key": key,
                   "bytes": size, "mime": content_type, "state": state})).scalar_one()))
    finally:
        await db.dispose()


async def withdraw(target: UUID) -> None:
    db = engine("weather_worker")
    try:
        async with db.begin() as connection:
            await catalog.withdraw(connection, target, reason="test withdrawal", actor="test")
    finally:
        await db.dispose()


# ---- tests ----------------------------------------------------------------------------------


def test_eligibility_matrix(tmp_path: Path) -> None:
    seeded = asyncio.run(seed(tmp_path))
    unpublished = asyncio.run(
        put_asset(secrets.token_bytes(500), published="clock_timestamp() + interval '1 day'")
    )
    unavailable = asyncio.run(put_asset(secrets.token_bytes(500), state="unavailable"))
    soon = asyncio.run(put_asset(secrets.token_bytes(500), embargo="clock_timestamp() + "
                                 "interval '15 seconds'"))
    catalog_rows = asyncio.run(rows())
    public = [*seeded.video.values(), seeded.still["still"], seeded.keogram["keogram"]]
    original = seeded.still["original"]
    native = catalog_rows[seeded.video["native"]]
    with serving() as running:
        port = running.port
        reference = http(port, f"/media/{uuid4()}")
        assert reference.status == 404 and reference.header("etag") is None
        assert reference.header("content-length") == str(len(reference.body))
        # Still embargoed by the database clock: the same 404 as an unknown ID.
        for method in ("GET", "HEAD"):
            assert http(port, f"/media/{soon}", method).stable()[:2] == reference.stable()[:2]
        for asset_id in public:
            row = catalog_rows[asset_id]
            reply = http(port, f"/media/{asset_id}")
            assert_media(reply, row)
            assert reply.header("content-length") == str(row["bytes"])
            assert hashlib.sha256(reply.body).hexdigest() == row["sha256"]
            head = http(port, f"/media/{asset_id}", "HEAD", range="bytes=0-1")
            assert head.status == 200 and head.body == b""
            assert head.header("content-length") == str(row["bytes"])
            assert head.header("content-range") is None
        denied = [
            f"/media/{uuid4()}",
            f"/media/{original}",
            f"/media/{unpublished}",
            f"/media/{unavailable}",
            f"/media/{soon}",
            f"/media/{seeded.video_id}",
            f"/media/{str(seeded.video['poster']).upper()}",
            f"/media/{native['object_key']}",
            f"/media/weather-originals/{catalog_rows[original]['object_key']}",
            f"/media/../media/{seeded.video['poster']}",
            f"/media/%2e%2e/media/{seeded.video['poster']}",
            f"/media/%252e%252e/{seeded.video['poster']}",
            f"/media/{seeded.video['poster']}/..",
            f"/media/{seeded.video['poster']}%2f",
            f"/downloads/{seeded.video['native']}/native",
            f"/downloads/{seeded.video['poster']}/native",
            f"/downloads/{uuid4()}/native",
            f"/downloads/{seeded.video_id}/web",
            f"/downloads/{seeded.video_id}/%6eative",
        ]
        for target in denied:
            for method, headers in (
                ("GET", {}),
                ("GET", {"range": "bytes=0-1"}),
                ("GET", {"range": "bytes=5-3"}),
                ("HEAD", {}),
            ):
                reply = http(port, target, method, **headers)
                assert reply.stable()[:2] == reference.stable()[:2], (target, method)
                assert reply.body == (b"" if method == "HEAD" else reference.body), target
        # The embargo is checked with the database clock at request time; equality serves.
        async def embargo_passed() -> bool:
            db = engine()
            try:
                async with db.connect() as connection:
                    return bool((await connection.execute(text(
                        "SELECT public_not_before <= clock_timestamp() FROM weather.media_asset "
                        "WHERE id=:id"), {"id": soon})).scalar_one())
            finally:
                await db.dispose()

        deadline = time.monotonic() + 30
        while not asyncio.run(embargo_passed()):
            assert time.monotonic() < deadline
            time.sleep(0.2)
        reply = http(port, f"/media/{soon}")
        assert reply.status == 200
        assert asyncio.run(view_uses_inclusive_embargo())
        # Withdrawal after a 200 gives the same 404 on the next request.
        poster = seeded.video["poster"]
        assert http(port, f"/media/{poster}").status == 200
        asyncio.run(withdraw(poster))
        assert http(port, f"/media/{poster}").stable()[:2] == reference.stable()[:2]
        assert http(port, f"/media/{poster}").body == reference.body


async def view_uses_inclusive_embargo() -> bool:
    db = engine()
    try:
        async with db.connect() as connection:
            definition = (await connection.execute(
                text("SELECT pg_get_viewdef('public_api.assets'::regclass)"))).scalar_one()
    finally:
        await db.dispose()
    flat = " ".join(str(definition).split())
    return re.search(r"(\w+\.)?public_not_before <= clock_timestamp\(\)", flat) is not None


def test_ranges_downloads_and_safari_sequence(tmp_path: Path) -> None:
    seeded = asyncio.run(seed(tmp_path))
    native = asyncio.run(rows())[seeded.video["native"]]
    length, etag = native["bytes"], f'"{native["sha256"]}"'
    media = f"/media/{seeded.video['native']}"
    download = f"/downloads/{seeded.video_id}/native"
    name = f"weather-skycam_east-{seeded.cycle_date.isoformat()}-daytime-native.mp4"
    with serving() as running:
        port = running.port
        # Safari: a two-byte probe, the whole file, then a seek near the end.
        probe = http(port, media, range="bytes=0-1")
        assert_media(probe, native, 206)
        assert probe.header("content-range") == f"bytes 0-1/{length}"
        assert probe.header("content-length") == "2" and len(probe.body) == 2
        whole = http(port, media)
        assert_media(whole, native)
        assert hashlib.sha256(whole.body).hexdigest() == native["sha256"]
        assert whole.body[:2] == probe.body
        assert 0 < whole.body.find(b"moov") < whole.body.find(b"mdat")
        seek = http(port, media, range=f"bytes={length - 1000}-", if_range=etag)
        assert seek.status == 206 and seek.body == whole.body[-1000:]
        assert seek.header("content-range") == f"bytes {length - 1000}-{length - 1}/{length}"
        # Concatenated ranges are the catalog bytes.
        cuts = [0, length // 3, 2 * length // 3, length]
        spans = zip(cuts, cuts[1:], strict=False)
        parts = [http(port, media, range=f"bytes={a}-{b - 1}") for a, b in spans]
        assert all(p.status == 206 for p in parts)
        assert hashlib.sha256(b"".join(p.body for p in parts)).hexdigest() == native["sha256"]
        # Edges.
        assert http(port, media, range=f"bytes=0-{length * 10}").body == whole.body
        assert http(port, media, range=f"bytes=-{length * 10}").status == 206
        for header in (f"bytes={length}-", "bytes=-0"):
            reply = http(port, media, range=header)
            assert reply.status == 416 and reply.header("content-range") == f"bytes */{length}"
        for header in ("bytes=9-3", "bytes=0-1,4-5", "bytes=x-"):
            assert http(port, media, range=header).status == 400
        assert http(port, media, range="frames=0-1").status == 200
        for validator in ('"stale"', f"W/{etag}", "Sat, 24 Oct 2026 00:00:00 GMT"):
            reply = http(port, media, range="bytes=0-1", if_range=validator)
            assert reply.status == 200 and len(reply.body) == length
        for name_, value in (("if_none_match", etag), ("if_match", '"x"'),
                             ("if_modified_since", "Sat, 24 Oct 2099 00:00:00 GMT"),
                             ("if_unmodified_since", "Sat, 24 Oct 1999 00:00:00 GMT")):
            assert http(port, media, **{name_: value}).status == 200
        # The download is the native rendition, whole or resumed, always an attachment.
        full = http(port, download)
        assert_media(full, native)
        assert full.body == whole.body
        disposition = f'attachment; filename="{name}"'
        assert full.header("content-disposition") == disposition
        start = http(port, download, range="bytes=0-4095")
        resumed = http(port, download, range="bytes=4096-", if_range=etag)
        assert resumed.status == 206 and resumed.header("content-disposition") == disposition
        assert start.body + resumed.body == whole.body
        # Withdrawing the video removes both routes on the next request.
        asyncio.run(withdraw(seeded.video_id))
        assert http(port, download).status == 404
        assert http(port, media).status == 404
        assert http(port, download, range="bytes=4096-", if_range=etag).status == 404


def _rss() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise AssertionError("No VmRSS")


def test_one_gib_slow_client_memory_and_disconnect(tmp_path: Path) -> None:
    # A sparse file: no disk, and Garage stores its identical blocks once.
    fixture = tmp_path / "large.mp4"
    with fixture.open("wb") as output:
        output.truncate(GIB)
    digest = hashlib.sha256()
    with fixture.open("rb") as handle:
        while block := handle.read(8 * MIB):
            digest.update(block)
    sha256 = digest.hexdigest()
    asset = asyncio.run(put_asset(fixture, asset_class="video", extension="mp4",
                                  content_type="video/mp4", sha256=sha256, size=GIB))
    with serving() as running:
        assert running.body is not None
        peak, stop = [0], threading.Event()

        def sample() -> None:
            while not stop.is_set():
                peak[0] = max(peak[0], _rss())
                time.sleep(0.02)

        baseline = _rss()
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        sock = _send(running.port, "GET", f"/media/{asset}", {})
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        try:
            status, headers, body = _head(sock)
            assert status == 200 and dict(headers)["content-length"] == str(GIB)
            received, digest = len(body), hashlib.sha256(body)
            buffer = memoryview(bytearray(256 * 1024))
            began, stalled = time.monotonic(), False
            while received < GIB:
                count = sock.recv_into(buffer)
                if not count:
                    break
                digest.update(buffer[:count])
                received += count
                if not stalled and received > 256 * MIB:
                    # A reader that stops: the origin must wait, not buffer.
                    stalled = True
                    time.sleep(3)
                # About 200 MiB/s: far slower than loopback Garage.
                ahead = received / (200 * MIB) - (time.monotonic() - began)
                if ahead > 0:
                    time.sleep(ahead)
        finally:
            sock.close()
            stop.set()
            sampler.join()
        assert received == GIB and digest.hexdigest() == sha256
        assert peak[0] - baseline < 64 * MIB, (peak[0] - baseline) // MIB

        # Disconnect mid-stream: the upstream span closes within a second.
        before = len(running.body.closed)
        sock = _send(running.port, "GET", f"/media/{asset}", {})
        status, _, body = _head(sock)
        assert status == 200
        received = len(body)
        while received < 4 * MIB:
            received += len(sock.recv(65536))
        # Reset rather than a graceful close, like a browser abandoning a request.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        closed_at = time.monotonic()
        while len(running.body.closed) == before:
            assert time.monotonic() - closed_at < 5
            time.sleep(0.01)
        assert running.body.closed[-1] - closed_at < 1.0


def test_mid_stream_store_failure_aborts_without_appending(tmp_path: Path) -> None:
    data = secrets.token_bytes(4 * MIB)
    asset = asyncio.run(put_asset(data, asset_class="video", extension="mp4",
                                  content_type="video/mp4"))

    async def cutting_proxy(running: Running) -> None:
        """Forwards to Garage but drops the connection after 1 MiB of response."""

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", 3900)

            async def forward() -> None:
                with suppress(Exception):
                    while chunk := await reader.read(65536):
                        up_writer.write(chunk)
                        await up_writer.drain()

            task = asyncio.create_task(forward())
            sent = 0
            with suppress(Exception):
                while sent < MIB and (chunk := await up_reader.read(65536)):
                    writer.write(chunk)
                    await writer.drain()
                    sent += len(chunk)
            task.cancel()
            for each in (writer, up_writer):
                each.transport.abort()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        running.extra["proxy"] = server
        running.extra["endpoint"] = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"

    with serving(setup=cutting_proxy) as running:
        reply = http(running.port, f"/media/{asset}")
        assert reply.status == 200 and reply.header("content-length") == str(len(data))
        assert 0 < len(reply.body) < len(data)
        assert data.startswith(reply.body)


def test_store_outage_is_503_while_the_catalog_answers(tmp_path: Path) -> None:
    seeded = asyncio.run(seed(tmp_path))
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    with serving(endpoint=f"http://127.0.0.1:{port}") as running:
        target = f"/media/{seeded.video['native']}"
        assert_unavailable(http(running.port, target))
        assert_unavailable(http(running.port, target, range="bytes=0-1"))
        assert_unavailable(http(running.port, f"/downloads/{seeded.video_id}/native"))
        assert http(running.port, target, "HEAD").status == 200
        listing = http(running.port, "/api/v1/timelapses")
        assert listing.status == 200 and str(seeded.video_id).encode() in listing.body


def test_catalog_outage_or_timeout_is_503(tmp_path: Path) -> None:
    seeded = asyncio.run(seed(tmp_path))
    target = f"/media/{seeded.video['native']}"
    cases: list[tuple[str, dict[str, str]]] = [
        ("GET", {}), ("HEAD", {}), ("GET", {"range": "bytes=0-1"}),
    ]
    with serving(database_host=str(tmp_path / "no-database")) as running:
        for method, headers in cases:
            assert_unavailable(http(running.port, target, method, **headers))
    with serving(timeout_ms=500) as running:
        assert http(running.port, target).status == 200

        async def locked() -> list[tuple[Reply, float]]:
            db = engine()
            try:
                async with db.begin() as connection:
                    await connection.execute(
                        text("LOCK TABLE weather.media_asset IN ACCESS EXCLUSIVE MODE")
                    )
                    results = []
                    for method, headers in cases:
                        started = time.monotonic()
                        reply = await asyncio.to_thread(
                            http, running.port, target, method, **headers
                        )
                        results.append((reply, time.monotonic() - started))
                    return results
            finally:
                await db.dispose()

        for reply, seconds in asyncio.run(locked()):
            assert_unavailable(reply)
            assert seconds < 3
        assert http(running.port, target).status == 200
