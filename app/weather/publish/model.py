# SPDX-License-Identifier: MIT
"""Products, their files and the fixed class/bucket/role rules the catalog also enforces."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

MEDIA_BUCKET = "weather-media"
ORIGINALS_BUCKET = "weather-originals"
# The database CHECKs the same margin: capture upper bound + 600 s + 30 s skew.
EMBARGO = timedelta(seconds=630)
SIDECAR_LIMIT = 1024 * 1024

ProductKind = Literal["video", "keogram", "still"]
State = Literal["published", "partial", "unavailable"]
CONTENT_TYPES = {
    "mp4": "video/mp4",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "png": "image/png",
    "json": "application/json",
}
# Asset class -> allowed extensions. Originals are byte-for-byte camera JPEGs.
CLASS_EXTENSIONS = {
    "video": {"mp4"},
    "poster": {"jpg", "webp"},
    "keogram": {"png", "webp", "jpg"},
    "still": {"jpg", "webp"},
    "sidecar": {"json"},
    "original": {"jpg"},
}
# Product -> role base -> asset class. A role may carry a suffix (poster_960, keogram_thumb).
ROLE_CLASSES: dict[str, dict[str, str]] = {
    "video": {
        "native": "video",
        "web": "video",
        "mobile": "video",
        "poster": "poster",
        "sidecar": "sidecar",
    },
    "keogram": {"keogram": "keogram", "sidecar": "sidecar"},
    "still": {"original": "original", "still": "still"},
}
VIDEO_MEDIA = ("native", "web", "mobile", "poster")
ROLE = re.compile(r"[a-z]+(_[a-z0-9]+)?")
CAMERA = re.compile(r"[a-z][a-z0-9_]{0,31}")
REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Sidecars are public: they must never name a bucket, an object key, a recipe's private
# configuration hash or anything about originals.
PRIVATE_KEYS = {"bucket", "object_key", "config_hash"}
PRIVATE_TEXT = re.compile(r"weather-originals|original/[0-9a-f]{64}")


class ProductError(ValueError):
    """The product description is invalid; nothing was uploaded or committed."""


def bucket_for(asset_class: str) -> str:
    return ORIGINALS_BUCKET if asset_class == "original" else MEDIA_BUCKET


def object_key(asset_class: str, sha256: str, extension: str) -> str:
    return f"{asset_class}/{sha256}.{extension}"


def role_class(product: str, role: str) -> str:
    if not ROLE.fullmatch(role) or len(role) > 16:
        raise ProductError("Invalid role")
    base = role.split("_", 1)[0]
    try:
        asset_class = ROLE_CLASSES[product][base]
    except KeyError:
        raise ProductError("Role not allowed for this product") from None
    if base in {"native", "web", "mobile", "sidecar", "original"} and role != base:
        raise ProductError("Role does not take a suffix")
    return asset_class


def check_public_document(document: Any, path: str = "$") -> None:
    """Reject a public JSON document that names private storage or originals."""
    if isinstance(document, dict):
        for key, value in document.items():
            if not isinstance(key, str):
                raise ProductError("Sidecar keys must be strings")
            lowered = key.lower()
            if lowered in PRIVATE_KEYS or lowered.startswith("original"):
                raise ProductError(f"Sidecar exposes a private field at {path}")
            check_public_document(value, f"{path}.{key}")
    elif isinstance(document, list):
        for index, value in enumerate(document):
            check_public_document(value, f"{path}[{index}]")
    elif isinstance(document, str) and PRIVATE_TEXT.search(document):
        raise ProductError(f"Sidecar exposes private storage at {path}")


@dataclass(frozen=True)
class ProductFile:
    """One file of a product: a local path, or in-memory bytes for a JSON sidecar."""

    role: str
    extension: str
    path: Path | None = None
    data: bytes | None = None
    width: int | None = None
    height: int | None = None

    @classmethod
    def sidecar(cls, document: dict[str, Any]) -> ProductFile:
        check_public_document(document)
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        if len(data) > SIDECAR_LIMIT:
            raise ProductError("Sidecar too large")
        return cls(role="sidecar", extension="json", data=data)


@dataclass(frozen=True)
class Product:
    kind: ProductKind
    camera_id: str
    key: str
    source_start: datetime
    source_end: datetime
    public_not_before: datetime
    state: State
    reason: str | None
    files: tuple[ProductFile, ...]
    # Roles carried unchanged from the current revision (e.g. media for a sidecar revision).
    keep: tuple[str, ...] = ()
    # Video identity; None for keograms and stills.
    cycle_date: date | None = None
    segment: str | None = None
    frames: int | None = None

    @staticmethod
    def segment_key(cycle_date: date, segment: str) -> str:
        if segment not in {"daytime", "overnight"}:
            raise ProductError("Invalid segment")
        return f"{cycle_date.isoformat()}/{segment}"

    @classmethod
    def video(
        cls,
        *,
        camera_id: str,
        cycle_date: date,
        segment: str,
        starts_at: datetime,
        ends_at: datetime,
        frames: int,
        public_not_before: datetime,
        state: State,
        reason: str | None,
        files: tuple[ProductFile, ...],
        keep: tuple[str, ...] = (),
    ) -> Product:
        return cls(
            kind="video",
            camera_id=camera_id,
            key=cls.segment_key(cycle_date, segment),
            source_start=starts_at,
            source_end=ends_at,
            public_not_before=public_not_before,
            state=state,
            reason=reason,
            files=files,
            keep=keep,
            cycle_date=cycle_date,
            segment=segment,
            frames=frames,
        )

    @classmethod
    def keogram(
        cls,
        *,
        camera_id: str,
        cycle_date: date,
        segment: str,
        source_start: datetime,
        source_end: datetime,
        public_not_before: datetime,
        files: tuple[ProductFile, ...],
        state: State = "published",
        reason: str | None = None,
    ) -> Product:
        return cls(
            kind="keogram",
            camera_id=camera_id,
            key=cls.segment_key(cycle_date, segment),
            source_start=source_start,
            source_end=source_end,
            public_not_before=public_not_before,
            state=state,
            reason=reason,
            files=files,
        )

    @classmethod
    def still(
        cls,
        *,
        camera_id: str,
        slot_start: datetime,
        source_start: datetime,
        source_end: datetime,
        public_not_before: datetime,
        files: tuple[ProductFile, ...],
        state: State = "published",
        reason: str | None = None,
    ) -> Product:
        if slot_start.utcoffset() != timedelta(0) or slot_start.minute or slot_start.second:
            raise ProductError("Still slots are whole UTC hours")
        return cls(
            kind="still",
            camera_id=camera_id,
            key=slot_start.strftime("%Y-%m-%dT%H:00Z"),
            source_start=source_start,
            source_end=source_end,
            public_not_before=public_not_before,
            state=state,
            reason=reason,
            files=files,
        )

    def validate(self) -> None:
        """Fail before any upload; the database re-checks the privacy rules."""
        if not CAMERA.fullmatch(self.camera_id):
            raise ProductError("Invalid camera")
        for instant in (self.source_start, self.source_end, self.public_not_before):
            if instant.tzinfo is None:
                raise ProductError("Times must be timezone-aware")
        if not self.source_start < self.source_end:
            raise ProductError("Empty source interval")
        if self.public_not_before < self.source_end + EMBARGO:
            raise ProductError("Embargo ends before source end + 630 s")
        if (self.state == "published") != (self.reason is None) or (
            self.reason is not None and not REASON.fullmatch(self.reason)
        ):
            raise ProductError("Published products have no reason; others need one")
        roles = [file.role for file in self.files] + list(self.keep)
        if len(roles) != len(set(roles)) or not roles:
            raise ProductError("Roles must be unique and nonempty")
        for file in self.files:
            asset_class = role_class(self.kind, file.role)
            if file.extension not in CLASS_EXTENSIONS[asset_class]:
                raise ProductError("Extension not allowed for this class")
            if (file.path is None) == (file.data is None):
                raise ProductError("A file has either a path or in-memory data")
            if (asset_class == "sidecar") != (file.data is not None):
                raise ProductError("Only sidecars are in-memory")
            if file.data is not None:
                # Every sidecar path is checked, not only ProductFile.sidecar().
                if len(file.data) > SIDECAR_LIMIT:
                    raise ProductError("Sidecar too large")
                try:
                    document = json.loads(file.data)
                except ValueError:
                    raise ProductError("Sidecar is not JSON") from None
                if not isinstance(document, dict):
                    raise ProductError("Sidecar is not a JSON object")
                check_public_document(document)
            if (file.width is None) != (file.height is None) or (
                file.width is not None and (file.width <= 0 or (file.height or 0) <= 0)
            ):
                raise ProductError("Invalid geometry")
        for role in self.keep:
            role_class(self.kind, role)
        if self.kind == "video":
            if self.cycle_date is None or self.segment is None or self.frames is None:
                raise ProductError("Videos need their segment identity")
            if not 0 <= self.frames <= 7200:
                raise ProductError("Invalid frame count")
            media = {role for role in roles if role.split("_", 1)[0] in VIDEO_MEDIA}
            if self.state != "unavailable" and not set(VIDEO_MEDIA) <= media:
                raise ProductError("Published and partial videos need all renditions and a poster")
            if self.state == "unavailable" and media:
                raise ProductError("Unavailable videos expose no media")
        if self.kind == "still" and "original" in roles and self.keep:
            raise ProductError("A still revision re-archives no original")


@dataclass(frozen=True)
class StagedObject:
    role: str
    asset_class: str
    sha256: str
    size: int
    extension: str
    width: int | None
    height: int | None

    @property
    def bucket(self) -> str:
        return bucket_for(self.asset_class)

    @property
    def key(self) -> str:
        return object_key(self.asset_class, self.sha256, self.extension)

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.extension]

    def manifest(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "class": self.asset_class,
            "sha256": self.sha256,
            "bytes": self.size,
            "extension": self.extension,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_manifest(cls, value: dict[str, Any]) -> StagedObject:
        return cls(
            role=str(value["role"]),
            asset_class=str(value["class"]),
            sha256=str(value["sha256"]),
            size=int(value["bytes"]),
            extension=str(value["extension"]),
            width=value["width"],
            height=value["height"],
        )


@dataclass(frozen=True)
class Staged:
    """An intent whose objects are uploaded and read back, ready to commit."""

    intent_id: Any
    fence: int
    expected_revision: int
    product: Product
    objects: tuple[StagedObject, ...]
    sidecar: bytes | None


@dataclass(frozen=True)
class Committed:
    publication_id: Any
    revision: int
    assets: dict[str, Any]
    video_id: Any | None
    duplicate: bool = False


def product_manifest(product: Product, objects: tuple[StagedObject, ...]) -> dict[str, Any]:
    """Everything the reconciler needs to commit the intent without the original process."""
    return {
        "kind": product.kind,
        "camera_id": product.camera_id,
        "key": product.key,
        "source_start": product.source_start.isoformat(),
        "source_end": product.source_end.isoformat(),
        "public_not_before": product.public_not_before.isoformat(),
        "state": product.state,
        "reason": product.reason,
        "keep": list(product.keep),
        "cycle_date": product.cycle_date.isoformat() if product.cycle_date else None,
        "segment": product.segment,
        "frames": product.frames,
        "objects": [item.manifest() for item in objects],
    }


def product_from_manifest(value: dict[str, Any]) -> tuple[Product, tuple[StagedObject, ...]]:
    objects = tuple(StagedObject.from_manifest(item) for item in value["objects"])
    product = Product(
        kind=value["kind"],
        camera_id=value["camera_id"],
        key=value["key"],
        source_start=datetime.fromisoformat(value["source_start"]),
        source_end=datetime.fromisoformat(value["source_end"]),
        public_not_before=datetime.fromisoformat(value["public_not_before"]),
        state=value["state"],
        reason=value["reason"],
        files=(),
        keep=tuple(value["keep"]),
        cycle_date=date.fromisoformat(value["cycle_date"]) if value["cycle_date"] else None,
        segment=value["segment"],
        frames=value["frames"],
    )
    return product, objects
