# SPDX-License-Identifier: MIT
"""Synthetic sealed chunks through the real collector writer: tests and the throughput Job.

No camera, address or credential is involved; the writer's configuration uses a loopback
literal and placeholder credentials that never leave this process.
"""

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from weather.capture.chunks import ChunkWriter, IOHealth
from weather.capture.config import CaptureConfig
from weather.capture.timing import Slot, request_timing, utc_text

NS = 1_000_000_000


@dataclass(frozen=True)
class Frame:
    slot_ns: int
    jpeg: bytes
    # Request midpoint offset from the slot; the request spans +-latency around it.
    offset_ns: int = 100_000_000
    latency_ns: int = 50_000_000
    extra: tuple[tuple[str, object], ...] = ()


def jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def solid(width: int, height: int, value: int | tuple[int, int, int]) -> bytes:
    pixel = (value, value, value) if isinstance(value, int) else value
    return jpeg(np.full((height, width, 3), pixel, dtype=np.uint8), quality=100)


def sky(width: int, height: int, seed: int) -> np.ndarray:
    """A smooth gradient with drifting texture and sensor-like noise."""
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    base = 60 + 140 * y + 20 * np.sin(6.28 * (x * 3 + seed / 97))
    noise = rng.normal(0, 3, (height, width)).astype(np.float32)
    channel = np.clip(base + noise, 0, 255)
    return np.stack([channel * 0.8, channel * 0.9, channel], axis=-1).astype(np.uint8)


def corrupt_entropy(data: bytes) -> bytes:
    """Overwrite entropy-coded data after SOS with invalid (all-ones) Huffman codes."""
    sos = data.index(b"\xff\xda")
    start = sos + 2 + int.from_bytes(data[sos + 2 : sos + 4], "big")
    body = bytearray(data)
    for index in range(start + 2, len(body) - 4, 2):
        body[index], body[index + 1] = 0xFF, 0x00
    return bytes(body)


def write_chunks(
    staging: Path,
    camera: str,
    frames: Sequence[Frame],
    *,
    geometry: tuple[int, int],
    cadence_ms: int = 1000,
    phase_ms: int = 0,
    chunk_seconds: int = 60,
    pod: str = "synthetic",
    extra_records: Iterable[dict[str, object]] = (),
    first_sequence: int = 0,
) -> list[Path]:
    """Seal frames (sorted by slot) into chunks exactly as a collector would."""
    config = CaptureConfig(
        camera=camera,
        staging=staging,
        pod_uid=pod,
        host="127.0.0.1",
        port=8080,
        username="synthetic",
        password="synthetic",
        geometry=geometry,
        cadence_ms=cadence_ms,
        phase_ms=phase_ms,
        chunk_seconds=chunk_seconds,
    )
    paths: list[Path] = []
    writer: ChunkWriter | None = None
    sequence = first_sequence
    extras = list(extra_records)
    for index, frame in enumerate(sorted(frames, key=lambda item: item.slot_ns)):
        if (
            writer is not None
            and not writer.window_start_ns <= frame.slot_ns < writer.window_end_ns
        ):
            paths.append(writer.seal())
            writer = None
        if writer is None:
            slot = Slot(f"{pod}-{sequence}", 0, frame.slot_ns, "clock")
            writer = ChunkWriter(config, "synthetic", sequence, slot, IOHealth())
            sequence += 1
            for extra in extras:
                writer.append(extra)
            extras = []
        midpoint = frame.slot_ns + frame.offset_ns
        record: dict[str, object] = {
            "record_type": "capture_attempt",
            "attempt_id": f"{pod}-{sequence}-{index}",
            "slot_id": f"{pod}-{sequence}-{index}",
            "slot_at_utc": utc_text(frame.slot_ns),
            "clock_epoch": "clock",
            "camera": camera,
            "status": "captured",
            "failure_class": None,
            "member": None,
            **request_timing(
                midpoint - frame.latency_ns,
                midpoint + frame.latency_ns,
                0,
                2 * frame.latency_ns,
                None,
                None,
            ),
        }
        record.update(dict(frame.extra))
        writer.append(record, frame.jpeg)
    if writer is not None:
        paths.append(writer.seal())
    return paths
