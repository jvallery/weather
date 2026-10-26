# SPDX-License-Identifier: MIT
"""Cluster-only fake-camera latency, Digest, response validation and process failure checks."""

import asyncio
import dataclasses
import hashlib
import ipaddress
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.request import parse_http_list, parse_keqv_list

import pytest
from weather.capture.chunks import read_chunk
from weather.capture.client import SNAPSHOT_PATH, CaptureFailure, SnapshotClient
from weather.capture.collector import Collector
from weather.capture.config import CaptureConfig

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="requires fleet cluster execution"
    ),
]
LOOPBACK = str(ipaddress.IPv4Address(0x7F000001))


def jpeg() -> bytes:
    # The collector checks markers and SOF geometry only; FFmpeg in the renderer decodes.
    sof = b"\xff\xc0\x00\x11\x08\x00\x10\x00\x20\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    sos = b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8" + sof + sos + b"\x12\x34" * 64 + b"\xff\xd9"


class FakeCamera:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.status = 200
        self.content_type = "image/jpeg"
        self.delay = 0.0
        self.trickle = False
        self.unknown_oversized = False
        self.always_unauthorized = False
        self.basic_challenge = False
        self.single_use_nonce = False
        self.nonce = 0
        self.requests: list[float] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.bytes_sent = 0
        self.tasks: set[asyncio.Task[Any]] = set()
        self.port = 0

    def authorized(self, header: str) -> bool:
        if not header.startswith("Digest ") or self.always_unauthorized:
            return False
        value = parse_keqv_list(parse_http_list(header[7:]))
        ha1 = hashlib.md5(b"synthetic-user:fake-camera:synthetic-password").hexdigest()
        ha2 = hashlib.md5(("GET:" + SNAPSHOT_PATH).encode()).hexdigest()
        nonce = f"nonce-{self.nonce}" if self.single_use_nonce else "fixed-nonce"
        expected = hashlib.md5(
            f"{ha1}:{nonce}:{value.get('nc')}:{value.get('cnonce')}:auth:{ha2}".encode()
        ).hexdigest()
        ok = value.get("response") == expected and value.get("uri") == SNAPSHOT_PATH
        if ok and self.single_use_nonce:
            self.nonce += 1  # Consumed: the next request is challenged again.
        return ok

    async def handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(time.monotonic())
            lines = raw.decode().split("\r\n")
            assert lines[0] == f"GET {SNAPSHOT_PATH} HTTP/1.1"
            headers = dict(line.split(": ", 1) for line in lines[1:-2])
            if not self.authorized(headers.get("Authorization", "")):
                writer.write(b"HTTP/1.1 401 Unauthorized\r\n")
                if self.basic_challenge:
                    writer.write(b'WWW-Authenticate: Basic realm="fake-camera"\r\n')
                nonce = f"nonce-{self.nonce}" if self.single_use_nonce else "fixed-nonce"
                writer.write(
                    f'WWW-Authenticate: Digest realm="fake-camera", nonce="{nonce}", '
                    'qop="auth", algorithm=MD5\r\n'
                    "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
                return
            await asyncio.sleep(self.delay)
            head = (
                f"HTTP/1.1 {self.status} Fake Camera\r\nContent-Type: {self.content_type}\r\n"
                "Connection: close\r\n"
            )
            if not self.unknown_oversized:
                head += f"Content-Length: {len(self.body)}\r\n"
            writer.write((head + "\r\n").encode())
            await writer.drain()
            if self.unknown_oversized:
                for _ in range(1280):  # 80 MiB generated in bounded pieces, not buffered.
                    writer.write(b"X" * 65536)
                    await writer.drain()
                    self.bytes_sent += 65536
                    await asyncio.sleep(0.001)
            elif self.trickle:
                for byte in self.body:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    self.bytes_sent += 1
                    await asyncio.sleep(1)
            else:
                writer.write(self.body)
                await writer.drain()
                self.bytes_sent += len(self.body)
        except ConnectionError, asyncio.IncompleteReadError:
            pass
        finally:
            self.in_flight -= 1
            writer.close()
            self.tasks.discard(task)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[FakeCamera]:
        server = await asyncio.start_server(self.handler, LOOPBACK, 0)
        self.port = server.sockets[0].getsockname()[1]
        try:
            yield self
        finally:
            server.close()
            await server.wait_closed()
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def config(self, root: Path) -> CaptureConfig:
        return CaptureConfig(
            camera="skycam_east",
            staging=root,
            pod_uid="fake-camera-pod",
            host=LOOPBACK,
            port=self.port,
            username="synthetic-user",
            password="synthetic-password",
            geometry=(32, 16),
        )


@pytest.mark.parametrize("basic_challenge", [False, True])
def test_digest_happy_path_and_identical_frames_retained(
    tmp_path: Path, basic_challenge: bool
) -> None:
    async def check() -> None:
        camera = FakeCamera(jpeg())
        camera.basic_challenge = basic_challenge
        async with camera.running():
            collector = Collector(camera.config(tmp_path))
            await collector.run(max_attempts=2)
        records = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        captured = [row for row in records if row.get("status") == "captured"]
        assert len(captured) == 2
        assert captured[0]["duplicate_hash"] is False and captured[1]["duplicate_hash"] is True
        assert captured[0]["member"]["sha256"] == captured[1]["member"]["sha256"]
        assert all(row["freshness"] == "unknown" for row in captured)
        # Without calibration the upper bound is still the response end.
        assert all(
            row["capture_upper_bound"] == row["response_completed_at_utc"] for row in captured
        )

    asyncio.run(check())


def test_a_slow_seal_never_makes_a_slot_overdue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#145: chunk seals run on the I/O thread, so a seal slower than the cadence costs no
    slot. Before, the timer waited for every NFS write and seal."""
    from weather.capture.chunks import ChunkWriter

    seal = ChunkWriter.seal

    def slow_seal(self: ChunkWriter) -> None:
        time.sleep(1.6)  # longer than the 1 s cadence
        seal(self)

    monkeypatch.setattr(ChunkWriter, "seal", slow_seal)
    queued: list[float] = []
    seal_or_abandon = Collector._seal_or_abandon

    def counted(self: Collector) -> None:
        queued.append(time.monotonic())
        seal_or_abandon(self)

    monkeypatch.setattr(Collector, "_seal_or_abandon", counted)

    async def check() -> None:
        camera = FakeCamera(jpeg())
        async with camera.running():
            collector = Collector(dataclasses.replace(camera.config(tmp_path), chunk_seconds=2))
            task = asyncio.create_task(collector.run())
            await asyncio.sleep(8.5)
            collector.shutdown()
            await task
        rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        assert len([row for row in rows if row.get("status") == "captured"]) >= 7
        assert not any(row.get("failure_class") == "overdue" for row in rows)
        sealed = len(list((tmp_path / "sealed").rglob("*.tar")))
        assert sealed >= 3
        # Each chunk's seal is queued once, not again on every timer pass while it runs.
        assert len(queued) <= sealed

    asyncio.run(check())


def test_single_use_nonce_retries_at_once_and_keeps_the_cadence(tmp_path: Path) -> None:
    async def check() -> None:
        camera = FakeCamera(jpeg())
        camera.single_use_nonce = True
        async with camera.running():
            collector = Collector(camera.config(tmp_path))
            task = asyncio.create_task(collector.run())
            await asyncio.sleep(4.5)
            collector.shutdown()
            await task
        # Every poll is a challenge plus its immediate authorized retry; polls stay >= cadence.
        assert camera.max_in_flight == 1
        pairs = list(zip(camera.requests[::2], camera.requests[1::2], strict=False))
        assert len(pairs) >= 4 and all(b - a < 0.5 for a, b in pairs)
        starts = [a for a, _ in pairs]
        assert all(b - a >= 0.95 for a, b in zip(starts, starts[1:], strict=False))
        rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        assert len([row for row in rows if row.get("status") == "captured"]) >= 4
        assert not any(row.get("failure_class") == "overdue" for row in rows)

    asyncio.run(check())


def test_one_byte_per_second_body_hits_total_deadline(tmp_path: Path) -> None:
    async def check() -> None:
        camera = FakeCamera(jpeg())
        camera.trickle = True
        async with camera.running():
            start = time.monotonic()
            with pytest.raises(CaptureFailure, match="timeout"):
                await SnapshotClient(camera.config(tmp_path)).fetch()
            elapsed = time.monotonic() - start
            assert 4.8 <= elapsed <= 5.6
            assert camera.bytes_sent < len(camera.body)

    asyncio.run(check())


def test_slow_camera_never_overlaps_or_catches_up(tmp_path: Path) -> None:
    async def check() -> None:
        camera = FakeCamera(jpeg())
        camera.delay = 2.5
        async with camera.running():
            collector = Collector(camera.config(tmp_path))
            task = asyncio.create_task(collector.run())
            await asyncio.sleep(10)
            collector.shutdown()
            await task
        assert camera.max_in_flight == 1
        # Includes the initial Digest challenge GET, answered at once by its authorized retry.
        # A slot under half a period late is still taken, so 2.5 s responses allow one poll per
        # 2.5 s and never a catch-up burst.
        assert len(camera.requests) <= 5
        assert camera.requests[1] - camera.requests[0] < 0.5
        polls = camera.requests[1:]
        assert all(b - a >= 0.95 for a, b in zip(polls, polls[1:], strict=False))
        rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        assert len([row for row in rows if row.get("status") == "captured"]) >= 2
        assert any(row.get("failure_class") == "overdue" for row in rows)

    asyncio.run(check())


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("redirect", "redirect"),
        ("html", "content_type"),
        ("missing_eoi", "jpeg_markers"),
        ("wrong_geometry", "geometry"),
        ("status_600", "protocol"),
        ("oversized", "oversized"),
    ],
)
def test_bad_responses_rejected_and_recorded(tmp_path: Path, kind: str, expected: str) -> None:
    async def check() -> None:
        body = jpeg()
        camera = FakeCamera(body)
        if kind == "redirect":
            camera.status = 302
        elif kind == "html":
            camera.body, camera.content_type = b"<html>login</html>", "text/html"
        elif kind == "missing_eoi":
            camera.body = body[:-2]
        elif kind == "wrong_geometry":
            # SOF remains valid; mutate dimensions while retaining a complete JPEG body.
            offset = body.index(b"\xff\xc0")
            camera.body = body[: offset + 5] + b"\x00\x20" + body[offset + 7 :]
        elif kind == "status_600":
            camera.status = 600
        else:
            camera.unknown_oversized = True
        async with camera.running():
            collector = Collector(camera.config(tmp_path))
            await collector.run(max_attempts=1)
        rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        attempts = [row for row in rows if row.get("record_type") == "capture_attempt"]
        assert attempts and attempts[0]["failure_class"] == expected
        assert attempts[0]["status"] == "request_failed" and attempts[0]["member"] is None
        if kind == "oversized":
            assert camera.bytes_sent < 80 * 1024 * 1024

    asyncio.run(check())


def test_auth_failure_cannot_trigger_rapid_relogins(tmp_path: Path) -> None:
    async def check() -> None:
        camera = FakeCamera(jpeg())
        camera.always_unauthorized = True
        async with camera.running():
            collector = Collector(camera.config(tmp_path))
            await collector.run(max_attempts=3)
        assert len(camera.requests) == 2  # One negotiation attempt, followed by >=60 s backoff.
        rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
        assert any(row.get("failure_class") == "authentication" for row in rows)
        assert any(row.get("failure_class") == "backoff" for row in rows)

    asyncio.run(check())


def test_chunk_deadline_seals_while_waiting_for_a_slower_capture_slot(tmp_path: Path) -> None:
    import dataclasses

    async def check() -> None:
        camera = FakeCamera(jpeg())
        async with camera.running():
            config = dataclasses.replace(
                camera.config(tmp_path),
                cadence_ms=5000,
                phase_ms=(time.time_ns() // 1_000_000 + 50) % 5000,
                chunk_seconds=1,
            )
            collector = Collector(config)
            # Existing negotiated nonce, so the test isolates sealing from cold authentication.
            collector.client._digest.challenge(
                'Digest realm="fake-camera", nonce="fixed-nonce", qop="auth", algorithm=MD5'
            )
            task = asyncio.create_task(collector.run())
            await asyncio.sleep(1.5)
            paths = list((tmp_path / "sealed").rglob("*.tar"))
            assert len(paths) == 1 and not task.done()
            assert read_chunk(paths[0])[1]["status"] == "captured"
            assert len(camera.requests) == 1
            collector.shutdown()
            await task

    asyncio.run(check())
