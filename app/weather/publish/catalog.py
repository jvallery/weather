# SPDX-License-Identifier: MIT
"""Catalog writes for publication. Every function uses the caller's transaction.

A commit serializes per product on a transaction advisory lock, then checks the intent's
fence (no newer attempt exists), its deadline, the product's revision (CAS) and withdrawal,
before writing asset rows, the revision, the video state and an outbox event together.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db.jobs import append_outbox

from .model import (
    Committed,
    Product,
    ProductError,
    Staged,
    StagedObject,
    product_from_manifest,
    product_manifest,
)

PUBLISHED_TOPIC = "media.published"
WITHDRAWN_TOPIC = "media.withdrawn"
EVENT_DEADLINE = timedelta(days=7)
REASON_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,500}")
ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}")
HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


class PublishError(RuntimeError):
    """A commit refused by the catalog; `code` is a fixed, loggable reason."""

    code = "failed"


class StaleFence(PublishError):
    code = "superseded"


class RevisionConflict(PublishError):
    code = "revision_conflict"


class Withdrawn(PublishError):
    code = "withdrawn"


class IntentExpired(PublishError):
    code = "deadline"


class IntentResolved(PublishError):
    code = "orphan"


class PrivateReference(PublishError):
    """A public sidecar names an archived original's hash."""

    code = "private_reference"


class NotFound(LookupError):
    pass


@dataclass(frozen=True)
class Current:
    publication_id: UUID | None
    revision: int
    fence: int
    state: str | None
    reason: str | None
    public_not_before: datetime | None
    source: tuple[datetime, datetime] | None
    withdrawn: bool
    assets: dict[str, tuple[UUID, str]]
    frames: int | None = None


def _identity(product: str, camera_id: str, key: str) -> str:
    return f"{product}:{camera_id}:{key}"


async def _lock(connection: AsyncConnection, product: str, camera_id: str, key: str) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": _identity(product, camera_id, key)},
    )


async def register_intent(
    connection: AsyncConnection,
    product: Product,
    objects: tuple[StagedObject, ...],
    *,
    expected_revision: int,
    deadline: datetime,
) -> tuple[UUID, int]:
    """Record the attempt before any byte is uploaded; it takes a new fencing token."""
    if deadline.tzinfo is None or expected_revision < 0:
        raise ProductError("Invalid intent")
    row = (
        await connection.execute(
            text("""
        INSERT INTO weather.upload_intent
          (fence,product,camera_id,product_key,expected_revision,manifest,deadline)
        VALUES (nextval('weather.publication_fence'),:product,:camera,:key,:expected,
                CAST(:manifest AS jsonb),:deadline)
        RETURNING id,fence
    """),
            {
                "product": product.kind,
                "camera": product.camera_id,
                "key": product.key,
                "expected": expected_revision,
                "manifest": json.dumps(product_manifest(product, objects)),
                "deadline": deadline,
            },
        )
    ).one()
    return cast(UUID, row.id), int(row.fence)


async def current(connection: AsyncConnection, product: Product) -> Current:
    params = {"product": product.kind, "camera": product.camera_id, "key": product.key}
    head = (
        await connection.execute(
            text("""
        SELECT id,revision,state,reason,public_not_before,source_start,source_end,
          (SELECT max(fence) FROM weather.publication WHERE product=:product
             AND camera_id=:camera AND product_key=:key) AS fence
        FROM weather.publication WHERE product=:product AND camera_id=:camera
          AND product_key=:key ORDER BY revision DESC LIMIT 1
    """),
            params,
        )
    ).one_or_none()
    withdrawn, frames = False, None
    if product.kind == "video":
        video = (
            await connection.execute(
                text("""
            SELECT withdrawn,frames FROM weather.video WHERE camera_id=:camera
              AND cycle_date=:cycle_date AND segment=:segment
        """),
                {
                    "camera": product.camera_id,
                    "cycle_date": product.cycle_date,
                    "segment": product.segment,
                },
            )
        ).one_or_none()
        if video is not None:
            withdrawn, frames = bool(video.withdrawn), int(video.frames)
    withdrawn = withdrawn or bool(
        (
            await connection.execute(
                text("""
            SELECT EXISTS (SELECT 1 FROM weather.product_tombstone WHERE product=:product
              AND camera_id=:camera AND product_key=:key)
        """),
                params,
            )
        ).scalar_one()
    )
    if head is None:
        return Current(None, 0, 0, None, None, None, None, withdrawn, {}, frames)
    assets = {
        str(row.role): (cast(UUID, row.asset_id), str(row.object_key))
        for row in await connection.execute(
            text("""
        SELECT pa.role,pa.asset_id,m.object_key FROM weather.publication_asset pa
        JOIN weather.media_asset m ON m.id=pa.asset_id WHERE pa.publication_id=:id
    """),
            {"id": head.id},
        )
    }
    return Current(
        publication_id=cast(UUID, head.id),
        revision=int(head.revision),
        fence=int(head.fence),
        state=str(head.state),
        reason=head.reason,
        public_not_before=head.public_not_before,
        source=(head.source_start, head.source_end),
        withdrawn=withdrawn,
        assets=assets,
        frames=frames,
    )


def _same(now: Current, staged: Staged) -> bool:
    """A retry of an already committed revision: same objects, state and timing."""
    product = staged.product
    keys = {item.role: item.key for item in staged.objects}
    keys.update({role: now.assets[role][1] for role in product.keep if role in now.assets})
    return (
        {role: key for role, (_, key) in now.assets.items()} == keys
        and now.state == product.state
        and now.reason == product.reason
        and now.public_not_before == product.public_not_before
        and now.source == (product.source_start, product.source_end)
    )


async def _mark_committed(connection: AsyncConnection, intent_id: UUID) -> None:
    await connection.execute(
        text("""
        UPDATE weather.upload_intent SET state='committed',resolved_at=clock_timestamp()
        WHERE id=:id AND state='pending'
    """),
        {"id": intent_id},
    )


async def mark_orphan(connection: AsyncConnection, intent_id: UUID, reason: str) -> bool:
    result = await connection.execute(
        text("""
        UPDATE weather.upload_intent SET state='orphan',reason=:reason,
          resolved_at=clock_timestamp() WHERE id=:id AND state='pending'
    """),
        {"id": intent_id, "reason": reason},
    )
    return result.rowcount == 1


async def _asset(
    connection: AsyncConnection, item: StagedObject, product: Product, sidecar: bytes | None
) -> UUID:
    original = item.asset_class == "original"
    inserted = (
        await connection.execute(
            text("""
        INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,object_key,
          bytes,content_type,width,height,public_not_before,published_at,sidecar)
        VALUES (:class,:bucket,:visibility,:sha256,:extension,:key,:bytes,:content_type,
          :width,:height,:public_not_before,
          CASE WHEN :original THEN NULL ELSE clock_timestamp() END,:sidecar)
        ON CONFLICT (object_key) DO NOTHING RETURNING id
    """),
            {
                "class": item.asset_class,
                "bucket": item.bucket,
                "visibility": "private" if original else "public",
                "sha256": item.sha256,
                "extension": item.extension,
                "key": item.key,
                "bytes": item.size,
                "content_type": item.content_type,
                "width": item.width,
                "height": item.height,
                "public_not_before": None if original else product.public_not_before,
                "original": original,
                "sidecar": sidecar if item.asset_class == "sidecar" else None,
            },
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return cast(UUID, inserted)
    existing = (
        await connection.execute(
            text("""
        SELECT id,bytes,withdrawn,state FROM weather.media_asset WHERE object_key=:key FOR UPDATE
    """),
            {"key": item.key},
        )
    ).one()
    if existing.withdrawn:
        # The same bytes were withdrawn; a retry or re-render never resurrects them.
        raise Withdrawn("Asset was withdrawn")
    if int(existing.bytes) != item.size:
        raise PublishError("Catalog size differs for a content address")
    if existing.state != "available":
        # The object was just verified in the store again.
        await connection.execute(
            text("UPDATE weather.media_asset SET state='available' WHERE id=:id"),
            {"id": existing.id},
        )
    return cast(UUID, existing.id)


async def gate(connection: AsyncConnection, staged: Staged) -> tuple[Current, Committed | None]:
    """Lock the product and check the intent. Returns the committed result of a retry whose
    content is already committed, or raises when the intent can no longer commit."""
    product = staged.product
    await _lock(connection, product.kind, product.camera_id, product.key)
    intent = (
        await connection.execute(
            text("""
        SELECT state,fence,deadline>clock_timestamp() AS live FROM weather.upload_intent
        WHERE id=:id FOR UPDATE
    """),
            {"id": staged.intent_id},
        )
    ).one()
    if intent.state == "orphan":
        raise IntentResolved("Intent was resolved as an orphan")
    now = await current(connection, product)
    assets = {role: asset for role, (asset, _) in now.assets.items()}
    if intent.state == "committed":
        # Committed by an earlier call or by the reconciler: report what is committed.
        committed = (
            await connection.execute(
                text("SELECT id,revision FROM weather.publication WHERE intent_id=:id"),
                {"id": staged.intent_id},
            )
        ).one_or_none()
        return now, Committed(
            committed.id if committed else now.publication_id,
            int(committed.revision) if committed else now.revision,
            assets,
            await _video_id(connection, product),
            duplicate=True,
        )
    newest = (
        await connection.execute(
            text("""
        SELECT max(fence) FROM weather.upload_intent
        WHERE product=:product AND camera_id=:camera AND product_key=:key
    """),
            {"product": product.kind, "camera": product.camera_id, "key": product.key},
        )
    ).scalar_one()
    if int(newest) > staged.fence or now.fence > staged.fence:
        raise StaleFence("A newer publish attempt holds the fence")
    if now.withdrawn:
        raise Withdrawn("Product was withdrawn")
    if not intent.live:
        raise IntentExpired("Intent deadline passed")
    if now.revision != staged.expected_revision:
        if _same(now, staged):
            # A retry after an unacknowledged commit: the content is already committed.
            await _mark_committed(connection, staged.intent_id)
            return now, Committed(
                now.publication_id,
                now.revision,
                assets,
                await _video_id(connection, product),
                duplicate=True,
            )
        raise RevisionConflict("Product revision changed")
    return now, None


async def commit(connection: AsyncConnection, staged: Staged) -> Committed:
    """Commit a staged intent atomically, or raise without writing anything."""
    product = staged.product
    now, done = await gate(connection, staged)
    if done is not None:
        return done
    assets: dict[str, tuple[UUID, str]] = {}
    for role in product.keep:
        if role not in now.assets:
            raise RevisionConflict("A kept role is not in the current revision")
        asset_id, key = now.assets[role]
        assets[role] = (asset_id, key.split("/", 1)[0])
    if staged.sidecar is not None:
        hashes = sorted(set(HEX64.findall(staged.sidecar.decode())))
        if (
            hashes
            and (
                await connection.execute(
                    text("""
            SELECT EXISTS (SELECT 1 FROM weather.media_asset
              WHERE class='original' AND sha256 = ANY(CAST(:hashes AS text[])))
        """),
                    {"hashes": hashes},
                )
            ).scalar_one()
        ):
            raise PrivateReference("Sidecar names an original's hash")
    for item in staged.objects:
        sidecar = staged.sidecar if item.asset_class == "sidecar" else None
        assets[item.role] = (await _asset(connection, item, product, sidecar), item.asset_class)
    revision = staged.expected_revision + 1
    publication_id = cast(
        UUID,
        (
            await connection.execute(
                text("""
        INSERT INTO weather.publication(product,camera_id,product_key,revision,fence,intent_id,
          state,reason,source_start,source_end,public_not_before,committed_at)
        VALUES (:product,:camera,:key,:revision,:fence,:intent,:state,:reason,:start,:end,
          :public_not_before,clock_timestamp())
        RETURNING id
    """),
                {
                    "product": product.kind,
                    "camera": product.camera_id,
                    "key": product.key,
                    "revision": revision,
                    "fence": staged.fence,
                    "intent": staged.intent_id,
                    "state": product.state,
                    "reason": product.reason,
                    "start": product.source_start,
                    "end": product.source_end,
                    "public_not_before": product.public_not_before,
                },
            )
        ).scalar_one(),
    )
    await connection.execute(
        text("""
        INSERT INTO weather.publication_asset(publication_id,product,role,asset_id,asset_class)
        SELECT :publication,:product,role,asset_id,asset_class
        FROM unnest(CAST(:roles AS text[]),CAST(:ids AS uuid[]),CAST(:classes AS text[]))
          AS a(role,asset_id,asset_class)
    """),
        {
            "publication": publication_id,
            "product": product.kind,
            "roles": list(assets),
            "ids": [asset for asset, _ in assets.values()],
            "classes": [asset_class for _, asset_class in assets.values()],
        },
    )
    video_id = None
    if product.kind == "video":
        video_id = (
            await connection.execute(
                text("""
        INSERT INTO weather.video(camera_id,cycle_date,segment,publication_id,revision,fence,
          state,reason,starts_at,ends_at,frames,public_not_before,committed_at)
        VALUES (:camera,:cycle_date,:segment,:publication,:revision,:fence,:state,:reason,
          :start,:end,:frames,:public_not_before,clock_timestamp())
        ON CONFLICT (camera_id,cycle_date,segment) DO UPDATE SET
          publication_id=EXCLUDED.publication_id,revision=EXCLUDED.revision,
          fence=EXCLUDED.fence,state=EXCLUDED.state,reason=EXCLUDED.reason,
          starts_at=EXCLUDED.starts_at,ends_at=EXCLUDED.ends_at,frames=EXCLUDED.frames,
          public_not_before=EXCLUDED.public_not_before,committed_at=EXCLUDED.committed_at
        WHERE weather.video.revision=:expected AND NOT weather.video.withdrawn
        RETURNING id
    """),
                {
                    "camera": product.camera_id,
                    "cycle_date": product.cycle_date,
                    "segment": product.segment,
                    "publication": publication_id,
                    "revision": revision,
                    "fence": staged.fence,
                    "state": product.state,
                    "reason": product.reason,
                    "start": product.source_start,
                    "end": product.source_end,
                    "frames": product.frames,
                    "public_not_before": product.public_not_before,
                    "expected": staged.expected_revision,
                },
            )
        ).scalar_one_or_none()
        if video_id is None:
            raise RevisionConflict("Video row changed")
    await _mark_committed(connection, staged.intent_id)
    # Consumers (search #25) never see a product before its embargo ends.
    moment = max(datetime.now(UTC), product.public_not_before)
    await append_outbox(
        connection,
        topic=PUBLISHED_TOPIC,
        key=f"{PUBLISHED_TOPIC}:{publication_id}",
        payload={
            "publication_id": str(publication_id),
            "product": product.kind,
            "camera_id": product.camera_id,
            "key": product.key,
            "revision": revision,
        },
        not_before=moment,
        deadline=moment + EVENT_DEADLINE,
    )
    return Committed(
        publication_id,
        revision,
        {role: asset for role, (asset, _) in assets.items()},
        cast(UUID | None, video_id),
    )


async def _video_id(connection: AsyncConnection, product: Product) -> UUID | None:
    if product.kind != "video":
        return None
    return cast(
        UUID | None,
        (
            await connection.execute(
                text("""
        SELECT id FROM weather.video WHERE camera_id=:camera AND cycle_date=:cycle_date
          AND segment=:segment
    """),
                {
                    "camera": product.camera_id,
                    "cycle_date": product.cycle_date,
                    "segment": product.segment,
                },
            )
        ).scalar_one_or_none(),
    )


async def _tombstone(
    connection: AsyncConnection, withdrawal: UUID, products: list[tuple[str, str, str]]
) -> None:
    for product, camera_id, key in products:
        await connection.execute(
            text("""
            INSERT INTO weather.product_tombstone(product,camera_id,product_key,withdrawal_id)
            VALUES (:product,:camera,:key,:withdrawal) ON CONFLICT DO NOTHING
        """),
            {"product": product, "camera": camera_id, "key": key, "withdrawal": withdrawal},
        )


async def _lock_all(connection: AsyncConnection, products: list[tuple[str, str, str]]) -> None:
    # Always in sorted order, so two multi-product withdrawals never deadlock.
    for product, camera_id, key in sorted(set(products)):
        await _lock(connection, product, camera_id, key)


async def withdraw(
    connection: AsyncConnection, target: UUID, *, reason: str, actor: str
) -> dict[str, Any]:
    """Withdraw a video or one asset, permanently; the bytes remain.

    A video takes its segment's keogram with it: both products are tombstoned (a re-render is
    refused) and every asset they published is withdrawn, except bytes another product also
    publishes. An asset is withdrawn wherever it is used, and the keogram or still products
    that published it are tombstoned.
    """
    if not REASON_TEXT.fullmatch(reason) or not ACTOR.fullmatch(actor):
        raise ValueError("Invalid withdrawal reason or actor")
    video = (
        await connection.execute(
            text("SELECT camera_id,cycle_date,segment FROM weather.video WHERE id=:id"),
            {"id": target},
        )
    ).one_or_none()
    params: dict[str, Any] = {"id": target, "reason": reason, "actor": actor}
    if video is not None:
        key = Product.segment_key(video.cycle_date, video.segment)
        products = [("video", video.camera_id, key), ("keogram", video.camera_id, key)]
        await _lock_all(connection, products)
        already = (
            await connection.execute(
                text("SELECT withdrawn FROM weather.video WHERE id=:id FOR UPDATE"), params
            )
        ).scalar_one()
        if already:
            return {"target": "video", "withdrawn": False, "assets": 0}
        await connection.execute(
            text("""
            UPDATE weather.video SET withdrawn=true,withdrawn_at=clock_timestamp(),
              withdraw_reason=:reason WHERE id=:id
        """),
            params,
        )
        assets = await connection.execute(
            text("""
            UPDATE weather.media_asset m SET withdrawn=true,withdrawn_at=clock_timestamp(),
              withdraw_reason=:reason
            WHERE NOT m.withdrawn AND m.id IN (
              SELECT pa.asset_id FROM weather.publication p
              JOIN weather.publication_asset pa ON pa.publication_id=p.id
              WHERE p.product IN ('video','keogram') AND p.camera_id=:camera
                AND p.product_key=:key)
            AND NOT EXISTS (
              SELECT 1 FROM weather.publication_asset other
              JOIN weather.publication o ON o.id=other.publication_id
              WHERE other.asset_id=m.id AND NOT (o.product IN ('video','keogram')
                AND o.camera_id=:camera AND o.product_key=:key))
        """),
            {**params, "camera": video.camera_id, "key": key},
        )
        count = assets.rowcount
        withdrawal = await connection.execute(
            text("""
            INSERT INTO weather.withdrawal(video_id,reason,actor) VALUES(:id,:reason,:actor)
            RETURNING id
        """),
            params,
        )
        await _tombstone(connection, withdrawal.scalar_one(), products)
        kind = "video"
    else:
        products = [
            (str(row.product), str(row.camera_id), str(row.product_key))
            for row in await connection.execute(
                text("""
            SELECT DISTINCT p.product,p.camera_id,p.product_key FROM weather.publication p
            JOIN weather.publication_asset pa ON pa.publication_id=p.id
            WHERE pa.asset_id=:id AND p.product IN ('keogram','still')
        """),
                params,
            )
        ]
        await _lock_all(connection, products)
        found = (
            await connection.execute(
                text("SELECT withdrawn FROM weather.media_asset WHERE id=:id FOR UPDATE"),
                params,
            )
        ).one_or_none()
        if found is None:
            raise NotFound("No video or asset with that ID")
        if found.withdrawn:
            return {"target": "asset", "withdrawn": False, "assets": 0}
        await connection.execute(
            text("""
            UPDATE weather.media_asset SET withdrawn=true,withdrawn_at=clock_timestamp(),
              withdraw_reason=:reason WHERE id=:id
        """),
            params,
        )
        withdrawal = await connection.execute(
            text("""
            INSERT INTO weather.withdrawal(asset_id,reason,actor) VALUES(:id,:reason,:actor)
            RETURNING id
        """),
            params,
        )
        await _tombstone(connection, withdrawal.scalar_one(), products)
        kind, count = "asset", 1
    moment = datetime.now(UTC)
    await append_outbox(
        connection,
        topic=WITHDRAWN_TOPIC,
        key=f"{WITHDRAWN_TOPIC}:{target}",
        payload={"target": kind, "id": str(target)},
        not_before=moment,
        deadline=moment + EVENT_DEADLINE,
    )
    return {"target": kind, "withdrawn": True, "assets": count, "products": len(products)}


async def pending_intents(
    connection: AsyncConnection, *, older_than: timedelta, limit: int
) -> list[Staged]:
    rows = await connection.execute(
        text("""
        SELECT id,fence,expected_revision,manifest FROM weather.upload_intent
        WHERE state='pending' AND created_at < clock_timestamp() - CAST(:age AS interval)
        ORDER BY created_at,id LIMIT :limit
    """),
        {"age": older_than, "limit": limit},
    )
    staged = []
    for row in rows:
        product, objects = product_from_manifest(cast(dict[str, Any], row.manifest))
        staged.append(
            Staged(
                intent_id=row.id,
                fence=int(row.fence),
                expected_revision=int(row.expected_revision),
                product=product,
                objects=objects,
                sidecar=None,
            )
        )
    return staged


async def oldest_pending_seconds(connection: AsyncConnection) -> float:
    value = (
        await connection.execute(
            text("""
        SELECT extract(epoch FROM clock_timestamp()-min(created_at))
        FROM weather.upload_intent WHERE state='pending'
    """)
        )
    ).scalar_one()
    return float(value or 0)


async def current_sidecars(
    connection: AsyncConnection, *, after: UUID | None, limit: int
) -> list[Any]:
    """One keyset page of the sidecars of every current video and keogram revision that is
    not withdrawn; their bytes are inline."""
    return list(
        await connection.execute(
            text("""
        WITH current AS (
          SELECT DISTINCT ON (product,camera_id,product_key) id FROM weather.publication
          WHERE product IN ('video','keogram')
          ORDER BY product,camera_id,product_key,revision DESC)
        SELECT DISTINCT m.id,m.object_key,m.sha256,m.bytes,m.sidecar FROM current c
        JOIN weather.publication_asset pa ON pa.publication_id=c.id AND pa.role='sidecar'
        JOIN weather.media_asset m ON m.id=pa.asset_id
        WHERE NOT m.withdrawn AND (CAST(:after AS uuid) IS NULL OR m.id > CAST(:after AS uuid))
        ORDER BY m.id LIMIT :limit
    """),
            {"after": after, "limit": limit},
        )
    )
