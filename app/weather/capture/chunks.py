# SPDX-License-Identifier: MIT
"""Exclusive USTAR writes and a tarfile reader that binds every frame; no extraction."""

import hashlib
import io
import json
import os
import re
import tarfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SAFE_ID, CaptureConfig
from .records import validate_record
from .timing import Slot, utc_text

MAX_MANIFEST_BYTES = 1024 * 1024
ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,88}\Z")


class IOHealth:
    """Read independently by the health HTTP thread even if NFS blocks the collector."""

    def __init__(self) -> None:
        self.started_ns: int | None = None

    @contextmanager
    def operation(self) -> Iterator[None]:
        self.started_ns = time.monotonic_ns()
        try:
            yield
        finally:
            self.started_ns = None

    def healthy(self, now_ns: int | None = None) -> bool:
        started = self.started_ns
        return started is None or (now_ns or time.monotonic_ns()) - started <= 30_000_000_000


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ChunkWriter:
    def __init__(
        self,
        config: CaptureConfig,
        incarnation: str,
        sequence: int,
        first: Slot,
        health: IOHealth,
        fsync: Callable[[int], None] = os.fsync,
    ) -> None:
        if not SAFE_ID.fullmatch(incarnation) or sequence < 0:
            raise ValueError("Invalid chunk identity")
        self.config, self.health, self.fsync = config, health, fsync
        self.identity = f"{config.pod_uid}-{incarnation}-{sequence}"
        hour = utc_text(first.scheduled_utc_ns)[:13].replace(":", "") + "Z"
        self.hour = hour
        self.clock_epoch = first.clock_epoch
        self.started_mono_ns = first.scheduled_mono_ns
        self.tmp_dir = config.staging / "tmp" / config.camera
        self.sealed_dir = config.staging / "sealed" / config.camera / hour
        window_end = min(
            first.scheduled_utc_ns + config.chunk_seconds * 1_000_000_000,
            (first.scheduled_utc_ns // 3_600_000_000_000 + 1) * 3_600_000_000_000,
        )
        self.window_start_ns, self.window_end_ns = first.scheduled_utc_ns, window_end
        self.path = self.tmp_dir / (f"{first.scheduled_utc_ns}_{window_end}__{self.identity}.tar")
        self.destination = self.sealed_dir / f"{self.identity}.tar"
        self._lines: list[bytes] = []
        self._manifest_bytes = 0
        self._closed = False
        self.renamed = False
        with health.operation():
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            self.sealed_dir.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("xb", buffering=0)
            self._tar = tarfile.open(fileobj=self._file, mode="w", format=tarfile.USTAR_FORMAT)
            self._line(
                {
                    "record_type": "chunk_header",
                    "schema_version": 2,
                    "camera": config.camera,
                    "pod_uid": config.pod_uid,
                    "incarnation": incarnation,
                    "sequence": sequence,
                    "slot_window_start_utc": utc_text(first.scheduled_utc_ns),
                    "slot_window_end_utc": utc_text(window_end),
                    "config_hash": config.config_hash,
                    "settings": config.public_settings(),
                }
            )

    def _line(self, record: dict[str, object]) -> None:
        validate_record(record)
        data = (
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            + b"\n"
        )
        if self._manifest_bytes + len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("Chunk manifest bound exceeded")
        self._lines.append(data)
        self._manifest_bytes += len(data)

    def append(self, record: dict[str, object], body: bytes | None = None) -> None:
        if self._closed:
            raise ValueError("Chunk is closed")
        if body is None and record.get("status") == "captured":
            raise ValueError("Captured attempt needs original frame bytes")
        with self.health.operation():
            if body is not None:
                attempt_id = record.get("attempt_id")
                if not isinstance(attempt_id, str) or not ATTEMPT_ID.fullmatch(attempt_id):
                    raise ValueError("Invalid attempt identity")
                if len(body) > self.config.max_bytes:
                    raise ValueError("Frame exceeds configured bound")
                member = tarfile.TarInfo(f"frames/{attempt_id}.jpg")
                member.size, member.mode, member.mtime = len(body), 0o600, 0
                self._tar.addfile(member, io.BytesIO(body))
                record = {
                    **record,
                    "member": {
                        "name": member.name,
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "width": self.config.geometry[0],
                        "height": self.config.geometry[1],
                    },
                }
            # No per-attempt fsync: nothing salvages a tmp file, so durability matters only at seal.
            self._line(record)

    def seal(self) -> Path:
        if self._closed:
            raise ValueError("Chunk is closed")
        with self.health.operation():
            manifest = b"".join(self._lines)
            member = tarfile.TarInfo("manifest.jsonl")
            member.size, member.mode, member.mtime = len(manifest), 0o600, 0
            self._tar.addfile(member, io.BytesIO(manifest))
            self._tar.close()
            self._file.flush()
            self.fsync(self._file.fileno())
            self._file.close()
            # Pod UID + random incarnation + increasing sequence gives each writer its own name.
            if self.destination.exists():
                raise FileExistsError("Sealed identity already exists")
            os.rename(self.path, self.destination)
            self.renamed = True
            _fsync_dir(self.sealed_dir)
            _fsync_dir(self.tmp_dir)
            self._closed = True
        return self.destination

    def abandon(self) -> None:
        # Nothing salvages tmp, so remove this writer's own file. Never touch another writer's.
        self._closed = True
        with self.health.operation():
            try:
                self._file.close()
            except OSError:
                pass
            if not self.renamed:
                try:
                    self.path.unlink()
                except OSError:
                    pass


def new_incarnation() -> str:
    return uuid.uuid4().hex


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def read_chunk(
    path: Path, max_frame_bytes: int = 16777216, *, hash_frames: bool = True
) -> list[dict[str, Any]]:
    """Regular frame members, then one final manifest binding each by length and sha256.

    With `hash_frames=False` (#146) the frame bodies are not read: the TAR structure,
    padding, termination and manifest are still validated and each member is bound by name
    and length, but its SHA-256 is left to the reader (`pipeline.intake.read_frame`
    re-verifies every frame it uses). A restart then reads headers, not gigabytes."""
    frames: dict[str, tuple[int, str | None]] = {}
    manifest: bytes | None = None
    end = 0
    try:
        with tarfile.open(path, mode="r:") as archive, path.open("rb") as raw:
            for member in archive:
                # Contiguous plain headers exclude PAX/GNU extension blocks; types exclude sparse.
                if (
                    manifest is not None
                    or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or member.offset != end
                    or member.offset_data != end + tarfile.BLOCKSIZE
                    or member.name in frames
                ):
                    raise ValueError("Only regular members before a final manifest are accepted")
                padding = -member.size % tarfile.BLOCKSIZE
                raw.seek(member.offset_data + member.size)
                if raw.read(padding) != bytes(padding):
                    raise ValueError("Nonzero member padding")
                end = member.offset_data + member.size + padding
                source = archive.extractfile(member)
                assert source is not None
                if member.name == "manifest.jsonl":
                    if not 0 < member.size <= MAX_MANIFEST_BYTES:
                        raise ValueError("Manifest bound exceeded")
                    manifest = source.read()
                    continue
                if not member.name.startswith("frames/") or not member.name.endswith(".jpg"):
                    raise ValueError("Unexpected member")
                attempt_id = member.name[7:-4]
                if not ATTEMPT_ID.fullmatch(attempt_id) or not 0 < member.size <= max_frame_bytes:
                    raise ValueError("Invalid frame member")
                if not hash_frames:
                    frames[member.name] = member.size, None
                    continue
                digest = hashlib.sha256()
                while part := source.read(65536):
                    digest.update(part)
                frames[member.name] = member.size, digest.hexdigest()
            # tarfile stops silently at a bad header; require two zero blocks and only zero fill.
            # The writer adds two zero blocks and then pads to a whole record, so the tail is
            # 2 blocks plus under one record, ending exactly on a record boundary.
            raw.seek(end)
            limit = tarfile.RECORDSIZE + 2 * tarfile.BLOCKSIZE
            tail = raw.read(limit)
            if (
                not 2 * tarfile.BLOCKSIZE <= len(tail) < limit
                or (end + len(tail)) % tarfile.RECORDSIZE
                or any(tail)
            ):
                raise ValueError("Incomplete TAR termination")
    except tarfile.TarError, EOFError:
        raise ValueError("Invalid TAR archive") from None
    if manifest is None or not manifest.endswith(b"\n"):
        raise ValueError("Missing complete manifest")
    lines = manifest.splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError("Invalid JSONL manifest")
    records = [
        json.loads(
            line,
            object_pairs_hook=_unique_json,
            parse_constant=_reject_constant,
        )
        for line in lines
    ]
    for record in records:
        validate_record(record)
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Manifest records must be objects")
    if records[0].get("record_type") != "chunk_header" or records[0].get("schema_version") != 2:
        raise ValueError("Invalid chunk header")
    window_start = datetime.fromisoformat(records[0]["slot_window_start_utc"])
    window_end = datetime.fromisoformat(records[0]["slot_window_end_utc"])
    seen: set[str] = set()
    for record in records[1:]:
        if record.get("record_type") == "unsealed_range":
            if record.get("classification") not in {"unsealed_uncertain", "seal_uncertain"}:
                raise ValueError("Invalid uncertain range")
            continue
        if record.get("record_type") == "skipped_range":
            if (
                record.get("failure_class") != "overdue"
                or not isinstance(record.get("slot_count"), int)
                or record["slot_count"] < 1
            ):
                raise ValueError("Invalid skipped range")
            continue
        if record.get("record_type") == "loss_range":
            if record.get("failure_class") != "persist_failed":
                raise ValueError("Invalid loss range")
            continue
        if record.get("camera") != records[0]["camera"] or not (
            window_start <= datetime.fromisoformat(record["slot_at_utc"]) < window_end
        ):
            raise ValueError("Attempt does not belong to the camera/window")
        attempt_id = record.get("attempt_id")
        if not isinstance(attempt_id, str) or attempt_id in seen:
            raise ValueError("Duplicate or missing attempt identity")
        seen.add(attempt_id)
        member = record.get("member")
        if record.get("status") == "captured":
            expected_name = f"frames/{attempt_id}.jpg"
            if not isinstance(member, dict) or member.get("name") != expected_name:
                raise ValueError("Frame binding differs")
            found = frames.pop(expected_name, None)
            sha256 = member.get("sha256")
            if (
                found is None
                or found[0] != member.get("bytes")
                or not isinstance(sha256, str)
                or not SHA256_HEX.fullmatch(sha256)
                or (found[1] is not None and found[1] != sha256)
            ):
                raise ValueError("Frame hash or length differs")
        elif member is not None or record.get("status") not in {"skipped", "request_failed"}:
            raise ValueError("Invalid non-captured attempt")
    if frames:
        raise ValueError("Unbound frame members")
    return records
