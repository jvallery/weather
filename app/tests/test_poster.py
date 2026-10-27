# SPDX-License-Identifier: MIT
"""#78: the pipeline publishes an operator's poster choice from the published web rendition."""

import asyncio
import io
import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image
from weather.pipeline import ffmpeg, poster
from weather.publish.model import Committed

W, H = 320, 180


def ffmpeg_available() -> bool:
    try:
        return bool(ffmpeg.version())
    except ffmpeg.FFmpegError, OSError, subprocess.SubprocessError:
        return False


needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available() and os.environ.get("WEATHER_CI_IN_CLUSTER") != "1",
    reason="FFmpeg runs in fleet CI (set WEATHER_FFMPEG to run locally)",
)


def rendition(path: Path) -> bytes:
    """Two seconds at 60 fps whose colour changes every frame, so a frame index is checkable."""
    subprocess.run(
        [
            ffmpeg.binary(),
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            # A uniform colour that steps with the frame number N.
            f"nullsrc=s={W}x{H}:r=60:d=2,geq=r='mod(N*4,256)':g=128:b='mod(255-N*4,256)'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "1",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


class Span:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def chunks(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self.data), size):
            yield self.data[start : start + size]

    async def aclose(self) -> None:
        self.closed = True


class Store:
    def __init__(self, data: bytes) -> None:
        self.data, self.requested = data, []
        self.span: Span | None = None

    async def open_span(self, key: str, first: int, last: int, size: int) -> Span:
        self.requested.append((key, first, last, size))
        self.span = Span(self.data)
        return self.span


class Connection:
    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []

    async def execute(self, _sql: Any, params: dict[str, Any]) -> None:
        self.executed.append(params)


class Publisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.connection = Connection()

    async def publish_poster(self, **kwargs: Any) -> Committed:
        files = kwargs["files"]
        # The derivatives exist while the publisher reads them; record their bytes.
        kwargs["bytes"] = {f"{f.role}.{f.extension}": f.path.read_bytes() for f in files}
        self.calls.append(kwargs)
        committed = Committed(publication_id=uuid4(), revision=7, assets={}, video_id=None)
        await kwargs["then"](self.connection, committed)
        return committed


@needs_ffmpeg
def test_the_chosen_frame_is_published_as_the_six_poster_roles_without_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = rendition(tmp_path / "web.mp4")
    choice = poster.Pending(
        video_id=uuid4(),
        cycle_date=date(2026, 10, 27),
        segment="daytime",
        frames=120,
        editorial_revision=3,
        frame=90,
        web_key="video/abc.mp4",
        web_bytes=len(data),
        width=W,
        height=H,
    )

    async def pending(_engine: Any, camera: str) -> poster.Pending:
        assert camera == "skycam_east"
        return choice

    outbox: list[dict[str, Any]] = []

    async def append_outbox(_connection: Any, **kwargs: Any) -> None:
        outbox.append(kwargs)

    monkeypatch.setattr(poster, "pending", pending)
    monkeypatch.setattr(poster.jobs, "append_outbox", append_outbox)
    store, publisher = Store(data), Publisher()
    sweep = poster.Posters(None, publisher, store, "skycam_east")  # type: ignore[arg-type]
    assert asyncio.run(sweep.sweep(datetime(2026, 10, 27, 12, tzinfo=UTC))) is True

    assert store.requested == [("video/abc.mp4", 0, len(data) - 1, len(data))]
    assert store.span is not None and store.span.closed
    (call,) = publisher.calls
    assert (call["camera_id"], call["segment"]) == ("skycam_east", "daytime")
    roles = {(f.role, f.extension, f.width) for f in call["files"]}
    assert {(role, ext) for role, ext, _ in roles} == {
        ("poster", "jpg"),
        ("poster_960", "jpg"),
        ("poster_480", "jpg"),
        ("poster_webp", "webp"),
        ("poster_webp960", "webp"),
        ("poster_webp480", "webp"),
    }
    assert all(width == W for _, _, width in roles)  # never upscaled past the source
    jpeg = call["bytes"]["poster.jpg"]
    assert jpeg[:2] == b"\xff\xd8" and b"Exif" not in jpeg and b"GPS" not in jpeg
    # The poster is frame 90 itself: close to a direct decode of that frame, far from another.
    published = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"), dtype=np.int16)

    def distance(index: int) -> float:
        frame = np.frombuffer(ffmpeg.extract_frame(tmp_path / "web.mp4", index, W, H), np.uint8)
        return float(np.abs(published - frame.reshape(H, W, 3).astype(np.int16)).mean())

    assert distance(90) < 4 < distance(30)
    (applied,) = publisher.connection.executed
    assert (applied["video"], applied["editorial"], applied["frame"], applied["revision"]) == (
        choice.video_id,
        3,
        90,
        7,
    )
    assert len(applied["sha"]) == 64
    (queued,) = outbox
    assert queued["topic"] == "editorial.changed" and queued["payload"]["public"] is True


def test_nothing_pending_publishes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pending(_engine: Any, _camera: str) -> None:
        return None

    monkeypatch.setattr(poster, "pending", pending)
    publisher = Publisher()
    sweep = poster.Posters(None, publisher, Store(b""), "skycam_west")  # type: ignore[arg-type]
    assert asyncio.run(sweep.sweep(datetime(2026, 10, 27, tzinfo=UTC))) is False
    assert publisher.calls == []
