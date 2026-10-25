# SPDX-License-Identifier: MIT
"""Publication against real PostgreSQL and Garage: crash points, fencing, privacy, isolation.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import dataclasses
import hashlib
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.api.timelapses import CatalogQuery, DatabaseSource, _hydrate
from weather.db.models import metadata
from weather.publish import catalog
from weather.publish import reconcile as reconcile_module
from weather.publish.cli import main as media_cli
from weather.publish.model import Committed, Product, ProductFile
from weather.publish.objects import ObjectStore, StoreError
from weather.publish.publisher import ObjectConflict, Publisher, describe
from weather.publish.reconcile import reconcile

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)
END = NOW - timedelta(hours=3)
START = END - timedelta(hours=10)
EMBARGO = END + timedelta(seconds=630)
DEADLINE = NOW + timedelta(hours=2)
PART = 5 * 1024 * 1024
LARGE = 11 * 1024 * 1024


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Integration requires fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if url.database != "weather_ci" or not socket.parent.name.startswith("weather-schema-ci."):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=10, max_overflow=0)


def store(bucket: str, client: httpx2.AsyncClient | None = None) -> ObjectStore:
    endpoint = os.environ["WEATHER_CI_S3_ENDPOINT"]
    if endpoint != "http://127.0.0.1:3900":
        pytest.fail("Integration requires its disposable loopback Garage")
    return ObjectStore(
        endpoint=endpoint,
        bucket=bucket,
        access_key=os.environ["WEATHER_CI_S3_ACCESS_KEY"],
        secret_key=os.environ["WEATHER_CI_S3_SECRET_KEY"],
        client=client,
        part_size=PART,
        multipart_threshold=PART + 1024 * 1024,
    )


def stores(client: httpx2.AsyncClient | None = None) -> dict[str, ObjectStore]:
    return {b: store(b, client) for b in ("weather-media", "weather-originals")}


@pytest.fixture(autouse=True)
def clean_test_database() -> Iterator[None]:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await connection.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
        finally:
            await db.dispose()

    asyncio.run(clean())
    yield


async def scalar(connection: AsyncConnection, sql: str, **params: Any) -> Any:
    return (await connection.execute(text(sql), params)).scalar_one()


async def rejects(
    connection: AsyncConnection, sql: str, params: dict[str, Any], state: str
) -> None:
    with pytest.raises(DBAPIError) as failure:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    assert getattr(failure.value.orig, "sqlstate", None) == state


def write(directory: Path, name: str, size: int = 4096) -> Path:
    path = directory / name
    path.write_bytes(secrets.token_bytes(size))
    return path


def media(parent: Path, *, large: bool = False) -> tuple[ProductFile, ...]:
    directory = parent / secrets.token_hex(4)
    directory.mkdir()
    files = []
    for role in ("native", "web", "mobile"):
        path = write(directory, f"{role}.mp4", LARGE if large and role == "native" else 4096)
        files.append(ProductFile(role=role, extension="mp4", path=path, width=1920, height=870))
    poster = write(directory, "poster.jpg")
    files.append(ProductFile(role="poster", extension="jpg", path=poster, width=1920, height=870))
    return tuple(files)


def video(
    files: tuple[ProductFile, ...],
    *,
    camera: str = "skycam_east",
    day: date | None = None,
    segment: str = "daytime",
    end: datetime = END,
    sidecar: dict[str, Any] | None = None,
) -> Product:
    cycle_date = day or (end - timedelta(hours=10)).date()
    # Real sidecars name their product; identical documents would share one content address.
    sidecar = sidecar or {
        "schema_version": 1,
        "camera": camera,
        "cycle_date": cycle_date.isoformat(),
        "segment": segment,
        "frames": 4200,
    }
    return Product.video(
        camera_id=camera,
        cycle_date=cycle_date,
        segment=segment,
        starts_at=end - timedelta(hours=10),
        ends_at=end,
        frames=4200,
        public_not_before=end + timedelta(seconds=630),
        state="published",
        reason=None,
        files=(*files, ProductFile.sidecar(sidecar)),
    )


async def counts(connection: AsyncConnection) -> dict[str, int]:
    return {
        table: int(await scalar(connection, f"SELECT count(*) FROM weather.{table}"))
        for table in ("media_asset", "publication", "video")
    }


async def no_missing_objects(connection: AsyncConnection, buckets: dict[str, ObjectStore]) -> None:
    rows = await connection.execute(text("SELECT bucket,object_key,bytes FROM weather.media_asset"))
    for row in rows:
        info = await buckets[row.bucket].head(row.object_key)
        assert info is not None and info.size == row.bytes


class FailingReads:
    """Wraps a store so readback dies: a crash after upload, before any catalog row."""

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def read(self, key: str, *, keep: bool = False) -> Any:
        raise StoreError("read")


def test_publication_schema_privacy_and_grants() -> None:
    async def check() -> None:
        worker, admin = engine("weather_worker"), engine()
        sha = "a" * 64
        asset = (
            "INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,object_key,"
            "bytes,content_type,public_not_before,published_at) VALUES (:class,:bucket,"
            ":visibility,:sha,:ext,:key,10,'image/jpeg',:pnb,:pnb)"
        )
        good = {
            "class": "still",
            "bucket": "weather-media",
            "visibility": "public",
            "sha": sha,
            "ext": "jpg",
            "key": f"still/{sha}.jpg",
            "pnb": EMBARGO,
        }
        try:
            async with admin.begin() as connection:
                for view in ("public_api.assets", "public_api.videos"):
                    options = await scalar(
                        connection, f"SELECT reloptions FROM pg_class WHERE oid='{view}'::regclass"
                    )
                    assert "security_barrier=true" in options
            async with worker.begin() as connection:
                for bad in (
                    {**good, "class": "original", "key": f"original/{sha}.jpg", "pnb": None},
                    {**good, "bucket": "weather-originals"},
                    {**good, "visibility": "private"},
                    {**good, "pnb": None},
                    {**good, "key": f"still/{'b' * 64}.jpg"},
                    {
                        **good,
                        "class": "original",
                        "bucket": "weather-originals",
                        "visibility": "private",
                        "key": f"original/{sha}.jpg",
                    },
                ):
                    await rejects(connection, asset, bad, "23514")
                await connection.execute(text(asset), good)
                await connection.execute(
                    text(
                        "INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,"
                        "object_key,bytes,content_type) VALUES ('original','weather-originals',"
                        "'private',:sha,'jpg',:key,10,'image/jpeg')"
                    ),
                    {"sha": sha, "key": f"original/{sha}.jpg"},
                )
                intent = await scalar(
                    connection,
                    "INSERT INTO weather.upload_intent(fence,product,camera_id,product_key,"
                    "expected_revision,manifest,deadline) VALUES (nextval("
                    "'weather.publication_fence'),'video','skycam_east','2026-10-23/daytime',0,"
                    "'{}',:deadline) RETURNING id",
                    deadline=DEADLINE,
                )
                publication = (
                    "INSERT INTO weather.publication(product,camera_id,product_key,revision,"
                    "fence,intent_id,state,source_start,source_end,public_not_before) VALUES "
                    "('video','skycam_east','2026-10-23/daytime',1,1,:intent,'published',:start,"
                    ":end,:pnb) RETURNING id"
                )
                await rejects(
                    connection,
                    publication,
                    {
                        "intent": intent,
                        "start": START,
                        "end": END,
                        "pnb": END + timedelta(seconds=629),
                    },
                    "23514",
                )
                pub = await scalar(
                    connection, publication, intent=intent, start=START, end=END, pnb=EMBARGO
                )
                original = await scalar(
                    connection, "SELECT id FROM weather.media_asset WHERE class='original'"
                )
                # An original never attaches to a video, and never under a public role.
                for role in ("original", "poster"):
                    await rejects(
                        connection,
                        "INSERT INTO weather.publication_asset VALUES "
                        "(:pub,'video',:role,:asset,'original')",
                        {"pub": pub, "role": role, "asset": original},
                        "23514",
                    )
                await rejects(
                    connection,
                    "INSERT INTO weather.video(camera_id,cycle_date,segment,publication_id,"
                    "revision,fence,state,starts_at,ends_at,frames,public_not_before,"
                    "committed_at) VALUES ('skycam_east','2026-10-23','daytime',:pub,1,1,"
                    "'published',:start,:end,4200,:pnb,now())",
                    {"pub": pub, "start": START, "end": END, "pnb": END + timedelta(seconds=600)},
                    "23514",
                )
                for statement in (
                    "UPDATE weather.publication SET revision=2",
                    "DELETE FROM weather.publication",
                    # UPDATE is granted only on the columns the code writes.
                    "UPDATE weather.upload_intent SET deadline=deadline+interval '1 day'",
                    "UPDATE weather.media_asset SET public_not_before=now()",
                    "UPDATE weather.media_asset SET object_key=object_key",
                    "UPDATE weather.upload_intent SET manifest='{}'",
                    "UPDATE weather.video SET camera_id='skycam_west'",
                ):
                    await rejects(connection, statement, {}, "42501")
                # An intent lives at most two days.
                await rejects(
                    connection,
                    "INSERT INTO weather.upload_intent(fence,product,camera_id,product_key,"
                    "expected_revision,manifest,deadline) VALUES (nextval("
                    "'weather.publication_fence'),'still','skycam_east','2026-10-23T18:00Z',0,"
                    "'{}',now()+interval '3 days')",
                    {},
                    "23514",
                )
            # Even the owner cannot rewrite the audit log or move an intent's deadline.
            async with admin.begin() as connection:
                for statement in (
                    "UPDATE weather.publication SET revision=2",
                    "DELETE FROM weather.publication",
                    "UPDATE weather.upload_intent SET deadline=deadline+interval '1 hour'",
                ):
                    await rejects(connection, statement, {}, "55000")
            api = engine("weather_api")
            try:
                async with api.connect() as connection:
                    await rejects(connection, "SELECT * FROM weather.media_asset", {}, "42501")
                    assert await scalar(connection, "SELECT count(*) FROM public_api.videos") == 0
                    # The still is public; the original next to it never is.
                    classes = await connection.execute(text("SELECT class FROM public_api.assets"))
                    assert classes.scalars().all() == ["still"]
                # Withdrawal is one-way and an asset's embargo is fixed, even for the owner.
                async with admin.begin() as connection:
                    await rejects(
                        connection,
                        "UPDATE weather.media_asset SET public_not_before=public_not_before"
                        "+interval '1 second' WHERE class='still'",
                        {},
                        "55000",
                    )
                    await connection.execute(
                        text(
                            "UPDATE weather.media_asset SET withdrawn=true,withdrawn_at=now(),"
                            "withdraw_reason='test' WHERE class='still'"
                        )
                    )
                    await rejects(
                        connection,
                        "UPDATE weather.media_asset SET withdrawn=false,withdrawn_at=NULL,"
                        "withdraw_reason=NULL WHERE class='still'",
                        {},
                        "55000",
                    )
            finally:
                await api.dispose()
        finally:
            await worker.dispose()
            await admin.dispose()

    asyncio.run(check())


def test_publish_crash_points(tmp_path: Path) -> None:
    async def check() -> None:
        db, buckets = engine("weather_worker"), stores()
        publisher = Publisher(db, buckets)
        try:
            # 1. Crash after upload (readback dies): objects exist, nothing is in the catalog.
            first = video(media(tmp_path), day=date(2026, 9, 1))
            crashing = Publisher(db, {b: FailingReads(s) for b, s in buckets.items()})  # type: ignore[misc]
            with pytest.raises(StoreError):
                await crashing.publish(first, expected_revision=0, deadline=DEADLINE)
            async with db.begin() as connection:
                assert await counts(connection) == {"media_asset": 0, "publication": 0, "video": 0}
            rerun = await publisher.publish(first, expected_revision=0, deadline=DEADLINE)
            assert (rerun.revision, rerun.duplicate) == (1, False)
            # 2. Crash after readback: staged but never committed; the reconciler commits it.
            second = video(media(tmp_path), day=date(2026, 9, 2))
            await publisher.stage(second, expected_revision=0, deadline=DEADLINE)
            # 3. Crash inside the commit transaction: everything rolls back.
            third = video(media(tmp_path), day=date(2026, 9, 3))
            staged = await publisher.stage(third, expected_revision=0, deadline=DEADLINE)
            original = catalog.append_outbox

            async def crash(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("crash before commit")

            catalog.append_outbox = crash  # type: ignore[assignment]
            try:
                with pytest.raises(RuntimeError):
                    await publisher.commit(staged)
            finally:
                catalog.append_outbox = original  # type: ignore[assignment]
            async with db.begin() as connection:
                assert (await counts(connection))["publication"] == 1
            # 4. Crash after commit (the caller never saw the result): the retry is a no-op.
            fourth = video(media(tmp_path), day=date(2026, 9, 4))
            done = await publisher.publish(fourth, expected_revision=0, deadline=DEADLINE)
            again = await publisher.publish(fourth, expected_revision=0, deadline=DEADLINE)
            assert again.duplicate and again.revision == 1
            assert again.publication_id == done.publication_id
            result = await reconcile(db, buckets, older_than=timedelta(0))
            # The first attempt of (1) is superseded; (2) and (3) are still valid and commit.
            assert result["orphaned"] == 1 and result["committed"] == 2
            assert await reconcile(db, buckets, older_than=timedelta(0)) == {
                "committed": 0,
                "orphaned": 0,
                "sidecars_restored": 0,
                "multipart_aborted": 0,
            }
            async with db.begin() as connection:
                assert await counts(connection) == {
                    "media_asset": 20,
                    "publication": 4,
                    "video": 4,
                }
                assert (
                    await scalar(
                        connection,
                        "SELECT string_agg(state||':'||coalesce(reason,'-'),',' ORDER BY fence)"
                        " FROM weather.upload_intent",
                    )
                    == "orphan:superseded,committed:-,committed:-,committed:-,committed:-,"
                    "committed:-"
                )
                assert (
                    await scalar(
                        connection,
                        "SELECT count(*) FROM weather.outbox WHERE topic='media.published'",
                    )
                    == 4
                )
                await no_missing_objects(connection, buckets)
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()

    asyncio.run(check())


def test_content_addresses_reject_wrong_bytes_and_never_overwrite(tmp_path: Path) -> None:
    async def check() -> None:
        db, buckets = engine("weather_worker"), stores()
        media_store = buckets["weather-media"]
        try:
            product = video(media(tmp_path), day=date(2026, 9, 5))
            native = await describe(product, product.files[0])
            other = secrets.token_bytes(native.size)
            # Bytes that do not hash to the key are refused by the store itself.
            with pytest.raises(StoreError):
                await media_store.put(
                    native.key,
                    sha256=native.sha256,
                    size=native.size,
                    content_type="video/mp4",
                    data=other,
                )
            assert await media_store.head(native.key) is None
            # A foreign object already under the key: publish fails and leaves it alone.
            await media_store.put(
                native.key,
                sha256=hashlib.sha256(other).hexdigest(),
                size=len(other),
                content_type="video/mp4",
                data=other,
            )
            with pytest.raises(ObjectConflict):
                await Publisher(db, buckets).publish(
                    product, expected_revision=0, deadline=DEADLINE
                )
            size, digest, _ = await media_store.read(native.key)
            assert digest == hashlib.sha256(other).hexdigest()
            async with db.begin() as connection:
                assert (await counts(connection))["media_asset"] == 0
            # The reconciler reports that intent for what it is.
            result = await reconcile(db, buckets, older_than=timedelta(0))
            assert result["orphaned"] == 1
            async with db.begin() as connection:
                assert (
                    await scalar(
                        connection, "SELECT reason FROM weather.upload_intent WHERE state='orphan'"
                    )
                    == "object_mismatch"
                )
            # Conditional create: Garage v2.3.0 ignores If-None-Match on PUT, so a second
            # create of a key succeeds. It can only write the bytes the key names, because the
            # signed payload hash must equal the key's hash; the object stays the same.
            fresh = secrets.token_bytes(2048)
            sha = hashlib.sha256(fresh).hexdigest()
            key = f"poster/{sha}.jpg"
            for _ in range(2):
                assert await media_store.put(
                    key, sha256=sha, size=len(fresh), content_type="image/jpeg", data=fresh
                ) in (True, False)
                size, digest, _ = await media_store.read(key)
                assert (size, digest) == (len(fresh), sha)
            with pytest.raises(StoreError):
                await media_store.put(
                    key,
                    sha256=sha,
                    size=len(fresh),
                    content_type="image/jpeg",
                    data=secrets.token_bytes(len(fresh)),
                )
            assert (await media_store.read(key))[1] == sha
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()

    asyncio.run(check())


def keogram_product(directory: Path, day: date, **changes: Any) -> Product:
    values: dict[str, Any] = {
        "camera_id": "skycam_east",
        "cycle_date": day,
        "segment": "daytime",
        "source_start": START,
        "source_end": END,
        "public_not_before": EMBARGO,
        "files": (
            ProductFile(
                role="keogram",
                extension="png",
                path=write(directory, f"keogram-{secrets.token_hex(4)}.png"),
                width=1440,
                height=512,
            ),
        ),
    }
    values.update(changes)
    return Product.keogram(**values)


def test_stale_fence_and_withdrawal_never_resurrect(tmp_path: Path) -> None:
    product = video(media(tmp_path), day=date(2026, 9, 6))
    rerender = video(media(tmp_path), day=date(2026, 9, 6))
    keogram = keogram_product(tmp_path, date(2026, 9, 6))
    # Another video that happens to publish a byte-identical poster.
    poster = next(f for f in product.files if f.role == "poster")
    neighbour = video(
        tuple(f for f in media(tmp_path) if f.role != "poster") + (poster,),
        day=date(2026, 9, 7),
    )

    async def commit_newest() -> UUID:
        db, buckets = engine("weather_worker"), stores()
        publisher = Publisher(db, buckets)
        try:
            older = await publisher.stage(product, expected_revision=0, deadline=DEADLINE)
            newer = await publisher.stage(product, expected_revision=0, deadline=DEADLINE)
            with pytest.raises(catalog.StaleFence):
                await publisher.commit(older)
            committed = await publisher.commit(newer)
            with pytest.raises(catalog.StaleFence):
                await publisher.commit(older)
            await publisher.publish(keogram, expected_revision=0, deadline=DEADLINE)
            await publisher.publish(neighbour, expected_revision=0, deadline=DEADLINE)
            assert committed.video_id is not None
            return committed.video_id
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()

    async def never_resurrected(video_id: UUID) -> None:
        db, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        publisher = Publisher(db, buckets)
        try:
            # Retrying the same bytes, or re-rendering different ones, never brings it back.
            for retry in (product, rerender):
                with pytest.raises(catalog.Withdrawn):
                    await publisher.publish(retry, expected_revision=1, deadline=DEADLINE)
            # Its segment's keogram went with it, and a re-rendered keogram stays out.
            with pytest.raises(catalog.Withdrawn):
                await publisher.publish(
                    keogram_product(tmp_path, date(2026, 9, 6)),
                    expected_revision=1,
                    deadline=DEADLINE,
                )
            async with db.begin() as connection:
                assert await scalar(
                    connection, "SELECT withdrawn FROM weather.video WHERE id=:id", id=video_id
                )
                # Native, web, mobile, sidecar and the keogram; the shared poster stays.
                withdrawn = "SELECT count(*) FROM weather.media_asset WHERE withdrawn"
                assert await scalar(connection, withdrawn) == 5
                tombstones = (
                    "SELECT string_agg(product,',' ORDER BY product) FROM weather.product_tombstone"
                )
                assert await scalar(connection, tombstones) == "keogram,video"
                assert await scalar(connection, "SELECT count(*) FROM weather.withdrawal") == 1
                events = "SELECT count(*) FROM weather.outbox WHERE topic='media.withdrawn'"
                assert await scalar(connection, events) == 1
                revisions = "SELECT max(revision) FROM weather.publication WHERE product_key=:key"
                assert await scalar(connection, revisions, key=product.key) == 1
                # The bytes remain.
                await no_missing_objects(connection, buckets)
            source = DatabaseSource(api)
            [item] = await _hydrate(source, await source.videos(CatalogQuery(), None, video_id))
            assert item.withdrawn and item.state == "withdrawn"
            assert item.renditions == [] and item.sidecar is None and item.poster is None
            assert item.keogram is None and item.keogram_state == "withdrawn"
            # Only the neighbour's five assets (its poster shared) remain public.
            async with api.connect() as connection:
                assert await scalar(connection, "SELECT count(*) FROM public_api.assets") == 5
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()
            await api.dispose()

    async def audit_rows() -> int:
        db = engine()
        try:
            async with db.connect() as connection:
                return int(await scalar(connection, "SELECT count(*) FROM weather.withdrawal"))
        finally:
            await db.dispose()

    video_id = asyncio.run(commit_newest())
    media_cli(["withdraw", str(video_id), "--reason", "test withdrawal"])
    asyncio.run(never_resurrected(video_id))
    # Withdrawing again is a no-op: no second audit row or event.
    media_cli(["withdraw", str(video_id), "--reason", "again"])
    assert asyncio.run(audit_rows()) == 1


def test_still_and_keogram_publish_without_a_video(tmp_path: Path) -> None:
    async def check() -> None:
        db, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        publisher = Publisher(db, buckets)
        try:
            day = date(2026, 9, 7)
            slot = END.replace(minute=0, second=0)
            original = write(tmp_path, "original.jpg")
            still = Product.still(
                camera_id="skycam_west",
                slot_start=slot,
                source_start=slot + timedelta(minutes=29),
                source_end=slot + timedelta(minutes=30),
                public_not_before=slot + timedelta(minutes=30, seconds=630),
                files=(
                    ProductFile(role="original", extension="jpg", path=original),
                    ProductFile(
                        role="still",
                        extension="jpg",
                        path=write(tmp_path, "still.jpg"),
                        width=1920,
                        height=870,
                    ),
                ),
            )
            keogram = Product.keogram(
                camera_id="skycam_east",
                cycle_date=day,
                segment="daytime",
                source_start=START,
                source_end=END,
                public_not_before=EMBARGO,
                files=(
                    ProductFile(
                        role="keogram",
                        extension="png",
                        path=write(tmp_path, "keogram.png"),
                        width=1440,
                        height=512,
                    ),
                ),
            )
            stored_still = await publisher.publish(still, expected_revision=0, deadline=DEADLINE)
            await publisher.publish(keogram, expected_revision=0, deadline=DEADLINE)
            async with db.begin() as connection:
                assert (await counts(connection))["video"] == 0
                row = (
                    await connection.execute(
                        text(
                            "SELECT bucket,visibility,public_not_before,published_at "
                            "FROM weather.media_asset WHERE id=:id"
                        ),
                        {"id": stored_still.assets["original"]},
                    )
                ).one()
                assert tuple(row) == ("weather-originals", "private", None, None)
            # The original is only in the originals bucket, and never public.
            original_key = (await describe(still, still.files[0])).key
            assert await buckets["weather-originals"].head(original_key) is not None
            assert await buckets["weather-media"].head(original_key) is None
            async with api.connect() as connection:
                public = set(
                    (await connection.execute(text("SELECT class FROM public_api.assets")))
                    .scalars()
                    .all()
                )
                assert public == {"still", "keogram"}
            # A video of another segment publishes with its keogram pending; the keogram's
            # own segment shows it.
            pending = await publisher.publish(
                video(media(tmp_path), day=day, segment="overnight", end=END - timedelta(hours=1)),
                expected_revision=0,
                deadline=DEADLINE,
            )
            matched = await publisher.publish(
                video(media(tmp_path), day=day), expected_revision=0, deadline=DEADLINE
            )
            source = DatabaseSource(api)
            for result, state in ((pending, "pending"), (matched, "published")):
                [item] = await _hydrate(
                    source, await source.videos(CatalogQuery(), None, result.video_id)
                )
                assert item.keogram_state == state
            # A failed keogram reports its real state, not "pending".
            await publisher.publish(
                keogram_product(
                    tmp_path,
                    day,
                    segment="overnight",
                    source_end=END - timedelta(hours=1),
                    public_not_before=END - timedelta(hours=1) + timedelta(seconds=630),
                    state="unavailable",
                    reason="extraction_failed",
                    files=(ProductFile.sidecar({"keogram": "failed", "segment": "overnight"}),),
                ),
                expected_revision=0,
                deadline=DEADLINE,
            )
            [item] = await _hydrate(
                source, await source.videos(CatalogQuery(), None, pending.video_id)
            )
            assert item.keogram_state == "unavailable" and item.keogram is None
            # A sidecar that names an archived original's hash is refused at commit.
            leak = await describe(still, still.files[0])
            with pytest.raises(catalog.PrivateReference):
                await publisher.publish(
                    video(
                        media(tmp_path),
                        day=date(2026, 9, 9),
                        sidecar={"frames": [{"sha256": leak.sha256}]},
                    ),
                    expected_revision=0,
                    deadline=DEADLINE,
                )
            # Withdrawing a still's public file tombstones the still: a re-render stays out.
            async with db.begin() as connection:
                done = await catalog.withdraw(
                    connection, stored_still.assets["still"], reason="privacy", actor="test"
                )
            assert done["products"] == 1
            rerendered = dataclasses.replace(
                still,
                files=(
                    still.files[0],
                    ProductFile(
                        role="still",
                        extension="jpg",
                        path=write(tmp_path, "still-2.jpg"),
                        width=1920,
                        height=870,
                    ),
                ),
            )
            with pytest.raises(catalog.Withdrawn):
                await publisher.publish(rerendered, expected_revision=1, deadline=DEADLINE)
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()
            await api.dispose()

    asyncio.run(check())


def test_sidecar_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        db, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        publisher = Publisher(db, buckets)
        # A #15-shaped fixture sidecar: time map, coverage, provenance; no private storage.
        fixture = {
            "schema_version": 1,
            "camera": "frontyard_1",
            "time_map": [{"frame_start": 0, "frame_end": 4200, "mode": "observed_exposure"}],
            "coverage": {"fraction": 0.97},
            "provenance": {"recipe_revision": "r1", "input_manifest_hash": "c" * 64},
        }
        try:
            day = date(2026, 9, 8)
            first = await publisher.publish(
                video(media(tmp_path), camera="frontyard_1", day=day, sidecar=fixture),
                expected_revision=0,
                deadline=DEADLINE,
            )
            async with db.begin() as connection:
                row = (
                    await connection.execute(
                        text("SELECT object_key,sidecar FROM weather.media_asset WHERE id=:id"),
                        {"id": first.assets["sidecar"]},
                    )
                ).one()
            assert row.object_key.startswith("sidecar/") and row.sidecar
            enriched = await publisher.publish_sidecar(
                kind="video",
                camera_id="frontyard_1",
                cycle_date=day,
                segment="daytime",
                document={**fixture, "description": "Clouds build after noon."},
                deadline=DEADLINE,
            )
            assert enriched.revision == 2
            assert enriched.assets["native"] == first.assets["native"]
            assert enriched.assets["sidecar"] != first.assets["sidecar"]
            source = DatabaseSource(api)
            [item] = await _hydrate(
                source, await source.videos(CatalogQuery(), None, first.video_id)
            )
            assert item.revision == 2 and item.sidecar is not None
            assert item.sidecar.asset_id == enriched.assets["sidecar"]
            # A keogram carries a sidecar too.
            keogram = await publisher.publish(
                keogram_product(
                    tmp_path,
                    day,
                    camera_id="frontyard_1",
                    files=(
                        *keogram_product(tmp_path, day).files,
                        ProductFile.sidecar({"bins": 1440, "camera": "frontyard_1"}),
                    ),
                ),
                expected_revision=0,
                deadline=DEADLINE,
            )
            # Lose both current sidecar objects; the reconciler re-commits them from the
            # catalog, paging one row at a time here.
            async with db.begin() as connection:
                keys = (
                    (
                        await connection.execute(
                            text("SELECT object_key FROM weather.media_asset WHERE id = ANY(:ids)"),
                            {"ids": [enriched.assets["sidecar"], keogram.assets["sidecar"]]},
                        )
                    )
                    .scalars()
                    .all()
                )
            media_store = buckets["weather-media"]
            for key in keys:
                await media_store._send("delete", "DELETE", key, ok={204})
                assert await media_store.head(key) is None
            monkeypatch.setattr(reconcile_module, "SIDECAR_PAGE", 1)
            result = await reconcile(db, buckets, older_than=timedelta(0))
            assert result["sidecars_restored"] == 2
            for key in keys:
                info = await media_store.head(key)
                assert info is not None and info.sha256 == key.split("/")[1].split(".")[0]
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()
            await api.dispose()

    asyncio.run(check())


class FailSecondPart(httpx2.AsyncBaseTransport):
    """Garage fails for camera A's large upload part 2; every other request passes."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, key: str) -> None:
        self.inner, self.key = inner, key

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.key in request.url.path and b"partNumber=2" in request.url.query:
            return httpx2.Response(503)
        return await self.inner.handle_async_request(request)


def test_garage_failure_for_one_camera(tmp_path: Path) -> None:
    async def check() -> None:
        db = engine("weather_worker")
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        product_a = video(media(tmp_path / "a", large=True), camera="skycam_west")
        product_b = video(media(tmp_path / "b", large=True), camera="skycam_east")
        failing_key = (await describe(product_a, product_a.files[0])).key
        client = httpx2.AsyncClient(
            transport=FailSecondPart(httpx2.AsyncHTTPTransport(), failing_key)
        )
        a_stores, b_stores = stores(client), stores()
        try:
            a, b = await asyncio.gather(
                Publisher(db, a_stores).publish(product_a, expected_revision=0, deadline=DEADLINE),
                Publisher(db, b_stores).publish(product_b, expected_revision=0, deadline=DEADLINE),
                return_exceptions=True,
            )
            assert isinstance(a, StoreError) and isinstance(b, Committed)
            assert await b_stores["weather-media"].list_multipart() == []
            assert await b_stores["weather-media"].head(failing_key) is None
            async with db.begin() as connection:
                assert (await counts(connection))["video"] == 1
            result = await reconcile(db, b_stores, older_than=timedelta(0))
            assert result["orphaned"] == 1
            async with db.begin() as connection:
                assert (
                    await scalar(
                        connection,
                        "SELECT reason FROM weather.upload_intent WHERE state='orphan'",
                    )
                    == "missing_object"
                )
        finally:
            for each in (*a_stores.values(), *b_stores.values()):
                await each.aclose()
            await db.dispose()

    asyncio.run(check())


def test_timelapse_catalog_reads_public_views(tmp_path: Path) -> None:
    async def check() -> None:
        db, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        publisher = Publisher(db, buckets)
        try:
            october = date(2026, 10, 1)
            for camera in ("skycam_east", "skycam_west"):
                await publisher.publish(
                    video(media(tmp_path), camera=camera, day=october),
                    expected_revision=0,
                    deadline=DEADLINE,
                )
            await publisher.publish(
                video(media(tmp_path), day=date(2026, 11, 1)),
                expected_revision=0,
                deadline=DEADLINE,
            )
            # Ended five minutes ago: inside ends_at + 630 s, so not listed at all. Relative to
            # the wall clock, not import-time NOW, which a long suite can outrun by 5.5 minutes.
            recent = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
            await publisher.publish(
                video(
                    media(tmp_path),
                    camera="frontyard_1",
                    day=october,
                    end=recent,
                ),
                expected_revision=0,
                deadline=DEADLINE,
            )
            source = DatabaseSource(api)
            month = CatalogQuery(first=october, last=date(2026, 10, 31), limit=1)
            first = await source.videos(month, None, None)
            assert len(first) == 2
            second = await source.videos(month, (first[0]["starts_at"], first[0]["id"]), None)
            assert len(second) == 1 and second[0]["id"] != first[0]["id"]
            everything = await source.videos(CatalogQuery(), None, None)
            assert len(everything) == 3
            assert "frontyard_1" not in {item["camera_id"] for item in everything}
            [item] = await _hydrate(source, second)
            assert item.state == "published" and len(item.renditions) == 3
            assert item.poster is not None and item.poster.url == f"/media/{item.poster.asset_id}"
            # Consumers are not told about a product before its embargo ends.
            async with db.begin() as connection:
                embargoed = await scalar(
                    connection,
                    "SELECT not_before >= :pnb FROM weather.outbox "
                    "WHERE topic='media.published' AND payload->>'camera_id'='frontyard_1'",
                    pnb=recent + timedelta(seconds=630),
                )
                assert embargoed
        finally:
            for each in buckets.values():
                await each.aclose()
            await db.dispose()
            await api.dispose()

    asyncio.run(check())
