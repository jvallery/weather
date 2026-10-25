# SPDX-License-Identifier: MIT
"""Hourly originals and comparison stills against real PostgreSQL, Garage and FFmpeg (#38):
schema rejections, exact archived bytes, dusk binding, crash safety, corrections, the embargo,
metadata-free derivatives and a video expiry that leaves stills intact.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import hashlib
import io
import json
import os
import tarfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.db.days import ensure_days
from weather.db.models import metadata
from weather.pipeline import schedule, synthetic
from weather.pipeline.hourly import HourlyArchive
from weather.pipeline.intake import ChunkIndex, Expected
from weather.publish import catalog as publish_catalog
from weather.publish.model import EMBARGO, MEDIA_BUCKET, ORIGINALS_BUCKET, Product, ProductFile
from weather.publish.objects import ObjectStore
from weather.publish.publisher import Publisher
from weather.stills import catalog
from weather.stills.policy import Choice
from weather.stills.sanitize import metadata as scan
from weather.timemodel import SiteConfig, SolarCycle, allocate_frames, day_for, slot_for

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/time-model-vectors.json").read_text())
SITE = SiteConfig(latitude=39.7392, longitude=-104.9903)
W, H = 32, 16
PHASE = {"skycam_east": 0, "skycam_west": 333, "frontyard_1": 667}
PRIVATE = ("sha256", "chunk", "member", "attempt_id", "original_asset_id", "original_class")


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


async def rejected(connection: AsyncConnection, sql: str, sqlstate: str, **params: Any) -> None:
    with pytest.raises(DBAPIError) as failure:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    assert getattr(failure.value.orig, "sqlstate", None) == sqlstate


def camera_jpeg(seed: int) -> bytes:
    """A camera-like native JPEG with EXIF (GPS) and a comment: archived as is, never public."""
    rgb = np.random.default_rng(seed).integers(0, 255, (H, W, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (39.0, 44.0, 21.0), 3: "W", 4: (104.0, 59.0, 25.0)}
    buffer = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(
        buffer, format="JPEG", quality=95, exif=exif.tobytes(), comment=b"private"
    )
    return buffer.getvalue()


def cycle_around(dusk: datetime) -> SolarCycle:
    dawn, next_dawn = dusk - timedelta(hours=2), dusk + timedelta(hours=3)
    return SolarCycle(
        cycle_date=day_for(dawn).local_date,
        dawn=dawn,
        dusk=dusk,
        next_dawn=next_dawn,
        sunrise=dawn,
        sunset=dusk,
        allocation=allocate_frames(7200, 10800),
        config_hash="0" * 64,
    )


def seal(staging: Path, camera: str, captures: list[tuple[datetime, bytes]]) -> None:
    """Seal captures whose grid slot is the given instant (source time = slot + 0.1 s)."""
    synthetic.write_chunks(
        staging,
        camera,
        [synthetic.Frame(int(at.timestamp() * 1000) * 1_000_000, data) for at, data in captures],
        geometry=(W, H),
        cadence_ms=2000,
        phase_ms=PHASE[camera],
        pod=camera.replace("_", "-"),
    )


def archive_for(db: AsyncEngine, publisher: Publisher, staging: Path, camera: str) -> HourlyArchive:
    expected = Expected(camera, 2000, PHASE[camera], W, H)
    # A fresh index each time: nothing in memory survives a restart.
    return HourlyArchive(camera, db, publisher, ChunkIndex(staging, expected), expected)


def member_bytes(staging: Path, camera: str, sha256: str) -> bytes:
    for chunk in (staging / "sealed" / camera).rglob("*.tar"):
        with tarfile.open(chunk) as archive:
            for member in archive.getmembers():
                if member.name.endswith(".jpg"):
                    source = archive.extractfile(member)
                    data = source.read() if source else b""
                    if hashlib.sha256(data).hexdigest() == sha256:
                        return data
    raise AssertionError("member not found")


MISSING = (
    "INSERT INTO weather.archival_still(camera_id,slot_starts_at,local_date,revision,state,"
    "reason,policy,eligible,retain_until) VALUES (:camera,:slot,:day,:revision,'missing',"
    "'no_capture','nearest-half-hour/v1',0,'2031-01-01')"
)
SELECTED = (
    "INSERT INTO weather.archival_still(camera_id,slot_starts_at,local_date,revision,state,"
    "policy,eligible,attempt_id,chunk,member,source_ts,capture_upper,offset_us,cycle_date,"
    "segment_kind,segment_revision,original_asset_id,original_class,sha256,bytes,width,height,"
    "publication_id,publication_product,retain_until) VALUES (:camera,:slot,:day,1,:state,"
    "'nearest-half-hour/v1',1,'a','h/c.tar','frames/a.jpg',:source,:source,:offset,:cycle,"
    ":kind,:rev,:asset,'original',:sha,10,32,16,:publication,:product,'2031-01-01')"
)


def test_hour_slots_and_selection_rows_are_enforced_by_the_database() -> None:
    async def check() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        slot = datetime(2025, 12, 22, 0, tzinfo=UTC)
        fall = datetime(2026, 11, 1, 8, tzinfo=UTC)
        try:
            async with worker.begin() as connection:
                await ensure_days(connection, date(2026, 3, 8), date(2026, 3, 8))
                await ensure_days(connection, date(2026, 11, 1), date(2026, 11, 1))
                await schedule.freeze(connection, SITE, [cycle_around(slot + timedelta(hours=1))])
            # DST: 23 and 25 slots with the vectors' offsets and two distinct 01:00 slots.
            for case in VECTORS["civil_calendar_days"]:
                if case["local_date"] not in ("2026-03-08", "2026-11-01"):
                    continue
                found = await rows(
                    worker,
                    "SELECT starts_at,utc_offset_minutes,fold FROM weather.hour_slot "
                    "WHERE local_date=:day ORDER BY starts_at",
                    day=date.fromisoformat(case["local_date"]),
                )
                assert len(found) == case["slot_count"]
                for row, item in zip(found, case["slots"], strict=True):
                    assert row.starts_at == datetime.fromisoformat(item["start"])
                    assert row.fold == item["fold"]
                    assert row.utc_offset_minutes == int(item["local_label"][-6:-3]) * 60
            # An original and a still publication of skycam_west for the dusk-hour slot.
            async with admin.begin() as connection:
                asset = (
                    await connection.execute(
                        text(
                            "INSERT INTO weather.media_asset(class,bucket,visibility,sha256,"
                            "extension,object_key,bytes,content_type) VALUES ('original',"
                            "'weather-originals','private',:sha,'jpg',:key,10,'image/jpeg') "
                            "RETURNING id"
                        ),
                        {"sha": "a" * 64, "key": f"original/{'a' * 64}.jpg"},
                    )
                ).scalar_one()
                intent = (
                    await connection.execute(
                        text(
                            "INSERT INTO weather.upload_intent(fence,product,camera_id,"
                            "product_key,expected_revision,manifest,deadline) VALUES "
                            "(nextval('weather.publication_fence'),'still','skycam_west',"
                            "'2025-12-22T00:00Z',0,'{}',clock_timestamp()+interval '1 hour') "
                            "RETURNING id,fence"
                        )
                    )
                ).one()
                publication = (
                    await connection.execute(
                        text(
                            "INSERT INTO weather.publication(product,camera_id,product_key,"
                            "revision,fence,intent_id,state,source_start,source_end,"
                            "public_not_before) VALUES ('still','skycam_west',"
                            "'2025-12-22T00:00Z',1,:fence,:intent,'published',:start,:end,"
                            ":pnb) RETURNING id"
                        ),
                        {
                            "fence": intent.fence,
                            "intent": intent.id,
                            "start": slot,
                            "end": slot + timedelta(minutes=31),
                            "pnb": slot + timedelta(minutes=42),
                        },
                    )
                ).scalar_one()
                segment = (
                    await connection.execute(
                        text(
                            "SELECT cycle_date,kind,revision FROM weather.segment "
                            "WHERE camera_id='skycam_west' AND starts_at<=:at AND :at<ends_at"
                        ),
                        {"at": slot + timedelta(minutes=30)},
                    )
                ).one()
            good = {
                "camera": "skycam_west",
                "slot": slot,
                "day": date(2025, 12, 21),
                "state": "on_target",
                "source": slot + timedelta(minutes=30),
                "offset": 0,
                "cycle": segment.cycle_date,
                "kind": segment.kind,
                "rev": segment.revision,
                "asset": asset,
                "sha": "a" * 64,
                "publication": publication,
                "product": "still",
            }
            async with worker.begin() as connection:
                await ensure_days(connection, date(2025, 12, 21), date(2025, 12, 21))
                # A slot outside its calendar day, or not on a UTC hour.
                for values in (
                    "('2026-03-09T07:00:00Z','2026-03-08',-360,0)",
                    "('2026-03-08T07:30:00Z','2026-03-08',-420,0)",
                ):
                    await rejected(
                        connection, f"INSERT INTO weather.hour_slot VALUES {values}", "23514"
                    )
                # Same-day composite FK: a 2026-11-01 slot can't be filed under 2026-10-31.
                await rejected(
                    connection,
                    MISSING,
                    "23503",
                    camera="skycam_east",
                    slot=fall,
                    day=date(2026, 10, 31),
                    revision=1,
                )
                same_day = {"slot": fall, "day": date(2026, 11, 1), "revision": 1}
                await connection.execute(text(MISSING), {"camera": "skycam_east", **same_day})
                # One active selection per (camera, slot); cameras are a fixed set.
                await rejected(
                    connection,
                    MISSING,
                    "23505",
                    camera="skycam_east",
                    **{**same_day, "revision": 2},
                )
                await rejected(connection, MISSING, "23514", camera="doorbell", **same_day)
                # The camera must match its publication (and its segment): skycam_west's
                # archived original can't be filed as skycam_east's.
                await rejected(connection, SELECTED, "23503", **{**good, "camera": "skycam_east"})
                # The recorded hash must be the archived original's.
                await rejected(connection, SELECTED, "23503", **{**good, "sha": "b" * 64})
                # Never borrowed from another hour; the offset is exact; a selection is whole.
                before = {"source": slot - timedelta(seconds=1), "offset": -1801000000}
                await rejected(connection, SELECTED, "23514", **{**good, **before})
                await rejected(connection, SELECTED, "23514", **{**good, "offset": 1})
                await rejected(connection, SELECTED, "23514", **{**good, "publication": None})
                # 60 s from :30 is still on target; 60.000001 s is not.
                edge = {"source": slot + timedelta(minutes=31), "offset": 60000000}
                nested = await connection.begin_nested()
                await connection.execute(text(SELECTED), {**good, **edge})
                await nested.rollback()
                late = {
                    "source": slot + timedelta(minutes=31, microseconds=1),
                    "offset": 60000001,
                }
                await rejected(connection, SELECTED, "23514", **{**good, **late})
                await connection.execute(text(SELECTED), good)
                still_id = (
                    await connection.execute(
                        text("SELECT id FROM weather.archival_still WHERE camera_id='skycam_west'")
                    )
                ).scalar_one()
                # A derivative can never reference an original.
                derivative = (
                    "INSERT INTO weather.still_derivative(archival_still_id,role,asset_id,"
                    "asset_class,recipe) VALUES (:still,'still',:asset,:cls,'sanitized-still/v1')"
                )
                await rejected(
                    connection, derivative, "23514", still=still_id, asset=asset, cls="original"
                )
                await rejected(
                    connection, derivative, "23503", still=still_id, asset=asset, cls="still"
                )
            # The API reads the view only, and the view names nothing private.
            columns = {
                r.column_name
                for r in await rows(
                    admin,
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public_api' AND table_name='stills'",
                )
            }
            assert columns and not columns & set(PRIVATE)
            async with api.begin() as connection:
                await rejected(connection, "SELECT 1 FROM weather.archival_still", "42501")
                listed = list(
                    await connection.execute(
                        text("SELECT camera_id,state FROM public_api.stills ORDER BY camera_id")
                    )
                )
            assert [tuple(r) for r in listed] == [
                ("skycam_east", "missing"),
                ("skycam_west", "on_target"),
            ]
        finally:
            for db in (admin, worker, api):
                await db.dispose()

    asyncio.run(check())


def test_hour_archive_exact_bytes_dusk_binding_crash_correction_and_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    slot = slot_for(now - timedelta(hours=6)).start
    # Civil dusk ten minutes into the hour: the hour straddles daytime and overnight.
    dusk = slot + timedelta(minutes=10)
    east = camera_jpeg(1)
    seal(
        tmp_path,
        "skycam_east",
        [
            (slot + timedelta(minutes=5), camera_jpeg(2)),  # daytime, far from :30
            (slot + timedelta(minutes=30, seconds=10), east),  # overnight, the winner
            (slot + timedelta(minutes=31), camera_jpeg(3)),
            (slot - timedelta(minutes=1), camera_jpeg(4)),  # previous hour: never chosen
        ],
    )
    west = camera_jpeg(5)
    seal(
        tmp_path,
        "skycam_west",
        [
            (slot + timedelta(minutes=2, milliseconds=333), camera_jpeg(6)),
            (slot + timedelta(minutes=9, milliseconds=333), west),  # nearest; before dusk
            (slot + timedelta(hours=1, minutes=29, milliseconds=333), camera_jpeg(7)),
        ],
    )
    seal(
        tmp_path,
        "frontyard_1",
        [(slot + timedelta(hours=1, minutes=1, milliseconds=667), camera_jpeg(8))],
    )

    async def check() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()
        publisher = Publisher(worker, buckets)
        try:
            async with worker.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle_around(dusk)])

            # East is killed after staging (objects uploaded and read back, intent pending)
            # and before the commit, then reruns from scratch.
            original_commit = Publisher.commit

            async def crash(*_args: Any, **_kwargs: Any) -> Any:
                raise ConnectionResetError("killed before commit")

            monkeypatch.setattr(Publisher, "commit", crash)
            await archive_for(worker, publisher, tmp_path, "skycam_east").step(now)
            monkeypatch.setattr(Publisher, "commit", original_commit)
            # Only database-only missing slots committed; no archived selection exists.
            assert (
                await rows(
                    admin, "SELECT 1 FROM weather.archival_still WHERE source_ts IS NOT NULL"
                )
                == []
            )
            for camera in ("skycam_east", "skycam_east", "skycam_west", "frontyard_1"):
                await archive_for(worker, publisher, tmp_path, camera).step(now)

            selections = {
                r.camera_id: r
                for r in await rows(
                    admin,
                    "SELECT * FROM weather.archival_still WHERE slot_starts_at=:slot",
                    slot=slot,
                )
            }
            assert len(selections) == 3
            assert (
                await rows(
                    admin,
                    "SELECT count(*) AS n FROM weather.archival_still "
                    "WHERE camera_id='skycam_east' AND active",
                )
            )[0].n == 12  # every due slot of the lookback, each exactly once (11 missing)
            e, w, f = (selections[c] for c in ("skycam_east", "skycam_west", "frontyard_1"))
            # East: on target, overnight; west: off target, daytime; same solar cycle.
            assert (e.state, e.segment_kind, e.revision) == ("on_target", "overnight", 1)
            assert e.offset_us == 10_100_000 and e.source_ts == slot + timedelta(
                minutes=30, seconds=10, milliseconds=100
            )
            assert (w.state, w.segment_kind) == ("off_target", "daytime")
            assert e.cycle_date == w.cycle_date == cycle_around(dusk).cycle_date
            assert e.local_date == day_for(slot).local_date
            assert (f.state, f.reason, f.original_asset_id) == ("missing", "no_capture", None)
            # Archived originals: the TAR member's exact bytes, EXIF and comment included,
            # only in the originals bucket.
            for row, data in ((e, east), (w, west)):
                assert row.sha256 == hashlib.sha256(data).hexdigest()
                assert member_bytes(tmp_path, row.camera_id, row.sha256) == data
                key = f"original/{row.sha256}.jpg"
                size, digest, stored = await buckets[ORIGINALS_BUCKET].read(key, keep=True)
                assert stored == data and digest == row.sha256 and size == len(data)
                assert await buckets[MEDIA_BUCKET].head(key) is None
                assert b"private" in data
            # The killed attempt's intent is left for the reconciler; nothing was overwritten.
            intents = await rows(
                admin,
                "SELECT state FROM weather.upload_intent WHERE camera_id='skycam_east' "
                "AND product_key=:key ORDER BY fence",
                key=catalog.product_key(slot_for(slot)),
            )
            assert [i.state for i in intents] == ["pending", "committed"]
            # Derivatives: six sanitized images, embargoed from the capture upper bound.
            derivatives = await rows(
                admin,
                "SELECT d.role,d.state,m.class AS asset_class,m.bucket,m.sha256,m.extension,"
                "m.public_not_before "
                "FROM weather.still_derivative d JOIN weather.media_asset m ON m.id=d.asset_id "
                "WHERE d.archival_still_id=:id ORDER BY d.role",
                id=e.id,
            )
            assert [d.role for d in derivatives] == sorted(
                ["still", "still_960", "still_480", "still_webp", "still_webp960", "still_webp480"]
            )
            for d in derivatives:
                assert (d.asset_class, d.bucket, d.state) == ("still", MEDIA_BUCKET, "published")
                assert d.public_not_before == e.capture_upper + EMBARGO
                _, _, data = await buckets[MEDIA_BUCKET].read(
                    f"still/{d.sha256}.{d.extension}", keep=True
                )
                assert data is not None and scan(data) == [] and b"private" not in data
            async with api.connect() as connection:
                visible = {
                    r.camera_id: r
                    for r in await connection.execute(
                        text("SELECT * FROM public_api.stills WHERE slot_starts_at=:slot"),
                        {"slot": slot},
                    )
                }
            assert len(visible["skycam_east"].asset_ids) == 6
            assert visible["frontyard_1"].state == "missing"

            # A simulated video expiry for the same segment leaves the stills intact.
            video_files = []
            for role, extension in (
                ("native", "mp4"),
                ("web", "mp4"),
                ("mobile", "mp4"),
                ("poster", "jpg"),
            ):
                path = tmp_path / f"{role}.{extension}"
                path.write_bytes(role.encode() * 64 + os.urandom(16))
                video_files.append(ProductFile(role=role, extension=extension, path=path))
            segment = (
                await rows(
                    admin,
                    "SELECT * FROM weather.segment WHERE camera_id='skycam_east' "
                    "AND kind='overnight'",
                )
            )[0]
            video = await publisher.publish(
                Product.video(
                    camera_id="skycam_east",
                    cycle_date=segment.cycle_date,
                    segment="overnight",
                    starts_at=segment.starts_at,
                    ends_at=segment.ends_at,
                    frames=segment.frames,
                    public_not_before=segment.ends_at + EMBARGO,
                    state="published",
                    reason=None,
                    files=tuple(video_files),
                ),
                expected_revision=0,
                deadline=now + timedelta(hours=1),
            )
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE weather.media_asset SET state='unavailable' WHERE id IN "
                        "(SELECT asset_id FROM weather.publication_asset WHERE publication_id=:p)"
                    ),
                    {"p": video.publication_id},
                )
                await publish_catalog.withdraw(
                    connection, video.video_id, reason="expired", actor="retention-test"
                )
            async with api.connect() as connection:
                after = (
                    await connection.execute(
                        text(
                            "SELECT state,asset_ids FROM public_api.stills "
                            "WHERE camera_id='skycam_east' AND slot_starts_at=:slot"
                        ),
                        {"slot": slot},
                    )
                ).one()
            assert after.state == "on_target" and list(after.asset_ids) == list(
                visible["skycam_east"].asset_ids
            )
            assert await buckets[ORIGINALS_BUCKET].head(f"original/{e.sha256}.jpg") is not None

            # A correction is a new revision; the old one and its derivatives are retired.
            async with worker.begin() as connection:
                revision = await catalog.record(
                    connection,
                    camera="skycam_east",
                    slot=slot_for(slot),
                    choice=Choice("missing", "operator_correction", None, None, 0),
                )
            assert revision == 2
            history = await rows(
                admin,
                "SELECT revision,active,retired_at FROM weather.archival_still "
                "WHERE camera_id='skycam_east' AND slot_starts_at=:slot ORDER BY revision",
                slot=slot,
            )
            assert [(h.revision, h.active) for h in history] == [(1, False), (2, True)]
            assert history[0].retired_at is not None
            states = await rows(
                admin,
                "SELECT DISTINCT state FROM weather.still_derivative WHERE archival_still_id=:id",
                id=e.id,
            )
            assert [s.state for s in states] == ["retired"]
            async with worker.connect() as connection:
                await rejected(
                    connection,
                    "UPDATE weather.archival_still SET active=true,retired_at=NULL WHERE id=:id",
                    "55000",
                    id=e.id,
                )
            async with api.connect() as connection:
                corrected = (
                    await connection.execute(
                        text(
                            "SELECT revision,state,asset_ids FROM public_api.stills "
                            "WHERE camera_id='skycam_east' AND slot_starts_at=:slot"
                        ),
                        {"slot": slot},
                    )
                ).one()
            assert (corrected.revision, corrected.state, list(corrected.asset_ids)) == (
                2,
                "missing",
                [],
            )
        finally:
            for store in buckets.values():
                await store.aclose()
            for db in (admin, worker, api):
                await db.dispose()

    asyncio.run(check())


def test_a_late_fallback_is_embargoed_from_its_capture_upper_bound(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    slot = slot_for(now).start
    # The hour's only capture, moments ago: under 600 s before the hour closes, if late.
    at = max(slot, now - timedelta(seconds=20)).replace(microsecond=0)
    at -= timedelta(seconds=at.second % 2)
    data = camera_jpeg(9)
    seal(tmp_path, "skycam_east", [(at, data)])

    async def check() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()
        try:
            async with worker.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle_around(slot + timedelta(hours=2))])
            archive = archive_for(worker, Publisher(worker, buckets), tmp_path, "skycam_east")
            choice = await asyncio.to_thread(archive.select, slot)
            assert choice.winner is not None
            await archive.archive(slot_for(slot), choice, now)
            row = (await rows(admin, "SELECT * FROM weather.archival_still"))[0]
            embargo = row.capture_upper + EMBARGO
            assert embargo > datetime.now(UTC)
            pnb = await rows(
                admin,
                "SELECT DISTINCT m.public_not_before FROM weather.still_derivative d "
                "JOIN weather.media_asset m ON m.id=d.asset_id",
            )
            assert [p.public_not_before for p in pnb] == [embargo]
            published = await rows(
                admin, "SELECT public_not_before FROM weather.publication WHERE product='still'"
            )
            assert [p.public_not_before for p in published] == [embargo]
            async with api.connect() as connection:
                assert list(await connection.execute(text("SELECT * FROM public_api.stills"))) == []
                assets = await connection.execute(
                    text("SELECT count(*) FROM public_api.assets WHERE class='still'")
                )
                assert assets.scalar_one() == 0
        finally:
            for store in buckets.values():
                await store.aclose()
            for db in (admin, worker, api):
                await db.dispose()

    asyncio.run(check())
