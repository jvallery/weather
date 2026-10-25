# SPDX-License-Identifier: MIT
"""Delayed previews (#39) without a database or NAS: P14 timing on a fake clock, the pointer and
bytes routes, sanitized derivatives, the producer's sealed-only selection, outages and cleanup.
The database view's own clock enforcement is covered by test_preview_integration.py."""

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image
from prometheus_client import CollectorRegistry
from weather.api import build_app
from weather.capture.chunks import ChunkWriter, IOHealth
from weather.capture.config import CaptureConfig
from weather.capture.timing import Slot
from weather.media.origin import MediaMetrics, MediaOrigin, Unavailable
from weather.pipeline import synthetic
from weather.pipeline.intake import Expected
from weather.preview import policy, producer
from weather.preview.jpeg import DerivativeError, check_clean, derive
from weather.preview.server import (
    FileBody,
    Files,
    PreviewMetrics,
    PreviewService,
    Row,
    build_preview_app,
    preview_routes,
)

ROOT = Path(__file__).resolve().parents[2]
NS = 1_000_000_000
MS = timedelta(milliseconds=1)
CAPTURED = datetime(2026, 10, 24, 18, 0, 0, tzinfo=UTC)


# ---- policy ------------------------------------------------------------------------------------


def test_p14_publication_boundary_and_unknown_future_reversed_bounds() -> None:
    lower, upper = CAPTURED, CAPTURED + timedelta(seconds=1)
    at = upper + timedelta(seconds=630)
    assert policy.publishable(lower, upper, at)
    assert not policy.publishable(lower, upper, at - MS)
    # A larger skew margin only delays; below the 30 s floor nothing is publishable.
    assert not policy.publishable(lower, upper, at, timedelta(seconds=60))
    assert not policy.publishable(lower, upper, at, timedelta(seconds=29))
    assert not policy.publishable(None, upper, at)
    assert not policy.publishable(lower, None, at)
    assert not policy.publishable(upper, lower, at)  # reversed
    assert not policy.publishable(lower - timedelta(seconds=301), upper, at)  # implausibly wide
    assert not policy.plausible(lower, at + MS, at)  # future upper bound
    # Too old to be worth publishing: it would already be unavailable.
    assert not policy.publishable(lower, upper, lower + timedelta(seconds=1800) + MS)


def test_age_classification_and_half_open_expiry() -> None:
    assert policy.state(CAPTURED, CAPTURED + timedelta(seconds=1200)) == "fresh"
    assert policy.state(CAPTURED, CAPTURED + timedelta(seconds=1200) + MS) == "stale"
    assert policy.state(CAPTURED, CAPTURED + timedelta(seconds=1800)) == "stale"
    assert policy.state(CAPTURED, CAPTURED + timedelta(seconds=1800) + MS) == "unavailable"
    timing = _timing()
    timing = replace(timing, first_published_at=timing.public_not_before)
    assert policy.servable(timing, timing.public_not_before)
    assert not policy.servable(timing, timing.public_not_before - MS)
    assert policy.servable(timing, timing.expires_at - MS)
    assert not policy.servable(timing, timing.expires_at)
    for bad in (
        replace(timing, captured_lower=timing.captured_upper + MS),  # reversed
        replace(timing, public_not_before=timing.captured_upper + timedelta(seconds=629)),
        replace(timing, first_published_at=timing.expires_at),
        replace(timing, expires_at=timing.captured_lower + timedelta(seconds=3601)),
    ):
        assert not policy.servable(bad, timing.public_not_before + timedelta(seconds=1))


def _timing(lower: datetime = CAPTURED) -> policy.Timing:
    upper = lower + timedelta(seconds=1)
    return policy.Timing(
        captured_lower=lower,
        captured_upper=upper,
        public_not_before=upper + timedelta(seconds=630),
        first_published_at=upper + timedelta(seconds=640),
        expires_at=lower + timedelta(seconds=3600),
    )


# ---- sanitized derivatives -------------------------------------------------------------------


def _camera_jpeg(width: int = 1600, height: int = 800) -> bytes:
    """A frame carrying every kind of metadata a camera could add."""
    image = Image.new("RGB", (width, height), (90, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    exif[0x0131] = "firmware-serial-123"  # Software
    exif.get_ifd(0x8825)[2] = (40.0, 1.0, 2.0)  # GPSLatitude
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        exif=exif.tobytes(),
        comment=b"camera-192.0.2.10",
        xmp=b"<x:xmpmeta>GPS secret</x:xmpmeta>",
        icc_profile=b"\0" * 128,
    )
    return buffer.getvalue()


def test_derivatives_are_re_encoded_without_exif_xmp_gps_or_comments() -> None:
    source = _camera_jpeg()
    for marker in (b"SecretCam", b"camera-192.0.2.10", b"GPS secret", b"Exif\0\0"):
        assert marker in source
    with pytest.raises(DerivativeError):
        check_clean(source)
    results = derive(source)
    assert [(v, w, h) for v, _, w, h in results] == [("large", 1280, 640), ("small", 480, 240)]
    for _, data, _, _ in results:
        check_clean(data)
        for marker in (b"SecretCam", b"192.0.2.10", b"GPS", b"Exif", b"xmpmeta", b"ICC_PROFILE"):
            assert marker not in data
        with Image.open(io.BytesIO(data)) as image:
            assert not image.getexif() and "comment" not in image.info and "xmp" not in image.info
    # Never upscaled.
    assert [(w, h) for _, _, w, h in derive(_camera_jpeg(320, 160))] == [(320, 160), (320, 160)]
    with pytest.raises(DerivativeError):
        derive(b"\xff\xd8not a jpeg")


# ---- the pointer and bytes routes ------------------------------------------------------------


class FakeSource:
    """Answers like a view that admits every row, so only the request-time re-check denies."""

    def __init__(self) -> None:
        self.now = CAPTURED
        self.rows: list[Row] = []
        self.down = False
        self.calls = 0

    async def latest(self, camera: str) -> tuple[datetime, list[Row]]:
        self.calls += 1
        if self.down:
            raise Unavailable("catalog")
        rows = [r for r in self.rows if r.camera_id == camera]
        top = max((r.generation for r in rows), default=None)
        return self.now, [r for r in rows if r.generation == top]

    async def one(self, identity: UUID) -> tuple[datetime, Row] | None:
        self.calls += 1
        if self.down:
            raise Unavailable("catalog")
        row = next((r for r in self.rows if r.id == identity), None)
        return (self.now, row) if row is not None else None


class Harness:
    def __init__(self, root: Path, **files: Any) -> None:
        self.root = root
        self.source = FakeSource()
        self.metrics = PreviewMetrics(CollectorRegistry())
        self.files = Files(root, self.metrics, **files)
        self.service = PreviewService(self.source, self.files, self.metrics)
        origin = MediaOrigin(
            preview_routes(self.service.resolve), FileBody(), MediaMetrics(CollectorRegistry())
        )
        self.client = TestClient(build_preview_app(self.service, origin))

    def publish(
        self, generation: int = 29_000_000, lower: datetime = CAPTURED, camera: str = "skycam_east"
    ) -> dict[str, Row]:
        rows = {}
        for variant, data, width, height in derive(_camera_jpeg()):
            path = f"previews/{camera}/{generation}-{variant}.jpg"
            (self.root / camera).mkdir(parents=True, exist_ok=True)
            (self.root / camera / f"{generation}-{variant}.jpg").write_bytes(data)
            row = Row(
                id=uuid4(),
                camera_id=camera,
                generation=generation,
                variant=variant,
                width=width,
                height=height,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                path=path,
                timing=_timing(lower),
            )
            self.source.rows.append(row)
            rows[variant] = row
        return rows

    def pointer(self, camera: str = "skycam_east") -> dict[str, Any]:
        response = self.client.get(f"/api/v1/cameras/{camera}/preview")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        return dict(response.json())


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    h = Harness(tmp_path)
    yield h
    h.files.close()


def test_pointer_fake_clock_embargo_age_and_expiry(harness: Harness) -> None:
    rows = harness.publish()
    timing = rows["large"].timing
    harness.source.now = timing.public_not_before - MS
    assert harness.pointer() | {"generated_at": None} == harness.pointer() | {"generated_at": None}
    body = harness.pointer()
    assert (body["state"], body["reason"], body["images"]) == ("unavailable", "no_preview", [])
    assert body["source_interval"] is None
    # Equality is eligible. First published is after the embargo here, so age is what counts.
    harness.source.now = timing.public_not_before
    assert harness.pointer()["reason"] == "no_preview"  # first_published_at is still ahead
    harness.source.now = timing.first_published_at
    body = harness.pointer()
    assert body["state"] == "fresh" and body["reason"] is None
    assert [i["variant"] for i in body["images"]] == ["large", "small"]
    assert body["images"][0]["url"] == f"/media/previews/{rows['large'].id}"
    assert body["source_interval"] == {
        "start": "2026-10-24T18:00:00Z",
        "end": "2026-10-24T18:00:01Z",
    }
    assert body["exposure_upper_bound"] == "2026-10-24T18:00:01Z"
    windows = (body["refresh_seconds"], body["fresh_seconds"], body["stale_seconds"])
    assert windows == (60, 1200, 1800)
    assert (body["minimum_delay_seconds"], body["skew_margin_seconds"]) == (600, 30)
    for age, state in (
        (timedelta(seconds=1200), "fresh"),
        (timedelta(seconds=1200) + MS, "stale"),
        (timedelta(seconds=1800), "stale"),
        (timedelta(seconds=1800) + MS, "unavailable"),
    ):
        harness.source.now = CAPTURED + age
        body = harness.pointer()
        assert body["state"] == state, age
        assert body["age_seconds"] == (None if state == "unavailable" else age.total_seconds())
    assert body["reason"] == "too_old" and body["images"] == []
    # The bytes stay eligible after the pointer stopped calling them current, until expires_at.
    assert harness.client.get(f"/media/previews/{rows['large'].id}").status_code == 200
    harness.source.now = timing.expires_at - MS
    assert harness.client.get(f"/media/previews/{rows['large'].id}").status_code == 200
    harness.source.now = timing.expires_at
    assert harness.client.get(f"/media/previews/{rows['large'].id}").status_code == 404
    assert harness.pointer()["reason"] == "no_preview"


def test_variants_of_one_generation_form_one_pointer(harness: Harness) -> None:
    rows = harness.publish()
    # Publication times of the two rows may differ; the capture is what identifies them.
    small = rows["small"]
    later = replace(small.timing, first_published_at=small.timing.first_published_at + MS)
    harness.source.rows[-1] = replace(small, timing=later)
    harness.source.now = later.first_published_at
    body = harness.pointer()
    assert body["state"] == "fresh" and len(body["images"]) == 2
    # Different captures under one generation are never mixed into one pointer.
    other = replace(
        later, captured_lower=later.captured_lower - MS, expires_at=later.expires_at - MS
    )
    harness.source.rows[-1] = replace(small, timing=other)
    assert harness.pointer()["reason"] == "no_preview"


def test_repeated_identical_bytes_never_reset_capture_time_or_age(harness: Harness) -> None:
    rows = harness.publish()
    harness.source.now = CAPTURED + timedelta(seconds=700)
    first = harness.pointer()
    again = harness.pointer()
    assert first == again
    harness.source.now += timedelta(seconds=60)
    later = harness.pointer()
    assert later["source_interval"] == first["source_interval"]
    assert later["first_published_at"] == first["first_published_at"]
    assert later["age_seconds"] == first["age_seconds"] + 60
    one = harness.client.get(f"/media/previews/{rows['small'].id}")
    two = harness.client.get(f"/media/previews/{rows['small'].id}")
    assert one.content == two.content and one.headers["etag"] == two.headers["etag"]


def test_bytes_get_head_range_and_validators_are_authorized_each_time(harness: Harness) -> None:
    rows = harness.publish()
    large = rows["large"]
    url = f"/media/previews/{large.id}"
    data = (harness.root / "skycam_east" / f"{large.generation}-large.jpg").read_bytes()
    etag = f'"{large.sha256}"'
    harness.source.now = large.timing.first_published_at
    full = harness.client.get(url)
    assert full.status_code == 200 and full.content == data
    assert full.headers["content-type"] == "image/jpeg"
    assert full.headers["cache-control"] == "no-store, no-transform"
    assert full.headers["etag"] == etag and "last-modified" not in full.headers
    head = harness.client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(data))
    part = harness.client.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == data[10:20]
    # No validator yields a 304 or skips authorization.
    assert harness.client.get(url, headers={"If-None-Match": etag}).status_code == 200
    harness.source.now = large.timing.public_not_before - MS
    unknown = harness.client.get(f"/media/previews/{uuid4()}")
    assert unknown.status_code == 404
    for method, headers in (
        ("GET", {}),
        ("HEAD", {}),
        ("GET", {"Range": "bytes=0-9"}),
        ("GET", {"Range": "bytes=0-9", "If-Range": etag}),
        ("GET", {"If-None-Match": etag}),
        ("HEAD", {"If-None-Match": etag, "Range": "bytes=0-9"}),
    ):
        denied = harness.client.request(method, url, headers=headers)
        assert denied.status_code == 404, (method, headers)
        assert "etag" not in denied.headers
        assert denied.headers["cache-control"].startswith("no-store")
        if method == "GET":
            assert denied.content == unknown.content
    assert harness.metrics.violations.labels("media")._value.get() >= 6


def test_missing_or_changed_file_is_unavailable_and_its_bytes_the_identical_404(
    harness: Harness,
) -> None:
    rows = harness.publish()
    harness.source.now = rows["large"].timing.first_published_at
    unknown = harness.client.get(f"/media/previews/{uuid4()}")
    path = harness.root / "skycam_east" / f"{rows['large'].generation}-large.jpg"
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))  # same size, other hash
    body = harness.pointer()
    assert (body["state"], body["reason"]) == ("unavailable", "media_unavailable")
    assert body["images"] == []
    changed = harness.client.get(f"/media/previews/{rows['large'].id}")
    assert (changed.status_code, changed.content) == (404, unknown.content)
    path.unlink()
    assert harness.pointer()["reason"] == "media_unavailable"
    missing = harness.client.get(f"/media/previews/{rows['large'].id}")
    assert (missing.status_code, missing.content) == (404, unknown.content)
    # A symlink planted in previews/ is never followed.
    path.symlink_to(harness.root / "skycam_east" / f"{rows['small'].generation}-small.jpg")
    assert harness.client.get(f"/media/previews/{rows['large'].id}").status_code == 404


def test_unknown_camera_catalog_outage_and_methods(harness: Harness) -> None:
    response = harness.client.get("/api/v1/cameras/backyard/preview")
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
    harness.source.down = True
    for url in ("/api/v1/cameras/skycam_west/preview", f"/media/previews/{uuid4()}"):
        response = harness.client.get(url)
        assert response.status_code == 503
        assert response.headers["cache-control"].startswith("no-store")
        assert "etag" not in response.headers
    assert harness.client.post(f"/media/previews/{uuid4()}").status_code == 405
    # Anything under the prefix that is not one canonical UUID is the same denial.
    for path in ("/media/previews/..%2fsealed", f"/media/previews/{uuid4()}/x", "/media/previews/"):
        assert harness.client.get(path).status_code == 404
    harness.source.down = False
    assert harness.pointer("frontyard_1")["reason"] == "no_preview"


def test_stalled_share_answers_unavailable_within_its_deadline(tmp_path: Path) -> None:
    release = threading.Event()

    def stalled(_path: Path, _size: int) -> bytes | None:
        release.wait(10)
        return None

    h = Harness(tmp_path, timeout=0.2, readers=2, read=stalled)
    try:
        rows = h.publish()
        h.source.now = rows["large"].timing.first_published_at
        started = time.monotonic()
        body = h.pointer()
        assert body["reason"] == "media_unavailable"
        # Both readers are now stuck: later requests answer at once instead of queueing.
        assert h.client.get(f"/media/previews/{rows['small'].id}").status_code == 503
        assert h.pointer()["reason"] == "media_unavailable"
        assert time.monotonic() - started < 2
        assert h.metrics.files.labels("busy")._value.get() >= 1
    finally:
        release.set()
        h.files.close()


def test_api_never_serves_or_imports_previews() -> None:
    client = TestClient(build_app())
    assert client.get("/api/v1/cameras/skycam_east/preview").status_code == 404
    # The api's own origin admits only /media/<uuid>; preview bytes are not its to serve.
    assert client.get(f"/media/previews/{uuid4()}").status_code in {404, 503}
    code = "import sys, weather.api; sys.exit('weather.preview.server' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


# ---- the producer ----------------------------------------------------------------------------

W, H = 64, 32
T0 = datetime(2026, 10, 23, 10, 0, 0, tzinfo=UTC)
EXPECTED = Expected("skycam_east", 2000, 0, W, H)


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.deletes = 0
        self.fail_insert = False

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        database = self

        class Connection:
            async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
                sql = str(statement)
                if sql.startswith("SELECT generation"):
                    mine = [r for r in database.rows if r["camera"] == params["camera"]]
                    top = max(mine, key=lambda r: r["generation"], default=None)
                    row = (
                        SimpleNamespace(
                            generation=top["generation"],
                            captured_lower=top["lower"],
                            captured_upper=top["upper"],
                        )
                        if top
                        else None
                    )
                    return SimpleNamespace(first=lambda: row)
                if "INSERT INTO weather.camera_preview" in sql:
                    if database.fail_insert:
                        raise RuntimeError("check violation")
                    database.rows.append(dict(params))
                elif sql.startswith("DELETE"):
                    database.deletes += 1
                return SimpleNamespace(first=lambda: None)

        yield Connection()

    def connect(self) -> Any:
        return self._connection()

    def begin(self) -> Any:
        return self._connection()


def _sealed(staging: Path, seconds: range, start: datetime = T0) -> list[Path]:
    frames = [
        synthetic.Frame(int(start.timestamp() + s) * NS, synthetic.solid(W, H, s % 250))
        for s in seconds
    ]
    paths = synthetic.write_chunks(staging, "skycam_east", frames, geometry=(W, H), cadence_ms=2000)
    # Seal times: each chunk is sealed just after its 60 s window.
    for index, path in enumerate(paths):
        sealed = start.timestamp() + seconds.start + 60 * (index + 1) + 1
        os.utime(path, ns=(int(sealed * NS), int(sealed * NS)))
    return paths


def _unsealed(staging: Path, slot: datetime) -> Path:
    config = CaptureConfig(
        camera="skycam_east",
        staging=staging,
        pod_uid="other",
        host="127.0.0.1",
        port=8080,
        username="synthetic",
        password="synthetic",
        geometry=(W, H),
        cadence_ms=2000,
        phase_ms=0,
        chunk_seconds=60,
    )
    first = Slot("x", 0, int(slot.timestamp()) * NS, "c")
    writer = ChunkWriter(config, "unsealed", 0, first, IOHealth())
    writer._file.flush()
    return writer.path


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _producer(staging: Path, db: FakeDatabase, clock: Clock) -> producer.PreviewProducer:
    return producer.PreviewProducer(staging, EXPECTED, db, clock=clock)  # type: ignore[arg-type]


def test_producer_publishes_the_newest_eligible_sealed_frame_and_never_reads_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sealed(tmp_path, range(0, 2400, 2))
    tmp = _unsealed(tmp_path, T0 + timedelta(minutes=9))
    assert tmp.parent == tmp_path / "tmp" / "skycam_east"
    touched: list[str] = []
    real_scandir, real_parse, real_read = os.scandir, producer.parse_chunk, producer.read_frame

    def scandir(path: Any) -> Any:
        touched.append(str(path))
        return real_scandir(path)

    def parse(path: Path, expected: Expected) -> Any:
        touched.append(str(path))
        return real_parse(path, expected)

    def read(candidate: Any) -> bytes:
        touched.append(str(candidate.chunk))
        return real_read(candidate)

    monkeypatch.setattr(producer.os, "scandir", scandir)
    monkeypatch.setattr(producer, "parse_chunk", parse)
    monkeypatch.setattr(producer, "read_frame", read)
    db, clock = FakeDatabase(), Clock(T0 + timedelta(minutes=20))
    preview = _producer(tmp_path, db, clock)
    assert asyncio.run(preview.step())
    unsealed = str(tmp_path / "tmp")
    assert touched and not any(p.startswith(unsealed) for p in touched)
    assert all(p.startswith(str(tmp_path / "sealed")) or "previews" in p for p in touched)
    assert [r["variant"] for r in db.rows] == ["large", "small"]
    row = db.rows[0]
    # Newest slot with upper + 630 s <= now: 568 (its request ends 150 ms after the slot).
    assert row["upper"] == T0 + timedelta(seconds=568, milliseconds=150)
    assert row["lower"] == T0 + timedelta(seconds=568, milliseconds=50)
    assert row["not_before"] == row["upper"] + timedelta(seconds=630) <= clock.now
    generation = int(clock.now.timestamp()) // 60
    assert row["generation"] == generation
    assert row["path"] == f"previews/skycam_east/{generation}-large.jpg"
    written = (tmp_path / row["path"]).read_bytes()
    assert hashlib.sha256(written).hexdigest() == row["sha256"] and len(written) == row["bytes"]
    check_clean(written)
    assert not list((tmp_path / "previews/skycam_east").glob(".tmp-*"))


def test_one_generation_per_minute_none_for_an_old_frame_and_one_after_an_outage(
    tmp_path: Path,
) -> None:
    _sealed(tmp_path, range(0, 2400, 2))
    db, clock = FakeDatabase(), Clock(T0 + timedelta(minutes=20))
    preview = _producer(tmp_path, db, clock)
    assert asyncio.run(preview.step())
    clock.now += timedelta(seconds=40)  # same minute
    assert not asyncio.run(preview.step())
    # A 30 minute outage: exactly one new generation, the newest eligible frame, no backlog.
    clock.now = T0 + timedelta(minutes=50)
    assert asyncio.run(preview.step())
    assert len({r["generation"] for r in db.rows}) == 2
    assert db.rows[-1]["upper"] == T0 + timedelta(seconds=2368, milliseconds=150)
    # Input ends at 2398 s. Once it is published, a later minute republishes nothing.
    clock.now = T0 + timedelta(minutes=51)
    assert asyncio.run(preview.step())
    clock.now = T0 + timedelta(minutes=55)
    assert not asyncio.run(preview.step())
    # A restart reloads the newest generation and stays quiet in the same state.
    again = _producer(tmp_path, db, clock)
    assert not asyncio.run(again.step())
    assert len(db.rows) == 6


def test_unknown_or_future_timing_is_never_published(tmp_path: Path) -> None:
    unknown = synthetic.Frame(
        int(T0.timestamp()) * NS,
        synthetic.solid(W, H, 1),
        extra=(("capture_upper_bound", None), ("clock_discontinuity", True)),
    )
    synthetic.write_chunks(tmp_path, "skycam_east", [unknown], geometry=(W, H), cadence_ms=2000)
    db = FakeDatabase()
    # Only the unknown-timing frame exists, long past any embargo: nothing is publishable.
    assert not asyncio.run(_producer(tmp_path, db, Clock(T0 + timedelta(minutes=15))).step())
    # Frames whose bounds are still in the future of the producer's clock never qualify.
    _sealed(tmp_path / "b", range(0, 600, 2))
    assert not asyncio.run(_producer(tmp_path / "b", db, Clock(T0 - timedelta(minutes=1))).step())
    assert db.rows == []


def test_catalog_refusal_and_cleanup(tmp_path: Path) -> None:
    _sealed(tmp_path, range(0, 2400, 2))
    db, clock = FakeDatabase(), Clock(T0 + timedelta(minutes=20))
    preview = _producer(tmp_path, db, clock)
    db.fail_insert = True  # e.g. the database clock is behind: its CHECK refuses the row
    assert not asyncio.run(preview.step())
    assert db.rows == [] and not preview.loaded
    db.fail_insert = False
    clock.now += timedelta(seconds=15)
    assert asyncio.run(preview.step())
    directory = tmp_path / "previews/skycam_east"
    old = int((clock.now - timedelta(minutes=66)).timestamp()) // 60
    (directory / f"{old}-large.jpg").write_bytes(b"x")
    kept = int((clock.now - timedelta(minutes=60)).timestamp()) // 60
    (directory / f"{kept}-small.jpg").write_bytes(b"x")
    stale_tmp = directory / ".tmp-1-large.jpg"
    stale_tmp.write_bytes(b"x")
    os.utime(stale_tmp, (time.time() - 600, time.time() - 600))
    (directory / "unrelated.txt").write_bytes(b"x")
    deletes = db.deletes
    clock.now += timedelta(minutes=1)
    asyncio.run(preview.step())
    names = {p.name for p in directory.iterdir()}
    assert f"{old}-large.jpg" not in names and ".tmp-1-large.jpg" not in names
    assert {f"{kept}-small.jpg", "unrelated.txt"} <= names
    assert db.deletes == deletes + 1


# ---- manifests -------------------------------------------------------------------------------


def _documents(relative: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all((ROOT / relative).read_text()) if d]


def test_weather_preview_is_the_only_public_pod_on_staging_and_reads_previews_only() -> None:
    root = yaml.safe_load((ROOT / "deploy/k8s/kustomization.yaml").read_text())
    assert "preview" in root["resources"]
    deployment = next(
        d for d in _documents("deploy/k8s/preview/preview.yaml") if d["kind"] == "Deployment"
    )
    assert int(deployment["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]) >= 2
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    container = pod["containers"][0]
    assert container["command"] == ["weather", "preview"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"]["path"] == "/healthz"
    staging = next(m for m in container["volumeMounts"] if m["name"] == "staging")
    assert staging == {
        "name": "staging",
        "mountPath": "/previews",
        "subPath": "previews",
        "readOnly": True,
    }
    volume = next(v for v in pod["volumes"] if v["name"] == "staging")
    assert volume["persistentVolumeClaim"] == {"claimName": "weather-staging", "readOnly": True}
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert labels["weather.vallery.net/database-role"] == "api"
    # The api has no NFS volume (C17).
    api = next(d for d in _documents("deploy/k8s/application.yaml") if d["kind"] == "Deployment")
    assert all("persistentVolumeClaim" not in v for v in api["spec"]["template"]["spec"]["volumes"])
    policy_doc = _documents("deploy/k8s/preview/network-policy.yaml")[0]
    assert policy_doc["spec"]["podSelector"]["matchLabels"].items() <= labels.items()
    assert policy_doc["spec"]["policyTypes"] == ["Ingress"]
    sources = {
        rule["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]: [
            p["port"] for p in rule["ports"]
        ]
        for rule in policy_doc["spec"]["ingress"]
    }
    assert sources == {"traefik-apps": [8000], "observability": [9000]}
    route = next(
        r
        for r in _documents("deploy/k8s/ingress.yaml")[-1]["spec"]["routes"]
        if r["services"][0]["name"] == "weather-preview"
    )
    assert "/media/previews/" in route["match"] and "/preview$" in route["match"]
    assert route["priority"] > 100
