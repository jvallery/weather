# SPDX-License-Identifier: MIT
"""Stage A inputs: sampled web-rendition frames and keogram columns per analysis window.

The worker reads `weather-media` with the read-only reader key (never originals), verifies the
content address of every object it downloads, decodes the selected frames in one FFmpeg pass,
and re-encodes each as a small JPEG. The keogram is optional (#37): when the segment has no
current keogram, or it is oversized, unreadable, mis-addressed or not an image, windows are
described from frames alone and the inputs record that. Only the web rendition can fail a run.
"""

import asyncio
import hashlib
import io
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image as PILImage

from weather.pipeline.ffmpeg import binary
from weather.publish.objects import ObjectStore, StoreError

from .client import MAX_IMAGE_BYTES, Image
from .windows import Window, samples

FRAME_WIDTH = 768
JPEG_QUALITY = 80
KEOGRAM_MIN_WIDTH = 256
DECODE_SECONDS = 900
# The web rendition and keogram are bounded by the publication contract; refuse anything else.
MAX_RENDITION_BYTES = 2 * 1024 * 1024 * 1024
MAX_KEOGRAM_PIXELS = 4096 * 1024
MAX_KEOGRAM_BYTES = 8 * MAX_IMAGE_BYTES


class MediaError(RuntimeError):
    """Unreadable or invalid media: the run fails for this media revision without retry."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Media {reason}")
        self.reason = reason


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class Source:
    web: StoredObject
    keogram: StoredObject | None


@dataclass(frozen=True)
class WindowMedia:
    window: Window
    frames: tuple[tuple[int, Image], ...]
    keogram: Image | None


class MediaReader(Protocol):
    async def prepare(self, source: Source, windows: Sequence[Window]) -> list[WindowMedia]: ...


async def _download(store: ObjectStore, item: StoredObject, path: Path) -> None:
    if not 0 < item.bytes <= MAX_RENDITION_BYTES:
        raise MediaError("size")
    digest, size = hashlib.sha256(), 0
    try:
        span = await store.open_span(item.key, 0, item.bytes - 1, item.bytes)
        try:
            with path.open("wb") as handle:
                async for chunk in span.chunks(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
        finally:
            await span.aclose()
    except StoreError:
        raise MediaError("store") from None
    if size != item.bytes or digest.hexdigest() != item.sha256:
        raise MediaError("content_address")


def _jpeg(image: PILImage.Image) -> Image:
    for quality in (JPEG_QUALITY, 65, 50):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        if buffer.tell() <= MAX_IMAGE_BYTES // 2:
            return Image("image/jpeg", buffer.getvalue())
    raise MediaError("frame_size")


def sample_frames(path: Path, indices: Sequence[int], directory: Path) -> list[Image]:
    """Decode exactly `indices` (ascending output frame numbers) in one pass, as JPEGs."""
    if not indices:
        return []
    if list(indices) != sorted(set(indices)):
        raise ValueError("Frame indices must be unique and ascending")
    select = "+".join(f"eq(n,{index})" for index in indices)
    command = [
        binary(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"select='{select}',scale='min({FRAME_WIDTH},iw)':-2",
        "-fps_mode",
        "passthrough",
        # The qualified FFmpeg has no PNG encoder; near-lossless MJPEG, re-encoded below.
        "-c:v",
        "mjpeg",
        "-q:v",
        "2",
        "-pix_fmt",
        "yuvj420p",
        "-f",
        "image2",
        str(directory / "frame-%05d.jpg"),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=DECODE_SECONDS)
    except subprocess.TimeoutExpired:
        raise MediaError("decode") from None
    files = sorted(directory.glob("frame-*.jpg"))
    if result.returncode or len(files) != len(indices):
        raise MediaError("decode")
    images = []
    for file in files:
        with PILImage.open(file) as frame:
            images.append(_jpeg(frame.convert("RGB")))
        file.unlink()
    return images


def keogram_columns(data: bytes, start_fraction: float, end_fraction: float) -> Image:
    """The keogram's columns for one window, widened (nearest) so the model can read them."""
    with PILImage.open(io.BytesIO(data)) as opened:
        pixels = opened.width * opened.height
        if opened.format not in ("PNG", "JPEG") or pixels > MAX_KEOGRAM_PIXELS:
            raise MediaError("keogram")
        image = opened.convert("RGB")
    first = min(int(start_fraction * image.width), image.width - 1)
    last = max(first + 1, min(image.width, -int(-end_fraction * image.width)))
    crop = image.crop((first, 0, last, image.height))
    if crop.width < KEOGRAM_MIN_WIDTH:
        scale = -(-KEOGRAM_MIN_WIDTH // crop.width)
        crop = crop.resize((crop.width * scale, crop.height), PILImage.Resampling.NEAREST)
    buffer = io.BytesIO()
    crop.save(buffer, "PNG", optimize=True)
    if buffer.tell() > MAX_IMAGE_BYTES:
        raise MediaError("keogram")
    return Image("image/png", buffer.getvalue())


def keogram_windows(
    data: bytes | None, windows: Sequence[Window], chosen: Sequence[Window]
) -> list[Image | None]:
    """Each chosen window's keogram columns, or no keogram at all if any of it is unusable."""
    if data is None or not windows:
        return [None] * len(chosen)
    span = (windows[-1].end - windows[0].start).total_seconds()
    try:
        return [
            keogram_columns(
                data,
                (window.start - windows[0].start).total_seconds() / span,
                (window.end - windows[0].start).total_seconds() / span,
            )
            for window in chosen
        ]
    except Exception:
        # Optional input: a bad keogram (bomb, truncated, wrong format) means frames only.
        return [None] * len(chosen)


class GarageMedia:
    """Production reader: the `weather-media` reader key, a scratch directory and FFmpeg."""

    def __init__(self, store: ObjectStore, scratch: Path | None = None) -> None:
        self.store, self.scratch = store, scratch

    async def prepare(self, source: Source, windows: Sequence[Window]) -> list[WindowMedia]:
        with tempfile.TemporaryDirectory(dir=self.scratch) as work:
            root = Path(work)
            await _download(self.store, source.web, root / "web.mp4")
            keogram = None
            if source.keogram is not None and 0 < source.keogram.bytes <= MAX_KEOGRAM_BYTES:
                try:
                    await _download(self.store, source.keogram, root / "keogram")
                    keogram = (root / "keogram").read_bytes()
                except MediaError:
                    keogram = None
            picks = [(window, samples(window)) for window in windows if window.available]
            indices = sorted({index for _, chosen in picks for index in chosen})
            frames_dir = root / "frames"
            frames_dir.mkdir()
            decoded = await asyncio.to_thread(sample_frames, root / "web.mp4", indices, frames_dir)
            by_index = dict(zip(indices, decoded, strict=True))
        columns = keogram_windows(keogram, windows, [window for window, _ in picks])
        return [
            WindowMedia(window, tuple((i, by_index[i]) for i in chosen), column)
            for (window, chosen), column in zip(picks, columns, strict=True)
        ]
