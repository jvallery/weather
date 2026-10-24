# SPDX-License-Identifier: MIT
"""Stage (intent, conditional create, HEAD + GET-and-hash readback) then commit a product.

Nothing is visible in the catalog until every object is verified. Keys come from local
hashes, never ETags. An existing key with a different size or hash fails loudly and is
never overwritten. A failure is confined to its own product: callers run one product at a
time per camera, and a multipart upload is always aborted on error.
"""

import asyncio
import dataclasses
import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from . import catalog, metrics
from .model import (
    Committed,
    Product,
    ProductError,
    ProductFile,
    Staged,
    StagedObject,
    role_class,
)
from .objects import READ_CHUNK, ObjectStore, StoreError

LOG = logging.getLogger("weather.publish")


class ObjectConflict(RuntimeError):
    """An object already exists under the key with a different size or hash."""

    code = "object_conflict"


class ReadbackFailed(RuntimeError):
    code = "readback"

    def __init__(self, message: str, reason: str = "object_mismatch") -> None:
        super().__init__(message)
        # Orphan reason: the object is absent, or present with other bytes.
        self.reason = reason


def _hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def describe(product: Product, file: ProductFile) -> StagedObject:
    """Hash the local bytes; the content address is derived from this hash alone."""
    if file.data is not None:
        sha256, size = hashlib.sha256(file.data).hexdigest(), len(file.data)
    else:
        assert file.path is not None
        sha256, size = await asyncio.to_thread(_hash_file, file.path)
    if size <= 0:
        raise ReadbackFailed("Empty object")
    return StagedObject(
        role=file.role,
        asset_class=role_class(product.kind, file.role),
        sha256=sha256,
        size=size,
        extension=file.extension,
        width=file.width,
        height=file.height,
    )


async def verify(store: ObjectStore, item: StagedObject, *, keep: bool = False) -> bytes | None:
    """HEAD (size and stored sha256), then GET and hash every byte."""
    info = await store.head(item.key)
    if info is None:
        metrics.READBACK_FAILURES.labels(item.asset_class).inc()
        raise ReadbackFailed("Object missing", "missing_object")
    if info.size != item.size or info.sha256 != item.sha256:
        metrics.READBACK_FAILURES.labels(item.asset_class).inc()
        raise ReadbackFailed("HEAD does not match the local hash")
    size, sha256, data = await store.read(item.key, keep=keep)
    if size != item.size or sha256 != item.sha256:
        metrics.READBACK_FAILURES.labels(item.asset_class).inc()
        raise ReadbackFailed("GET bytes do not match the local hash")
    return data


async def upload(store: ObjectStore, item: StagedObject, file: ProductFile) -> None:
    existing = await store.head(item.key)
    if existing is not None:
        if existing.size != item.size or existing.sha256 != item.sha256:
            raise ObjectConflict("Existing object differs; it is never overwritten")
        return
    created = await store.put(
        item.key,
        sha256=item.sha256,
        size=item.size,
        content_type=item.content_type,
        path=file.path,
        data=file.data,
    )
    if not created:
        # A concurrent writer created it first; the readback below decides.
        LOG.info("Object already created by a concurrent writer")


class Publisher:
    def __init__(self, engine: AsyncEngine, stores: Mapping[str, ObjectStore]) -> None:
        self._engine = engine
        self._stores = stores

    def _store(self, item: StagedObject) -> ObjectStore:
        try:
            return self._stores[item.bucket]
        except KeyError:
            raise StoreError("bucket configuration") from None

    async def stage(
        self, product: Product, *, expected_revision: int, deadline: datetime
    ) -> Staged:
        product.validate()
        objects = tuple([await describe(product, file) for file in product.files])
        files = {file.role: file for file in product.files}
        async with self._engine.begin() as connection:
            intent_id, fence = await catalog.register_intent(
                connection,
                product,
                objects,
                expected_revision=expected_revision,
                deadline=deadline,
            )
        sidecar = None
        for item in objects:
            await upload(self._store(item), item, files[item.role])
        for item in objects:
            data = await verify(self._store(item), item, keep=item.asset_class == "sidecar")
            if item.asset_class == "sidecar":
                sidecar = data
            metrics.OBJECTS.labels(item.asset_class).inc()
            metrics.BYTES.labels(item.asset_class).inc(item.size)
        return Staged(intent_id, fence, expected_revision, product, objects, sidecar)

    async def commit(self, staged: Staged) -> Committed:
        async with self._engine.begin() as connection:
            return await catalog.commit(connection, staged)

    async def publish(
        self, product: Product, *, expected_revision: int, deadline: datetime
    ) -> Committed:
        """Stage and commit in one call; raises (and counts) a per-product failure."""
        product.validate()  # before the camera becomes a metric label
        metrics.ATTEMPTS.labels(product.kind, product.camera_id).inc()
        try:
            staged = await self.stage(
                product, expected_revision=expected_revision, deadline=deadline
            )
            return await self.commit(staged)
        except BaseException as error:
            reason = getattr(error, "code", None) or (
                "store" if isinstance(error, StoreError) else "error"
            )
            metrics.FAILURES.labels(product.kind, product.camera_id, reason).inc()
            LOG.warning("Publish failed (%s, %s)", product.kind, type(error).__name__)
            raise

    async def publish_sidecar(
        self,
        *,
        kind: str,
        camera_id: str,
        cycle_date: date,
        segment: str,
        document: dict[str, object],
        deadline: datetime,
    ) -> Committed:
        """Revision N+1 of a video or keogram that replaces only the sidecar."""
        if kind not in {"video", "keogram"}:
            raise ProductError("Only segment products carry sidecars")
        identity = Product(
            kind=kind,  # type: ignore[arg-type]
            camera_id=camera_id,
            key=Product.segment_key(cycle_date, segment),
            source_start=datetime.min.replace(tzinfo=UTC),
            source_end=datetime.min.replace(tzinfo=UTC),
            public_not_before=datetime.min.replace(tzinfo=UTC),
            state="unavailable",
            reason=None,
            files=(),
            cycle_date=cycle_date if kind == "video" else None,
            segment=segment if kind == "video" else None,
        )
        async with self._engine.connect() as connection:
            now = await catalog.current(connection, identity)
        if now.state is None or now.source is None or now.public_not_before is None:
            raise catalog.RevisionConflict("Nothing published to enrich")
        enriched = dataclasses.replace(
            identity,
            state=now.state,  # type: ignore[arg-type]
            reason=now.reason,
            source_start=now.source[0],
            source_end=now.source[1],
            public_not_before=now.public_not_before,
            files=(ProductFile.sidecar(document),),
            keep=tuple(sorted(role for role in now.assets if role != "sidecar")),
            frames=now.frames,
        )
        return await self.publish(enriched, expected_revision=now.revision, deadline=deadline)
