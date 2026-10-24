# SPDX-License-Identifier: MIT
"""Mounted configuration; endpoints and credentials are never serialized."""

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CAMERAS = {"skycam_east", "skycam_west", "frontyard_1"}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class CaptureConfig:
    camera: str
    staging: Path
    pod_uid: str
    host: str = field(repr=False)
    port: int = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    geometry: tuple[int, int] = (4096, 1856)
    cadence_ms: int = 1000
    phase_ms: int = 0
    chunk_seconds: int = 60
    connect_seconds: float = 1.0
    read_seconds: float = 3.0
    total_seconds: float = 5.0
    max_bytes: int = 16 * 1024 * 1024
    cache_allowance_ms: int | None = None
    calibration_revision: str | None = None
    https: bool = False
    metrics_port: int = 9000

    def __post_init__(self) -> None:
        if self.camera not in CAMERAS or not SAFE_ID.fullmatch(self.pod_uid):
            raise ValueError("Invalid camera or pod identity")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            raise ValueError("Camera destination must be a literal address") from None
        if not 1 <= self.port <= 65535 or not 1 <= self.metrics_port <= 65535:
            raise ValueError("Invalid configured port")
        if (
            not self.username
            or not self.password
            or any(c in self.username + self.password for c in "\r\n\x00")
        ):
            raise ValueError("Invalid mounted camera credentials")
        if not self.staging.is_absolute() or not 1 <= self.chunk_seconds <= 300:
            raise ValueError("Invalid staging or chunk bound")
        if not 1000 <= self.cadence_ms <= 3600000 or not 0 <= self.phase_ms < self.cadence_ms:
            raise ValueError("Invalid capture schedule")
        if not 0 < self.connect_seconds <= self.total_seconds <= 5:
            raise ValueError("Invalid total capture deadline")
        if not 0 < self.read_seconds <= self.total_seconds or not 1 <= self.max_bytes <= 16777216:
            raise ValueError("Invalid response bound")
        if len(self.geometry) != 2 or any(not 1 <= x <= 16384 for x in self.geometry):
            raise ValueError("Invalid allowed geometry")
        if self.cache_allowance_ms is not None and (
            self.cache_allowance_ms < 0 or not self.calibration_revision
        ):
            raise ValueError("Finite cache allowance requires calibration revision")
        if self.calibration_revision is not None and not SAFE_ID.fullmatch(
            self.calibration_revision
        ):
            raise ValueError("Invalid calibration revision")

    def public_settings(self) -> dict[str, object]:
        """Only non-secret operational settings enter the chunk manifest."""
        return {
            "camera": self.camera,
            "geometry": self.geometry,
            "cadence_ms": self.cadence_ms,
            "phase_ms": self.phase_ms,
            "chunk_seconds": self.chunk_seconds,
            "connect_seconds": float(self.connect_seconds),
            "read_seconds": float(self.read_seconds),
            "total_seconds": float(self.total_seconds),
            "max_bytes": self.max_bytes,
            "cache_allowance_ms": self.cache_allowance_ms,
            "calibration_revision": self.calibration_revision,
        }

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.public_settings(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def load(cls, camera: str, config_dir: Path, secret_dir: Path) -> CaptureConfig:
        try:
            config = json.loads((config_dir / "capture.json").read_text())
            config.setdefault(
                "phase_ms", {"skycam_east": 0, "skycam_west": 333, "frontyard_1": 667}[camera]
            )
            secrets = {
                name: (secret_dir / name).read_text().rstrip("\r\n")
                for name in ["CAMERA_HOST", "CAMERA_PORT", "CAMERA_USER", "CAMERA_PASSWORD"]
            }
            return cls(
                camera=camera,
                staging=Path(config.pop("staging")),
                pod_uid=(config_dir / "POD_UID").read_text().strip(),
                host=secrets["CAMERA_HOST"],
                port=int(secrets["CAMERA_PORT"]),
                username=secrets["CAMERA_USER"],
                password=secrets["CAMERA_PASSWORD"],
                geometry=tuple(config.pop("geometry", [4096, 1856])),
                **config,
            )
        except OSError, ValueError, TypeError, KeyError:
            raise ValueError("Mounted capture configuration is invalid") from None
