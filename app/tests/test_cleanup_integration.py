# SPDX-License-Identifier: MIT
"""Staging cleanup and object lifecycle (#19) against real PostgreSQL and Garage: the SQL
catalog's four-condition rule across dusk, crash reruns, render directories after publish
readback, reconcile, expiry dry-runs on fixtures and orphan/tombstone GC with the grace guard.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import hashlib
import json
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.db.models import metadata
from weather.pipeline import schedule, synthetic
from weather.publish.model import MEDIA_BUCKET, ORIGINALS_BUCKET, Product, ProductFile
from weather.publish.objects import ObjectStore, StoreError
from weather.publish.publisher import Publisher
from weather.scheduler.cleanup import (
    Cleanup,
    CleanupSettings,
    Deletion,
    Mode,
    SqlCatalog,
    remove,
)
from weather.scheduler.lifecycle import (
    GcRefused,
    LifecycleSettings,
    ReconcileAborted,
    expire,
    gc,
    purge,
    reconcile_objects,
)
from weather.timemodel import SiteConfig, solar_cycle

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SITE = SiteConfig(latitude=40.0, longitude=-105.0)
CAMERA = "skycam_east"
W, H = 16, 8
NS = synthetic.NS
LAG = timedelta(seconds=6)
NOW = datetime.now(UTC).replace(microsecond=0)
DEADLINE = NOW + timedelta(hours=2)
GUARDED = LifecycleSettings(pitr_window_days=30, b2_version_retention_days=40)


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Integration requires fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if url.database != "weather_ci" or not socket.parent.name.startswith("weather-schema-ci."):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=5, max_overflow=0)


def stores() -> dict[str, ObjectStore]:
    endpoint = os.environ["WEATHER_CI_S3_ENDPOINT"]
    if endpoint != "http://127.0.0.1:3900":
        pytest.fail("Integration requires its disposable loopback Garage")
    return {
        bucket: ObjectStore(
            endpoint=endpoint,
            bucket=bucket,
            access_key=os.environ["WEATHER_CI_S3_ACCESS_KEY"],
            secret_key=os.environ["WEATHER_CI_S3_SECRET_KEY"],
        )
        for bucket in (MEDIA_BUCKET, ORIGINALS_BUCKET)
    }


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


async def rows(db: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with db.connect() as connection:
        return list(await connection.execute(text(sql), params))


async def admin(sql: str, **params: Any) -> None:
    db = engine()
    try:
        async with db.begin() as connection:
            await connection.execute(text(sql), params)
    finally:
        await db.dispose()


def blob(directory: Path, name: str, size: int = 2048) -> Path:
    path = directory / name
    path.write_bytes(secrets.token_bytes(size))
    return path


def video_product(
    directory: Path, day: date, segment: str, start: datetime, end: datetime
) -> Product:
    directory = directory / secrets.token_hex(4)
    directory.mkdir()
    files = [
        ProductFile(role=r, extension="mp4", path=blob(directory, f"{r}.mp4"), width=64, height=32)
        for r in ("native", "web", "mobile")
    ]
    files.append(
        ProductFile(
            role="poster", extension="jpg", path=blob(directory, "p.jpg"), width=64, height=32
        )
    )
    sidecar = {"schema_version": 1, "camera": CAMERA, "cycle_date": day.isoformat(),
               "segment": segment, "nonce": secrets.token_hex(8)}
    return Product.video(
        camera_id=CAMERA,
        cycle_date=day,
        segment=segment,
        starts_at=start,
        ends_at=end,
        frames=120,
        public_not_before=end + timedelta(seconds=630),
        state="published",
        reason=None,
        files=(*files, ProductFile.sidecar(sidecar)),
    )


def still_product(directory: Path, slot: datetime) -> Product:
    return Product.still(
        camera_id=CAMERA,
        slot_start=slot,
        source_start=slot + timedelta(minutes=29),
        source_end=slot + timedelta(minutes=30),
        public_not_before=slot + timedelta(minutes=30, seconds=630),
        files=(
            ProductFile(
                role="original", extension="jpg", path=blob(directory, f"o{slot:%Y%m%d%H}.jpg")
            ),
            ProductFile(
                role="still",
                extension="jpg",
                path=blob(directory, f"s{slot:%Y%m%d%H}.jpg"),
                width=64,
                height=32,
            ),
        ),
    )


def keogram_product(directory: Path, day: date, start: datetime, end: datetime) -> Product:
    return Product.keogram(
        camera_id=CAMERA,
        cycle_date=day,
        segment="daytime",
        source_start=start,
        source_end=end,
        public_not_before=end + timedelta(seconds=630),
        files=(
            ProductFile(
                role="keogram",
                extension="png",
                path=blob(directory, f"k{day}.png"),
                width=1440,
                height=64,
            ),
        ),
    )


def test_lifecycle_schema_grants_and_audit_rows() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                async def can(relation: str, privilege: str) -> bool:
                    return bool(
                        (
                            await connection.execute(
                                text("SELECT has_table_privilege('weather_worker',:r,:p)"),
                                {"r": relation, "p": privilege},
                            )
                        ).scalar_one()
                    )

                for relation in ("staging_chunk", "staging_deletion", "object_event"):
                    name = f"weather.{relation}"
                    assert await can(name, "INSERT") and await can(name, "SELECT")
                    assert not await can(name, "DELETE") and not await can(name, "TRUNCATE")
                    for role in ("weather_api", "weather_ingest", "weather_editor"):
                        assert not (
                            await connection.execute(
                                text("SELECT has_table_privilege(:role,:r,'SELECT')"),
                                {"role": role, "r": name},
                            )
                        ).scalar_one()
                columns = {
                    ("staging_chunk", "state"): True,
                    ("staging_chunk", "manifest_sha256"): False,
                    ("staging_deletion", "unlinked_at"): True,
                    ("staging_deletion", "sha256"): False,
                    ("media_asset", "state_changed_at"): True,
                    ("media_asset", "object_key"): False,
                }
                for (relation, column), allowed in columns.items():
                    assert (
                        await connection.execute(
                            text(
                                "SELECT has_column_privilege('weather_worker',:r,:c,'UPDATE')"
                            ),
                            {"r": f"weather.{relation}", "c": column},
                        )
                    ).scalar_one() is allowed
                await connection.execute(
                    text("""
                    INSERT INTO weather.object_event (action,bucket,object_key,reason,actor)
                    VALUES ('gc_deleted','weather-media',:key,'orphan','gc')
                """),
                    {"key": "video/" + "a" * 64 + ".mp4"},
                )
            for sql in (
                "UPDATE weather.object_event SET reason='x'",
                "DELETE FROM weather.object_event",
            ):
                with pytest.raises(DBAPIError):
                    async with db.begin() as connection:
                        await connection.execute(text(sql))
        finally:
            await db.dispose()

    asyncio.run(check())


def cycle_for(day: date) -> Any:
    return solar_cycle(day, SITE)


def test_sql_catalog_keeps_a_dusk_spanning_chunk_until_both_segments_release_it(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        worker = engine("weather_worker")
        day = (NOW - timedelta(days=10)).date()
        cycle = cycle_for(day)
        dusk = cycle.dusk
        base = dusk.replace(second=0, microsecond=0) - timedelta(minutes=2)
        first = int(base.timestamp())
        paths = synthetic.write_chunks(
            tmp_path,
            CAMERA,
            [
                synthetic.Frame((first + s) * NS, synthetic.solid(W, H, s % 200))
                for s in range(0, 300, 2)
            ],
            geometry=(W, H),
            cadence_ms=2000,
        )
        settings = CleanupSettings(
            # #37 is not wired yet; #38's hourly condition is, and stays required.
            enabled=True, modes={"keogram": Mode.VACUOUS, "hourly": Mode.REQUIRED}
        )
        catalog = SqlCatalog(worker)

        def pass_(**kw: Any) -> Cleanup:
            return Cleanup(
                CAMERA, tmp_path, catalog, settings, lag=LAG, expected=(2000, 0, W, H), **kw
            )

        try:
            async with worker.begin() as connection:
                assert await schedule.freeze(connection, SITE, [cycle]) == 1
            segments = {
                r.kind: r.id
                for r in await rows(
                    worker,
                    "SELECT id,kind FROM weather.segment WHERE camera_id=:c AND cycle_date=:d",
                    c=CAMERA,
                    d=day,
                )
            }
            report = await pass_().run()
            assert report["inventoried"] == len(paths) and report["pinned_mezzanine"] == len(paths)
            stored = await rows(worker, "SELECT * FROM weather.staging_chunk ORDER BY window_start")
            assert len(stored) == len(paths) and {r.state for r in stored} == {"present"}
            # Daytime finishes its mezzanine: only chunks whose frames cannot reach dusk go.
            await schedule.mark_through(worker, segments["daytime"], dusk)
            await schedule.mark_durable(worker, segments["daytime"])
            released = [r for r in stored if r.window_end + LAG <= dusk]
            # No hour slot has a selection yet: the hourly condition keeps every released one.
            report = await pass_().run()
            assert report["pinned_hourly"] == len(released) and "deleted_chunk" not in report
            first_hour = stored[0].window_start.replace(minute=0, second=0, microsecond=0)
            last_hour = (stored[-1].window_end + LAG).replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
            insert = """
                INSERT INTO weather.archival_still (camera_id,slot_starts_at,local_date,revision,
                  state,reason,policy,eligible,retain_until)
                SELECT :camera,h.starts_at,h.local_date,:revision,'missing',:reason,
                  'nearest-half-hour/v1',0,h.local_date + 1826
                FROM weather.hour_slot h WHERE h.starts_at >= :low AND h.starts_at < :high
            """
            # `selection_expired` only says no selection ran for 12 h (an outage): kept.
            await admin(
                insert, camera=CAMERA, revision=1, reason="selection_expired", low=first_hour,
                high=last_hour,
            )
            report = await pass_().run()
            assert report["pinned_hourly"] == len(released) and "deleted_chunk" not in report
            assert all((tmp_path / r.name).exists() for r in released)
            # A correction to `no_capture` (no input in those hours) finalizes them.
            await admin(
                "UPDATE weather.archival_still SET active=false,retired_at=clock_timestamp() "
                "WHERE camera_id=:camera AND active",
                camera=CAMERA,
            )
            await admin(
                insert, camera=CAMERA, revision=2, reason="no_capture", low=first_hour,
                high=last_hour,
            )
            calls = 0

            def crash_first(staging: Path, deletion: Deletion) -> bool:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("killed between decision and unlink")
                return remove(staging, deletion)

            with pytest.raises(OSError):
                await pass_(remover=crash_first).run()
            report = await pass_().run()
            expected = [r for r in stored if r.window_end + LAG <= dusk]
            assert expected and report["resumed"] == 1
            assert report.get("deleted_chunk", 0) == len(expected)
            spanning = [r for r in stored if r.window_start < dusk < r.window_end]
            assert len(spanning) == 1
            assert (tmp_path / spanning[0].name).exists()
            # The overnight segment releases past the spanning chunk: now it goes too.
            await schedule.mark_through(
                worker, segments["overnight"], spanning[0].window_end + timedelta(seconds=1)
            )
            await pass_().run()
            assert not (tmp_path / spanning[0].name).exists()
            deletions = await rows(
                worker,
                "SELECT d.*,c.manifest_sha256,c.state FROM weather.staging_deletion d "
                "JOIN weather.staging_chunk c ON c.id=d.chunk_id",
            )
            assert all(
                d.sha256 == d.manifest_sha256 and d.state == "deleted" and d.unlinked_at
                for d in deletions
            )
            assert len(deletions) == len(
                [r for r in stored if r.window_end <= spanning[0].window_end]
            )
            # Every row is kept as metadata, deleted or not.
            assert len(await rows(worker, "SELECT id FROM weather.staging_chunk")) == len(paths)
            # Every chunk still pinned by the overnight segment is still on staging.
            survivors = await rows(
                worker, "SELECT * FROM weather.staging_chunk WHERE state='present'"
            )
            assert survivors and all((tmp_path / r.name).exists() for r in survivors)
        finally:
            await worker.dispose()

    asyncio.run(check())


def test_render_directory_is_removed_only_after_its_renditions_publish(tmp_path: Path) -> None:
    async def check() -> None:
        worker = engine("weather_worker")
        buckets = stores()
        day = (NOW - timedelta(days=3)).date()
        cycle = cycle_for(day)
        try:
            async with worker.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle])
            [seg] = await rows(
                worker,
                "SELECT id FROM weather.segment WHERE camera_id=:c AND cycle_date=:d "
                "AND kind='daytime'",
                c=CAMERA,
                d=day,
            )
            directory = tmp_path / "render" / CAMERA / f"{day.isoformat()}_daytime_r1-v1"
            (directory / "blocks").mkdir(parents=True)
            (directory / "blocks" / "00000.mkv").write_bytes(b"m" * 64)
            cleanup = Cleanup(
                CAMERA,
                tmp_path,
                SqlCatalog(worker),
                CleanupSettings(enabled=True),
                lag=LAG,
                expected=(2000, 0, W, H),
            )
            # Rendering, then finalized as published, but nothing committed yet: kept.
            assert "render_eligible" not in await cleanup.run()
            await schedule.finish(
                worker,
                seg.id,
                {"state": "published", "reason": None, "slots": {"fraction": 1.0}},
            )
            assert "render_eligible" not in await cleanup.run()
            assert directory.exists()
            publisher = Publisher(worker, buckets)
            await publisher.publish(
                video_product(tmp_path, day, "daytime", cycle.dawn, cycle.dusk),
                expected_revision=0,
                deadline=DEADLINE,
            )
            report = await cleanup.run()
            assert report["deleted_render"] == 1 and not directory.exists()
            [audit] = await rows(worker, "SELECT * FROM weather.staging_deletion")
            assert audit.segment_id == seg.id and audit.reason == "finalized"
        finally:
            for store in buckets.values():
                await store.aclose()
            await worker.dispose()

    asyncio.run(check())


def test_reconcile_marks_a_deleted_object_unavailable_and_the_api_reports_it(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        worker, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        try:
            end = NOW - timedelta(hours=3)
            committed = await Publisher(worker, buckets).publish(
                video_product(tmp_path, end.date(), "daytime", end - timedelta(hours=8), end),
                expected_revision=0,
                deadline=DEADLINE,
            )
            [native] = await rows(
                worker,
                "SELECT id,object_key FROM weather.media_asset WHERE id=:id",
                id=committed.assets["native"],
            )
            await buckets[MEDIA_BUCKET].delete(native.object_key)
            # One of five missing is above the default 5 % breaker: nothing is marked.
            with pytest.raises(ReconcileAborted) as aborted:
                await reconcile_objects(worker, buckets[MEDIA_BUCKET], rate_per_second=200)
            assert aborted.value.reason == "too_many_missing"
            assert aborted.value.counts["missing"] == 1
            assert not await rows(worker, "SELECT id FROM weather.object_event")
            # A bucket that does not exist is refused before any HEAD.
            gone = ObjectStore(
                endpoint=os.environ["WEATHER_CI_S3_ENDPOINT"],
                bucket="weather-ci-no-such-bucket",
                access_key=os.environ["WEATHER_CI_S3_ACCESS_KEY"],
                secret_key=os.environ["WEATHER_CI_S3_SECRET_KEY"],
            )
            try:
                assert not await gone.bucket_exists()
            except StoreError:
                pass  # a refused HEAD (403) also stops reconcile before any marking
            await gone.aclose()
            counts = await reconcile_objects(
                worker, buckets[MEDIA_BUCKET], rate_per_second=200, max_missing_ratio=0.5
            )
            assert counts["missing"] == 1 and counts["marked_unavailable"] == 1
            assert counts["present"] == counts["checked"] - 1 and counts["errors"] == 0
            [row] = await rows(
                worker, "SELECT state,state_changed_at FROM weather.media_asset WHERE id=:id",
                id=native.id,
            )
            assert row.state == "unavailable" and row.state_changed_at is not None
            [listed] = await rows(
                api, "SELECT state,reason,native_asset_id FROM public_api.videos WHERE id=:id",
                id=committed.video_id,
            )
            assert (listed.state, listed.reason, listed.native_asset_id) == (
                "unavailable",
                "media_unavailable",
                None,
            )
            # Idempotent: a second run checks only available rows and marks nothing.
            again = await reconcile_objects(
                worker, buckets[MEDIA_BUCKET], rate_per_second=200, max_missing_ratio=0.5
            )
            assert again["marked_unavailable"] == 0 and again["missing"] == 0
            [event] = await rows(worker, "SELECT action,reason FROM weather.object_event")
            assert tuple(event) == ("unavailable", "missing")
        finally:
            for store in buckets.values():
                await store.aclose()
            await api.dispose()
            await worker.dispose()

    asyncio.run(check())


async def lifecycle_fixtures(worker: AsyncEngine, buckets: dict[str, ObjectStore], tmp: Path
                             ) -> dict[str, Any]:
    publisher = Publisher(worker, buckets)
    old_end = NOW - timedelta(days=400)
    new_end = NOW - timedelta(days=30)
    old_video = await publisher.publish(
        video_product(tmp, old_end.date(), "daytime", old_end - timedelta(hours=8), old_end),
        expected_revision=0,
        deadline=DEADLINE,
    )
    new_video = await publisher.publish(
        video_product(tmp, new_end.date(), "daytime", new_end - timedelta(hours=8), new_end),
        expected_revision=0,
        deadline=DEADLINE,
    )
    ancient = (NOW - timedelta(days=6 * 366)).replace(minute=0, second=0)
    recent = (NOW - timedelta(days=400)).replace(minute=0, second=0)
    old_still = await publisher.publish(
        still_product(tmp, ancient), expected_revision=0, deadline=DEADLINE
    )
    new_still = await publisher.publish(
        still_product(tmp, recent), expected_revision=0, deadline=DEADLINE
    )
    old_keogram = await publisher.publish(
        keogram_product(tmp, ancient.date(), ancient, ancient + timedelta(hours=8)),
        expected_revision=0,
        deadline=DEADLINE,
    )
    return {
        "old_video": old_video,
        "new_video": new_video,
        "old_still": old_still,
        "new_still": new_still,
        "old_keogram": old_keogram,
    }


def test_expiry_dry_runs_list_only_what_retention_covers_and_apply_tombstones(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        worker, api, buckets = engine("weather_worker"), engine("weather_api"), stores()
        try:
            made = await lifecycle_fixtures(worker, buckets, tmp_path)
            # Both flags false: list, tombstone nothing.
            result = await expire(worker, LifecycleSettings())
            assert result["expired"] == 0
            assert result["candidates"] == {
                "hourly:keogram": 1,
                "hourly:original": 1,
                "hourly:still": 1,
                "video:poster": 1,
                "video:sidecar": 1,
                "video:video": 3,
            }
            assert {
                r.state for r in await rows(worker, "SELECT state FROM weather.media_asset")
            } == {"available"}
            # Video expiry enabled: only the old video's media expire; stills still serve.
            result = await expire(worker, LifecycleSettings(retention_enabled=True))
            assert result["expired"] == 5
            expired = {
                r.id
                for r in await rows(
                    worker, "SELECT id FROM weather.media_asset WHERE state='expired'"
                )
            }
            assert expired == set(made["old_video"].assets.values())
            public = {
                r.id for r in await rows(api, "SELECT id FROM public_api.assets")
            }
            for name in ("old_keogram", "new_video"):
                assert set(made[name].assets.values()) <= public
            # Stills serve through #38's selection rows (public_api.assets gates them on
            # still_derivative); video expiry leaves their assets, originals included, intact.
            states = {
                r.id: r.state
                for r in await rows(worker, "SELECT id,state FROM weather.media_asset")
            }
            for name in ("old_still", "new_still"):
                assert {states[a] for a in made[name].assets.values()} == {"available"}
            # The five-year rule tombstones stills, originals and keograms; re-running is a no-op.
            result = await expire(
                worker, LifecycleSettings(retention_enabled=True, hourly_deletion_enabled=True)
            )
            assert result["expired"] == 3
            assert (await expire(worker, LifecycleSettings(retention_enabled=True)))[
                "expired"
            ] == 0
            events = await rows(worker, "SELECT action,reason FROM weather.object_event")
            assert {tuple(e) for e in events} == {
                ("expired", "retention_video"),
                ("expired", "retention_hourly"),
            }
        finally:
            for store in buckets.values():
                await store.aclose()
            await api.dispose()
            await worker.dispose()

    asyncio.run(check())


def test_gc_deletes_only_unreferenced_objects_past_grace_and_refuses_unguarded(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        worker, buckets = engine("weather_worker"), stores()
        media = buckets[MEDIA_BUCKET]
        try:
            with pytest.raises(GcRefused):
                await gc(worker, media, LifecycleSettings(gc_enabled=True))
            made = await lifecycle_fixtures(worker, buckets, tmp_path)
            await expire(
                worker, LifecycleSettings(retention_enabled=True, hourly_deletion_enabled=True)
            )
            # Two orphaned intents whose objects were uploaded: one past grace, one inside it.
            orphan_keys = []
            for age in (100, 10):
                data = secrets.token_bytes(64)
                key_sha = hashlib.sha256(data).hexdigest()
                key = f"poster/{key_sha}.jpg"
                await media.put(key, sha256=key_sha, size=64, content_type="image/jpeg", data=data)
                manifest = {
                    "objects": [
                        {"role": "poster", "class": "poster", "sha256": key_sha, "bytes": 64,
                         "extension": "jpg", "width": 1, "height": 1}
                    ]
                }
                await admin(
                    """
                    INSERT INTO weather.upload_intent (fence,product,camera_id,product_key,
                      expected_revision,manifest,state,reason,deadline,created_at,resolved_at)
                    VALUES (nextval('weather.publication_fence'),'video',:c,:k,0,
                      CAST(:m AS jsonb),'orphan','deadline',
                      clock_timestamp() - make_interval(days => :age) + interval '1 hour',
                      clock_timestamp() - make_interval(days => :age),
                      clock_timestamp() - make_interval(days => :age))
                    """,
                    c=CAMERA,
                    k=f"2020-01-0{1 if age == 100 else 2}/daytime",
                    m=json.dumps(manifest),
                    age=age,
                )
                orphan_keys.append(key)
            # Tombstones only count from the expiry; age the video tombstones past grace.
            await admin(
                "UPDATE weather.media_asset SET state_changed_at = clock_timestamp() - "
                "interval '100 days' WHERE id = ANY(CAST(:ids AS uuid[]))",
                ids=[str(v) for v in made["old_video"].assets.values()],
            )
            dry = await gc(worker, media, GUARDED)
            assert dry == {
                "orphan_candidates": 1,
                "tombstone_candidates": 5,
                "deleted": 0,
                "kept_after_audit": 0,
            }
            assert await media.head(orphan_keys[0]) is not None
            enabled = LifecycleSettings(
                gc_enabled=True, pitr_window_days=30, b2_version_retention_days=40
            )
            done = await gc(worker, media, enabled)
            assert done["deleted"] == 6
            assert await media.head(orphan_keys[0]) is None
            # The orphan inside grace and every referenced or in-grace object remain.
            assert await media.head(orphan_keys[1]) is not None
            for name in ("new_video", "old_still", "new_still", "old_keogram"):
                for role, asset in made[name].assets.items():
                    [row] = await rows(
                        worker, "SELECT bucket,object_key FROM weather.media_asset WHERE id=:id",
                        id=asset,
                    )
                    assert await buckets[row.bucket].head(row.object_key) is not None, role
            # Originals: their flag is false here, and the scheduler's GC targets media only.
            originals = await gc(worker, buckets[ORIGINALS_BUCKET], enabled)
            assert originals["deleted"] == 0
            # Rerun: nothing left; audit rows for every deletion.
            assert (await gc(worker, media, enabled))["deleted"] == 0
            events = await rows(
                worker, "SELECT reason FROM weather.object_event WHERE action='gc_deleted'"
            )
            assert sorted(e.reason for e in events) == ["orphan"] + ["tombstone"] * 5
        finally:
            for store in buckets.values():
                await store.aclose()
            await worker.dispose()

    asyncio.run(check())


def test_hard_purge_needs_a_withdrawn_asset_and_leaves_an_audit_row(tmp_path: Path) -> None:
    async def check() -> None:
        from weather.publish import catalog
        from weather.scheduler.lifecycle import PurgeRefused

        worker, buckets = engine("weather_worker"), stores()
        media = buckets[MEDIA_BUCKET]
        try:
            end = NOW - timedelta(hours=3)
            committed = await Publisher(worker, buckets).publish(
                video_product(tmp_path, end.date(), "overnight", end - timedelta(hours=8), end),
                expected_revision=0,
                deadline=DEADLINE,
            )
            poster: UUID = committed.assets["poster"]
            with pytest.raises(PurgeRefused):
                await purge(worker, media, poster, reason="third_party_privacy", actor="jason")
            async with worker.begin() as connection:
                await catalog.withdraw(connection, poster, reason="privacy", actor="jason")
            result = await purge(worker, media, poster, reason="third_party_privacy", actor="jason")
            assert result["purged"] == str(poster)
            [row] = await rows(
                worker, "SELECT state,object_key FROM weather.media_asset WHERE id=:id", id=poster
            )
            assert row.state == "unavailable" and await media.head(row.object_key) is None
            [event] = await rows(worker, "SELECT action,reason,actor FROM weather.object_event")
            assert tuple(event) == ("purged", "third_party_privacy", "jason")
        finally:
            for store in buckets.values():
                await store.aclose()
            await worker.dispose()

    asyncio.run(check())


def test_a_started_segment_stays_active_past_late_render_until_finalized() -> None:
    """A deadline-missed segment with a release point keeps rendering (from what is still on
    staging) and publishes, so neither its released input nor its blocks are lost."""

    async def check() -> None:
        worker = engine("weather_worker")
        day = (NOW - timedelta(days=5)).date()
        try:
            async with worker.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle_for(day)])
            await admin(
                "UPDATE weather.segment SET state='unavailable',reason='deadline_missed' "
                "WHERE camera_id=:c AND cycle_date=:d",
                c=CAMERA,
                d=day,
            )
            [daytime] = await rows(
                worker,
                "SELECT id,starts_at FROM weather.segment WHERE camera_id=:c AND cycle_date=:d "
                "AND kind='daytime'",
                c=CAMERA,
                d=day,
            )
            assert not [r for r in await schedule.active(worker, CAMERA, NOW) if r.id == daytime.id]
            await schedule.mark_through(worker, daytime.id, daytime.starts_at + timedelta(hours=1))
            [row] = [r for r in await schedule.active(worker, CAMERA, NOW) if r.id == daytime.id]
            assert row.mezzanine_through == daytime.starts_at + timedelta(hours=1)
            await schedule.finish(
                worker,
                daytime.id,
                {"state": "partial", "reason": "low_coverage", "slots": {"fraction": 0.6}},
            )
            assert not [r for r in await schedule.active(worker, CAMERA, NOW) if r.id == daytime.id]
            # A stuck one is resolved by an operator's abandon, which records who did it.
            [overnight] = await rows(
                worker,
                "SELECT id,starts_at FROM weather.segment WHERE camera_id=:c AND cycle_date=:d "
                "AND kind='overnight'",
                c=CAMERA,
                d=day,
            )
            await schedule.mark_through(worker, overnight.id, overnight.starts_at)
            assert await schedule.abandon(worker, overnight.id, "jason")
            assert not await schedule.abandon(worker, overnight.id, "jason")
            [row] = await rows(
                worker,
                "SELECT state,reason,render_result->>'abandoned_by' AS actor,finalized_at "
                "FROM weather.segment WHERE id=:id",
                id=overnight.id,
            )
            assert (row.state, row.reason, row.actor) == ("unavailable", "abandoned", "jason")
            assert not [
                r for r in await schedule.active(worker, CAMERA, NOW) if r.id == overnight.id
            ]
        finally:
            await worker.dispose()

    asyncio.run(check())


def test_hourly_backlog_reselects_expired_and_rowless_slots_with_sealed_input(
    tmp_path: Path,
) -> None:
    """After an outage longer than 12 h, slots left `selection_expired` (or with no row) are
    selected again while their sealed input is still on staging, so cleanup can release it."""
    from weather.pipeline.hourly import HourlyArchive
    from weather.pipeline.intake import ChunkIndex, Expected
    from weather.stills import catalog as stills
    from weather.timemodel import slot_for

    async def check() -> None:
        worker, buckets = engine("weather_worker"), stores()
        expected = Expected(CAMERA, 2000, 0, W, H)
        archive = HourlyArchive(
            CAMERA, worker, Publisher(worker, buckets), ChunkIndex(tmp_path, expected), expected
        )
        base = (NOW - timedelta(days=3)).replace(minute=0, second=0)
        expired, withdrawn = base, base + timedelta(hours=2)
        # No directory of its own: the hour after a sealed hour can hold its captures.
        rowless, after = base + timedelta(hours=1), base + timedelta(hours=3)
        uncovered = (NOW - timedelta(days=10)).replace(minute=0, second=0)
        for hour in (expired, withdrawn, uncovered):
            # Sealed hour directories without any capture: selection finds no input.
            (tmp_path / "sealed" / CAMERA / hour.strftime("%Y-%m-%dT%HZ")).mkdir(parents=True)
        try:
            from weather.stills.policy import Choice

            # Segments cover the days around `base` only; `uncovered` has none.
            first = (base - timedelta(days=2)).date()
            async with worker.begin() as connection:
                await schedule.freeze(
                    connection, SITE, [cycle_for(first + timedelta(days=n)) for n in range(4)]
                )

            async with worker.begin() as connection:
                for slot, reason in ((expired, "selection_expired"), (withdrawn, "withdrawn")):
                    await stills.record(
                        connection,
                        camera=CAMERA,
                        slot=slot_for(slot),
                        choice=Choice("missing", reason, None, None, 0),
                    )
            async with worker.connect() as connection:
                assert await stills.reselectable(
                    connection, CAMERA, [expired, rowless, withdrawn]
                ) == {expired, rowless}
            await archive._backlog(NOW, set(), 3)
            active = {
                r.slot_starts_at: (r.revision, r.reason)
                for r in await rows(
                    worker,
                    "SELECT slot_starts_at,revision,reason FROM weather.archival_still "
                    "WHERE active AND camera_id=:c",
                    c=CAMERA,
                )
            }
            # The expired slot is corrected (revision 2), the row-less one selected, and the
            # withdrawn one is final.
            assert active == {
                expired: (2, "no_capture"),
                rowless: (1, "no_capture"),
                withdrawn: (1, "withdrawn"),
                after: (1, "no_capture"),
                # Recorded terminally before any decode: nothing could bind it.
                uncovered: (1, "no_segment"),
                uncovered + timedelta(hours=1): (1, "no_segment"),
            }
            async with worker.connect() as connection:
                assert not await stills.reselectable(connection, CAMERA, list(active))
        finally:
            for store in buckets.values():
                await store.aclose()
            await worker.dispose()

    asyncio.run(check())


def test_gc_kept_supersedes_gc_deleted_when_a_publish_intervenes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weather.scheduler.lifecycle as lifecycle

    async def check() -> None:
        worker, buckets = engine("weather_worker"), stores()
        media = buckets[MEDIA_BUCKET]
        enabled = LifecycleSettings(
            gc_enabled=True, pitr_window_days=30, b2_version_retention_days=40
        )
        try:
            data = secrets.token_bytes(64)
            sha = hashlib.sha256(data).hexdigest()
            key = f"poster/{sha}.jpg"
            await media.put(key, sha256=sha, size=64, content_type="image/jpeg", data=data)
            manifest = json.dumps(
                {
                    "objects": [
                        {"role": "poster", "class": "poster", "sha256": sha, "bytes": 64,
                         "extension": "jpg", "width": 1, "height": 1}
                    ]
                }
            )
            insert = """
                INSERT INTO weather.upload_intent (fence,product,camera_id,product_key,
                  expected_revision,manifest,state,reason,deadline,created_at,resolved_at)
                VALUES (nextval('weather.publication_fence'),'video',:c,:k,0,
                  CAST(:m AS jsonb),CAST(:state AS varchar),:reason,
                  clock_timestamp() - make_interval(days => :age) + interval '1 hour',
                  clock_timestamp() - make_interval(days => :age),
                  CASE WHEN CAST(:state AS varchar) = 'pending' THEN NULL
                       ELSE clock_timestamp() - make_interval(days => :age) END)
            """
            await admin(insert, c=CAMERA, k="2020-02-01/daytime", m=manifest, state="orphan",
                        reason="deadline", age=100)
            real = lifecycle._event

            async def racing(connection: Any, action: str, *args: Any) -> None:
                await real(connection, action, *args)
                if action == "gc_deleted":
                    # A publisher registers an intent for the same bytes in between.
                    await admin(insert, c=CAMERA, k="2020-02-02/daytime", m=manifest,
                                state="pending", reason=None, age=0)

            monkeypatch.setattr(lifecycle, "_event", racing)
            done = await gc(worker, media, enabled)
            assert done["deleted"] == 0 and done["kept_after_audit"] == 1
            assert await media.head(key) is not None
            monkeypatch.setattr(lifecycle, "_event", real)
            events = [
                r.action
                for r in await rows(
                    worker, "SELECT action FROM weather.object_event ORDER BY created_at"
                )
            ]
            assert events == ["gc_deleted", "gc_kept"]
            # That intent later fails for good and its grace passes: gc_kept is the latest GC
            # event, so the key is a candidate again and is collected.
            await admin(
                "UPDATE weather.upload_intent SET state='orphan',reason='deadline',"
                "resolved_at=clock_timestamp() - interval '95 days' WHERE state='pending'"
            )
            again = await gc(worker, media, enabled)
            assert again["deleted"] == 1 and await media.head(key) is None
        finally:
            for store in buckets.values():
                await store.aclose()
            await worker.dispose()

    asyncio.run(check())
