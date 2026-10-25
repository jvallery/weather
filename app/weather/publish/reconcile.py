# SPDX-License-Identifier: MIT
"""Resolve what a crash left behind: commit a still-valid intent or mark it an orphan,
re-upload a lost sidecar from its inline catalog copy, and abort stale multipart uploads.

Orphans are only marked here; #19's GC deletes their objects after the grace period, and
only when no asset row references the key. The reconciler never creates a second record:
every commit goes through the same fenced, revision-checked catalog.commit.
"""

import dataclasses
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from . import catalog, metrics
from .model import StagedObject
from .objects import ObjectStore, StoreError
from .publisher import ReadbackFailed, verify

# An active publisher stages (uploads and reads back) well inside this; younger intents are
# left alone. A publisher still staging past it may find its intent orphaned; it then fails
# with IntentResolved and its caller retries with a new intent. Nothing inconsistent results.
INTENT_GRACE = timedelta(hours=1)
MULTIPART_STALE = timedelta(hours=6)
BATCH = 100
SIDECAR_PAGE = 500


async def _orphan(engine: AsyncEngine, intent_id: object, reason: str) -> bool:
    async with engine.begin() as connection:
        marked = await catalog.mark_orphan(connection, intent_id, reason)  # type: ignore[arg-type]
    if marked:
        metrics.ORPHANS.labels(reason).inc()
    return marked


async def reconcile(
    engine: AsyncEngine,
    stores: Mapping[str, ObjectStore],
    *,
    older_than: timedelta = INTENT_GRACE,
    multipart_older_than: timedelta = MULTIPART_STALE,
    limit: int = BATCH,
) -> dict[str, int]:
    counts = {"committed": 0, "orphaned": 0, "sidecars_restored": 0, "multipart_aborted": 0}
    async with engine.connect() as connection:
        intents = await catalog.pending_intents(connection, older_than=older_than, limit=limit)
    for staged in intents:
        try:
            # Cheap checks first: a superseded or expired intent needs no readback.
            async with engine.begin() as connection:
                _, done = await catalog.gate(connection, staged)
            if done is not None:
                continue
            if staged.product.kind == "still":
                # A still commits only with its selection row (#38), which the pipeline's
                # hourly rerun writes in the same transaction; never commit it bare.
                counts["orphaned"] += await _orphan(engine, staged.intent_id, "still_selection")
                continue
            sidecar = None
            for item in staged.objects:
                data = await verify(stores[item.bucket], item, keep=item.asset_class == "sidecar")
                if item.asset_class == "sidecar":
                    sidecar = data
            async with engine.begin() as connection:
                result = await catalog.commit(
                    connection, dataclasses.replace(staged, sidecar=sidecar)
                )
            if not result.duplicate:
                counts["committed"] += 1
                metrics.RECONCILED.labels("committed").inc()
        except catalog.PublishError as error:
            counts["orphaned"] += await _orphan(engine, staged.intent_id, error.code)
        except ReadbackFailed as error:
            counts["orphaned"] += await _orphan(engine, staged.intent_id, error.reason)
        except StoreError, KeyError:
            # The store is unreachable or its bucket is not configured here: decide nothing.
            continue
    media = stores.get("weather-media")
    after = None
    while media is not None:
        async with engine.connect() as connection:
            page = await catalog.current_sidecars(connection, after=after, limit=SIDECAR_PAGE)
        if not page:
            break
        after = page[-1].id
        for row in page:
            item = StagedObject(
                role="sidecar",
                asset_class="sidecar",
                sha256=row.sha256,
                size=int(row.bytes),
                extension="json",
                width=None,
                height=None,
            )
            data = bytes(row.sidecar)
            if hashlib.sha256(data).hexdigest() != item.sha256:
                continue
            try:
                if await media.head(item.key) is not None:
                    continue
                await media.put(
                    item.key,
                    sha256=item.sha256,
                    size=item.size,
                    content_type=item.content_type,
                    data=data,
                )
                await verify(media, item)
            except StoreError, ReadbackFailed:
                continue
            counts["sidecars_restored"] += 1
            metrics.RECONCILED.labels("sidecar_restored").inc()
    cutoff = datetime.now(UTC) - multipart_older_than
    for store in stores.values():
        try:
            uploads = await store.list_multipart()
        except StoreError:
            continue
        for upload in uploads:
            if upload.initiated < cutoff:
                await store.abort_multipart(upload.key, upload.upload_id)
                counts["multipart_aborted"] += 1
                metrics.RECONCILED.labels("multipart_aborted").inc()
    async with engine.connect() as connection:
        metrics.OLDEST_PENDING.set(await catalog.oldest_pending_seconds(connection))
    return counts
