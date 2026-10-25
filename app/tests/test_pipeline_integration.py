# SPDX-License-Identifier: MIT
"""Renderer against real PostgreSQL and Garage: frozen schedule, boundary jobs, a stopped
camera, exactly-once publication through #18 under kills, and keograms (#37).

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import astuple, replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.db import jobs
from weather.db.models import metadata
from weather.pipeline import schedule, synthetic
from weather.pipeline.intake import Expected
from weather.pipeline.keogram import Region, Sample
from weather.pipeline.keogram_publish import Keograms, write_bins
from weather.pipeline.recipe import Recipe
from weather.pipeline.summary import AggregateWeather
from weather.pipeline.worker import PipelineConfig, Worker
from weather.publish.model import MEDIA_BUCKET, ORIGINALS_BUCKET
from weather.publish.objects import ObjectStore
from weather.publish.publisher import Publisher
from weather.scheduler.registry import JobContext
from weather.timemodel import (
    ScheduleError,
    SiteConfig,
    SolarCycle,
    allocate_frames,
    day_for,
    solar_cycle,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/time-model-vectors.json").read_text())
ANCHOR = VECTORS["solar_anchors"][0]
SITE = SiteConfig(latitude=ANCHOR["latitude"], longitude=ANCHOR["longitude"])
W, H = 32, 16


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


def test_schedule_freezes_days_ahead_matches_time_model_and_is_idempotent() -> None:
    async def check() -> None:
        worker = engine("weather_worker")
        start = date(2026, 12, 20)
        cycles = [solar_cycle(start + timedelta(days=n), SITE) for n in range(4)]
        try:
            async with worker.begin() as connection:
                assert await schedule.freeze(connection, SITE, cycles) == 4
            frozen = await rows(worker, "SELECT * FROM weather.solar_cycle ORDER BY cycle_date")
            segments = await rows(
                worker, "SELECT * FROM weather.segment ORDER BY cycle_date,camera_id,kind"
            )
            assert len(frozen) == 4 and len(segments) == 24
            for row, cycle in zip(frozen, cycles, strict=True):
                assert (row.dawn, row.dusk, row.next_dawn) == (
                    cycle.dawn,
                    cycle.dusk,
                    cycle.next_dawn,
                )
                assert (row.day_frames, row.night_frames) == (
                    cycle.allocation.day_frames,
                    cycle.allocation.night_frames,
                )
                assert row.day_frames + row.night_frames == 7200
                day = [s for s in segments if s.cycle_date == cycle.cycle_date]
                assert len(day) == 6 and {s.camera_id for s in day} == {
                    "skycam_east",
                    "skycam_west",
                    "frontyard_1",
                }
                for item in day:
                    expected = cycle.daytime if item.kind == "daytime" else cycle.overnight
                    assert (item.starts_at, item.ends_at, item.frames, item.state) == (
                        expected.start,
                        expected.end,
                        expected.frame_count,
                        "scheduled",
                    )
            assert all(a.next_dawn == b.dawn for a, b in zip(frozen, frozen[1:], strict=False))
            anchor = next(c for c in ANCHOR["cycles"] if c["local_date"] == "2026-12-21")
            row = next(r for r in frozen if r.cycle_date == date(2026, 12, 21))
            tolerance = timedelta(seconds=ANCHOR["tolerance_seconds"])
            assert abs(row.dawn - datetime.fromisoformat(anchor["dawn"])) <= tolerance
            assert abs(row.dusk - datetime.fromisoformat(anchor["dusk"])) <= tolerance
            # Rerun: nothing changes. A different computation never rewrites a frozen cycle.
            shifted = replace(SITE, dawn_offset_seconds=600)
            moved = [solar_cycle(start + timedelta(days=n), shifted) for n in range(4)]
            async with worker.begin() as connection:
                assert await schedule.freeze(connection, SITE, cycles) == 0
                assert await schedule.freeze(connection, shifted, moved) == 0
            assert (
                await rows(worker, "SELECT * FROM weather.solar_cycle ORDER BY cycle_date")
                == frozen
            )
            assert len(await rows(worker, "SELECT id FROM weather.segment")) == 24
            # A new day whose recomputed dawn moved (a rotated offset) starts at the frozen
            # next dawn instead of stalling; its allocation follows its real lengths.
            moved_day = solar_cycle(date(2026, 12, 24), shifted)
            assert moved_day.dawn != frozen[-1].next_dawn
            async with worker.begin() as connection:
                assert await schedule.freeze(connection, shifted, [moved_day]) == 1
            anchored = (
                await rows(
                    worker,
                    "SELECT * FROM weather.solar_cycle WHERE cycle_date=:d",
                    d=date(2026, 12, 24),
                )
            )[0]
            assert anchored.dawn == frozen[-1].next_dawn
            assert (anchored.dusk, anchored.next_dawn) == (moved_day.dusk, moved_day.next_dawn)
            assert (anchored.day_frames, anchored.night_frames) == astuple(
                allocate_frames(
                    Fraction((anchored.dusk - anchored.dawn) // timedelta(microseconds=1), 10**6),
                    Fraction(
                        (anchored.next_dawn - anchored.dusk) // timedelta(microseconds=1), 10**6
                    ),
                )
            )
            # A cycle that cannot start there (dusk before the frozen dawn) fails visibly.
            with pytest.raises(ScheduleError, match="frozen previous"):
                schedule.anchored(moved_day, moved_day.dusk + timedelta(seconds=1))
            # Overlapping current segments of one camera are refused by the database.
            with pytest.raises(Exception, match="ex_segment_current_overlap"):
                async with worker.begin() as connection:
                    await connection.execute(
                        text("""
                        INSERT INTO weather.segment(site,cycle_date,camera_id,kind,revision,
                          starts_at,ends_at,frames)
                        SELECT site,cycle_date,camera_id,kind,2,starts_at,ends_at,frames
                        FROM weather.segment WHERE camera_id='skycam_east' LIMIT 1
                    """)
                    )
        finally:
            await worker.dispose()

    asyncio.run(check())


def test_schedule_across_the_fall_dst_change_is_contiguous() -> None:
    async def check() -> None:
        worker = engine("weather_worker")
        cycles = [solar_cycle(date(2026, 10, 31) + timedelta(days=n), SITE) for n in range(3)]
        try:
            async with worker.begin() as connection:
                assert await schedule.freeze(connection, SITE, cycles) == 3
            frozen = await rows(worker, "SELECT * FROM weather.solar_cycle ORDER BY cycle_date")
            segments = await rows(
                worker,
                "SELECT * FROM weather.segment WHERE camera_id='skycam_east' ORDER BY starts_at",
            )
            assert [r.cycle_date for r in frozen] == [c.cycle_date for c in cycles]
            assert all(a.next_dawn == b.dawn for a, b in zip(frozen, frozen[1:], strict=False))
            assert all(r.day_frames + r.night_frames == 7200 for r in frozen)
            # The 25-hour local day changes nothing in UTC: segments tile time exactly.
            assert all(
                a.ends_at == b.starts_at for a, b in zip(segments, segments[1:], strict=False)
            )
            keys = {schedule.hour_key(s.ends_at) for s in segments}
            assert len(keys) == len(segments)
        finally:
            await worker.dispose()

    asyncio.run(check())


def test_tick_keys_are_deterministic_and_idempotent() -> None:
    async def check() -> None:
        worker = engine("weather_worker")
        now = datetime.now(UTC).replace(microsecond=0)
        today = day_for(now).local_date
        try:
            async with worker.begin() as connection:
                await schedule.freeze(
                    connection,
                    SITE,
                    [solar_cycle(today + timedelta(days=n), SITE) for n in (-1, 0, 1)],
                )
            for _ in range(2):
                async with worker.begin() as connection:
                    await schedule.periodic(connection, now)
            keys = [
                r.idempotency_key
                for r in await rows(worker, "SELECT idempotency_key FROM weather.job ORDER BY 1")
            ]
            assert f"schedule:{today.isoformat()}" in keys
            assert len(keys) == len(set(keys))
            boundaries = await rows(
                worker,
                "SELECT DISTINCT ends_at FROM weather.segment WHERE ends_at "
                "BETWEEN CAST(:lo AS timestamptz) AND CAST(:hi AS timestamptz)",
                lo=now - schedule.LOOKBACK,
                hi=now + schedule.HOUR,
            )
            for boundary in boundaries:
                assert f"finalize:{schedule.hour_key(boundary.ends_at)}" in keys
                assert f"deadline:{schedule.hour_key(boundary.ends_at)}" in keys
        finally:
            await worker.dispose()

    asyncio.run(check())


async def run_jobs(db: AsyncEngine, kind: str, handler: Any) -> int:
    """Claim and run every due job of one kind, as the scheduler loop would."""
    count = 0
    while True:
        async with db.begin() as connection:
            job = await jobs.claim(connection, kinds={kind})
        if job is None:
            return count
        await handler(JobContext(db, job))
        count += 1


class KillOnce:
    """Dies on the first call, like a process killed at that point."""

    def __init__(self, armed: bool = True) -> None:
        self.armed = armed

    def check(self) -> None:
        if self.armed:
            self.armed = False
            raise RuntimeError("killed")


class HookedPublisher(Publisher):
    """#18's publisher with a kill between staging and commit, and a hook before publish."""

    def __init__(self, *args: Any, kill_commit: KillOnce, before: Any = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.kill_commit, self.before = kill_commit, before

    async def publish(self, *args: Any, **kwargs: Any) -> Any:
        if self.before is not None:
            hook, self.before = self.before, None
            await hook()
        return await super().publish(*args, **kwargs)

    async def commit(self, staged: Any, then: Any = None) -> Any:
        self.kill_commit.check()  # objects uploaded and read back, intent registered
        return await super().commit(staged, then)


def test_stopped_camera_is_unavailable_and_others_publish_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    # A short synthetic cycle that straddles a UTC hour: [h:59, h+1:01) and [h+1:01, h+1:03).
    # Boundaries 4-5 h ago: inside the tick's 6 h lookback and past the 2 h deadline.
    dawn = (now - timedelta(hours=4)).replace(minute=59, second=0)
    if dawn > now - timedelta(hours=4):
        dawn -= timedelta(hours=1)
    cycle = SolarCycle(
        cycle_date=day_for(dawn).local_date,
        dawn=dawn,
        dusk=dawn + timedelta(seconds=120),
        next_dawn=dawn + timedelta(seconds=240),
        sunrise=dawn,
        sunset=dawn + timedelta(seconds=120),
        allocation=allocate_frames(120, 120),
        config_hash="0" * 64,
    )
    # East captures at 1 Hz, frontyard at the commissioned 0.5 Hz (#4).
    cadence = {"skycam_east": 1000, "frontyard_1": 2000}
    for camera, cadence_ms in cadence.items():
        synthetic.write_chunks(
            tmp_path,
            camera,
            [
                synthetic.Frame(
                    int(dawn.timestamp() + s) * synthetic.NS,
                    synthetic.jpeg(synthetic.sky(W, H, s)),
                )
                for s in range(0, 240, cadence_ms // 1000)
            ],
            geometry=(W, H),
            cadence_ms=cadence_ms,
            pod=camera.replace("_", "-"),
        )
    assert len(list((tmp_path / "sealed/skycam_east").iterdir())) == 2  # two hour directories

    async def check() -> None:
        admin, worker_db, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()

        def worker(camera: str, publisher: Publisher) -> Worker:
            # A fresh Worker each time: nothing in memory survives a restart.
            config = PipelineConfig(
                camera=camera,
                staging=tmp_path,
                expected=Expected(camera, cadence[camera], 0, W, H),
                recipe=Recipe(),
            )
            return Worker(config, worker_db, publisher, AggregateWeather(worker_db))

        try:
            async with worker_db.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle])
                await schedule.periodic(connection, now)
            keys = [r.idempotency_key for r in await rows(worker_db, "SELECT * FROM weather.job")]
            assert any(k.startswith("finalize:") for k in keys)
            await run_jobs(worker_db, schedule.FINALIZE_KIND, schedule.finalize_job)
            states = await rows(worker_db, "SELECT camera_id,state FROM weather.segment")
            assert len(states) == 6 and {s.state for s in states} == {"processing"}

            # East: killed after staging (objects uploaded, intent registered) before commit,
            # then restarted from durable state.
            kill_commit = KillOnce()
            await worker(
                "skycam_east", HookedPublisher(worker_db, buckets, kill_commit=kill_commit)
            ).step(now)
            assert not kill_commit.armed
            for _ in range(2):
                fresh = worker(
                    "skycam_east", HookedPublisher(worker_db, buckets, kill_commit=KillOnce(False))
                )
                await fresh.step(now)

            pending = await rows(
                admin,
                "SELECT count(*) AS n FROM weather.upload_intent "
                "WHERE camera_id='skycam_east' AND state='pending'",
            )
            assert pending[0].n == 1  # the killed attempt's intent, for the reconciler

            # Frontyard: the deadline commits its placeholders while the pipeline is about to
            # publish (a race), and the segment update after the real publish is killed.
            async def deadline() -> None:
                await run_jobs(worker_db, schedule.DEADLINE_KIND, schedule.deadline_job)

            kill_finish = KillOnce()
            original_finish = schedule.finish

            async def killed_finish(*args: Any, **kwargs: Any) -> None:
                kill_finish.check()
                await original_finish(*args, **kwargs)

            monkeypatch.setattr(schedule, "finish", killed_finish)
            await worker(
                "frontyard_1",
                HookedPublisher(worker_db, buckets, kill_commit=KillOnce(False), before=deadline),
            ).step(now)
            monkeypatch.setattr(schedule, "finish", original_finish)
            assert not kill_finish.armed
            await worker(
                "frontyard_1", HookedPublisher(worker_db, buckets, kill_commit=KillOnce(False))
            ).step(now)

            final = {
                (r.camera_id, r.kind): r
                for r in await rows(
                    worker_db,
                    "SELECT camera_id,kind,state,reason,render_result,mezzanine_durable_at,"
                    "mezzanine_through,ends_at FROM weather.segment",
                )
            }
            assert len(final) == 6
            for kind in ("daytime", "overnight"):
                west = final[("skycam_west", kind)]
                assert (west.state, west.reason) == ("unavailable", "deadline_missed")
                for camera in ("skycam_east", "frontyard_1"):
                    row = final[(camera, kind)]
                    assert row.state == "published", (camera, kind, row.state, row.reason)
                    assert row.mezzanine_durable_at is not None
                    assert row.mezzanine_through == row.ends_at
                    assert row.render_result["frames"] == 3600
                    assert row.render_result["slots"]["expected"] == 120_000 // cadence[camera]
                    assert row.render_result["slots"]["fraction"] == 1.0
            published = {
                (p.camera_id, p.product_key): (p.n, p.r, p.states)
                for p in await rows(
                    admin,
                    "SELECT camera_id,product_key,count(*) AS n,max(revision) AS r,"
                    "string_agg(state,',' ORDER BY revision) AS states "
                    "FROM weather.publication WHERE product='video' GROUP BY 1,2",
                )
            }
            assert len(published) == 6
            for (camera, _), (n, revision, history) in published.items():
                expected = {
                    "skycam_east": (1, 1, "published"),
                    "frontyard_1": (2, 2, "unavailable,published"),  # placeholder superseded
                    "skycam_west": (1, 1, "unavailable"),
                }[camera]
                assert (n, revision, history) == expected, camera
            # The public catalog shows the stopped camera honestly (R03).
            async with api.connect() as connection:
                public = list(
                    await connection.execute(
                        text("SELECT camera_id,segment,state,reason FROM public_api.videos")
                    )
                )
            assert len(public) == 6
            for row in public:
                if row.camera_id == "skycam_west":
                    assert (row.state, row.reason) == ("unavailable", "deadline_missed")
                else:
                    assert row.state == "published"
            videos = await rows(admin, "SELECT public_not_before,ends_at FROM weather.video")
            assert all(v.public_not_before >= v.ends_at + timedelta(seconds=630) for v in videos)
            summary = await AggregateWeather(worker_db).summary(dawn, dawn + timedelta(seconds=120))
            assert summary is not None and summary["items"]
        finally:
            for store in buckets.values():
                await store.aclose()
            await admin.dispose()
            await worker_db.dispose()
            await api.dispose()

    asyncio.run(check())


# Keograms (#37) --------------------------------------------------------------------------------

REGION = Region(version="test-1", x=(12, 20), y=(0, H), orientation="vertical")


def past_cycle(now: datetime) -> SolarCycle:
    dawn = (now - timedelta(hours=4)).replace(minute=10, second=0)
    return SolarCycle(
        cycle_date=day_for(dawn).local_date,
        dawn=dawn,
        dusk=dawn + timedelta(seconds=120),
        next_dawn=dawn + timedelta(seconds=240),
        sunrise=dawn,
        sunset=dawn + timedelta(seconds=120),
        allocation=allocate_frames(120, 120),
        config_hash="0" * 64,
    )


def east_worker(staging: Path, db: AsyncEngine, publisher: Publisher) -> Worker:
    config = PipelineConfig(
        camera="skycam_east",
        staging=staging,
        expected=Expected("skycam_east", 1000, 0, W, H),
        recipe=Recipe(),
        region=REGION,
    )
    return Worker(config, db, publisher, AggregateWeather(db))


def east_frames(staging: Path, dawn: datetime) -> None:
    synthetic.write_chunks(
        staging,
        "skycam_east",
        [
            synthetic.Frame(
                int(dawn.timestamp() + s) * synthetic.NS, synthetic.jpeg(synthetic.sky(W, H, s))
            )
            for s in range(240)
        ],
        geometry=(W, H),
    )


async def start_segments(db: AsyncEngine, cycle: SolarCycle, now: datetime) -> None:
    async with db.begin() as connection:
        await schedule.freeze(connection, SITE, [cycle])
        await schedule.periodic(connection, now)
    await run_jobs(db, schedule.FINALIZE_KIND, schedule.finalize_job)


async def video_sidecar_keogram(admin: AsyncEngine, kind: str) -> Any:
    found = await rows(
        admin,
        """SELECT m.sidecar FROM weather.video v
           JOIN weather.publication_asset pa ON pa.publication_id=v.publication_id
             AND pa.role='sidecar'
           JOIN weather.media_asset m ON m.id=pa.asset_id
           WHERE v.camera_id='skycam_east' AND v.segment=:kind""",
        kind=kind,
    )
    return json.loads(bytes(found[0].sidecar))["keogram"]


def test_keograms_publish_after_their_videos_and_drop_their_bins(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    cycle = past_cycle(now)
    east_frames(tmp_path, cycle.dawn)

    async def check() -> None:
        admin, worker_db, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()
        try:
            await start_segments(worker_db, cycle, now)
            seen: list[int] = []
            original = write_bins

            async def counting(*args: Any, **kwargs: Any) -> None:
                await original(*args, **kwargs)
                found = await rows(admin, "SELECT count(*) AS n FROM weather.keogram_bin")
                seen.append(found[0].n)

            import weather.pipeline.keogram_publish as module

            module.write_bins = counting  # type: ignore[assignment]
            try:
                await east_worker(tmp_path, worker_db, Publisher(worker_db, buckets)).step(now)
            finally:
                module.write_bins = original
            # Bins streamed in before each block was durable (two segments, 60-frame blocks).
            assert seen and max(seen) > 0
            keograms = {
                r.kind: r
                for r in await rows(
                    admin,
                    "SELECT s.kind,k.* FROM weather.keogram k JOIN weather.segment s "
                    "ON s.id=k.segment_id WHERE s.camera_id='skycam_east'",
                )
            }
            assert set(keograms) == {"daytime", "overnight"}
            for kind, row in keograms.items():
                # 120 one-second captures across 1,440 bins: observed bins, explicit gaps.
                assert (row.state, row.reason, row.observed) == ("partial", "gaps", 120), kind
                assert row.video_synced
                ends = cycle.dusk if kind == "daytime" else cycle.next_dawn
                assert row.public_not_before == max(ends, row.capture_upper_bound) + timedelta(
                    seconds=630
                )
            # The segment's bins are gone once the PNG and thumbnail committed and read back.
            assert (await rows(admin, "SELECT count(*) AS n FROM weather.keogram_bin"))[0].n == 0
            assets = await rows(
                admin,
                "SELECT p.product_key,pa.role,m.class,m.width,m.height,m.public_not_before "
                "FROM weather.publication p JOIN weather.publication_asset pa "
                "ON pa.publication_id=p.id JOIN weather.media_asset m ON m.id=pa.asset_id "
                "WHERE p.product='keogram' ORDER BY 1,2",
            )
            roles = {(a.product_key.split("/")[1], a.role): a for a in assets}
            assert set(roles) == {
                (kind, role)
                for kind in ("daytime", "overnight")
                for role in ("keogram", "keogram_thumb", "sidecar")
            }
            assert (roles[("daytime", "keogram")].width, roles[("daytime", "keogram")].height) == (
                1440,
                H,
            )
            assert roles[("daytime", "keogram_thumb")].width == 360
            # The video published first with its keogram pending; a sidecar revision follows.
            history = await rows(
                admin,
                "SELECT product_key,string_agg(state,',' ORDER BY revision) AS states "
                "FROM weather.publication WHERE product='video' GROUP BY 1",
            )
            assert {h.states for h in history} == {"published,published"}
            for kind in ("daytime", "overnight"):
                assert await video_sidecar_keogram(admin, kind) == {
                    "state": "partial",
                    "reason": "gaps",
                }
            async with api.connect() as connection:
                public = list(
                    await connection.execute(
                        text(
                            "SELECT segment,state,image_asset_id,thumbnail_asset_id,"
                            "sidecar_asset_id,observed,gaps,video_id FROM public_api.keograms "
                            "WHERE camera_id='skycam_east'"
                        )
                    )
                )
                videos = list(
                    await connection.execute(
                        text("SELECT segment,keogram_state,keogram_asset_id FROM public_api.videos")
                    )
                )
            assert {(p.segment, p.state, p.observed) for p in public} == {
                ("daytime", "partial", 120),
                ("overnight", "partial", 120),
            }
            assert all(p.image_asset_id and p.thumbnail_asset_id and p.video_id for p in public)
            assert {(v.keogram_state, v.keogram_asset_id is not None) for v in videos} == {
                ("partial", True)
            }
            # A second pass has nothing left to do.
            await east_worker(tmp_path, worker_db, Publisher(worker_db, buckets)).step(now)
            assert (
                await rows(admin, "SELECT count(*) AS n FROM weather.publication")
            )[0].n == len(history) * 2 + 2
        finally:
            for store in buckets.values():
                await store.aclose()
            await admin.dispose()
            await worker_db.dispose()
            await api.dispose()

    asyncio.run(check())


class KeogramFails(Publisher):
    async def publish(self, product: Any, **kwargs: Any) -> Any:
        if product.kind == "keogram":
            raise RuntimeError("injected keogram failure")
        return await super().publish(product, **kwargs)


def test_a_failed_keogram_leaves_its_video_published(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    cycle = past_cycle(now)
    east_frames(tmp_path, cycle.dawn)

    async def check() -> None:
        admin, worker_db, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()
        try:
            await start_segments(worker_db, cycle, now)
            worker = east_worker(tmp_path, worker_db, KeogramFails(worker_db, buckets))
            for attempt in range(3):  # every attempt fails; the third is final
                await worker.step(now + timedelta(minutes=2 * attempt))
            states = await rows(
                admin,
                "SELECT k.state,k.reason,k.attempts FROM weather.keogram k JOIN weather.segment s "
                "ON s.id=k.segment_id WHERE s.camera_id='skycam_east'",
            )
            assert {(s.state, s.reason, s.attempts) for s in states} == {
                ("failed", "publish_failed", 3)
            }
            assert (await rows(admin, "SELECT count(*) AS n FROM weather.keogram_bin"))[0].n == 0
            async with api.connect() as connection:
                videos = list(
                    await connection.execute(
                        text("SELECT state,keogram_state,native_asset_id FROM public_api.videos")
                    )
                )
                keograms = list(
                    await connection.execute(
                        text(
                            "SELECT state,reason,image_asset_id FROM public_api.keograms "
                            "WHERE camera_id='skycam_east'"
                        )
                    )
                )
            assert {(v.state, v.keogram_state, v.native_asset_id is not None) for v in videos} == {
                ("published", "failed", True)
            }
            assert {tuple(k) for k in keograms} == {("failed", "publish_failed", None)}
            for kind in ("daytime", "overnight"):
                assert await video_sidecar_keogram(admin, kind) == {
                    "state": "failed",
                    "reason": "publish_failed",
                }
            segments = await rows(
                admin, "SELECT state FROM weather.segment WHERE camera_id='skycam_east'"
            )
            assert {s.state for s in segments} == {"published"}
        finally:
            for store in buckets.values():
                await store.aclose()
            await admin.dispose()
            await worker_db.dispose()
            await api.dispose()

    asyncio.run(check())


def test_a_fresh_keogram_is_embargoed_from_its_newest_bin(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    # The segment ended 700 s ago (past its end + 630 s guard), and its newest capture's upper
    # bound is 120 s past the end, so now lies inside that bound's own 630 s embargo.
    dawn = now - timedelta(seconds=700 + 144)
    cycle = SolarCycle(
        cycle_date=day_for(dawn).local_date,
        dawn=dawn,
        dusk=dawn + timedelta(seconds=144),
        next_dawn=dawn + timedelta(seconds=400),
        sunrise=dawn,
        sunset=dawn + timedelta(seconds=144),
        allocation=allocate_frames(144, 256),
        config_hash="0" * 64,
    )

    async def check() -> None:
        admin, worker_db, api = engine(), engine("weather_worker"), engine("weather_api")
        buckets = stores()
        try:
            async with worker_db.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle])
            segment = (
                await rows(
                    admin,
                    "SELECT * FROM weather.segment WHERE camera_id='skycam_east' "
                    "AND kind='daytime'",
                )
            )[0]
            job = schedule.SegmentJob(
                "skycam_east",
                segment.cycle_date,
                "daytime",
                segment.revision,
                segment.starts_at,
                segment.ends_at,
                segment.frames,
            )
            start = int(segment.starts_at.timestamp()) * 1_000_000
            # Every 0.1 s bin observed; the newest capture's upper bound is after the end.
            upper_last = int(segment.ends_at.timestamp()) * 1_000_000 + 120_000_000
            samples = [
                Sample(
                    b,
                    0,
                    start + b * 100_000 + 50_000,
                    f"c{b}",
                    upper_last if b == 1439 else start + b * 100_000 + 60_000,
                    None,
                    bytes([b % 256] * (H * 3)),
                )
                for b in range(1440)
            ]
            async with worker_db.begin() as connection:
                await write_bins(connection, job, REGION, samples, None)
                await connection.execute(
                    text("UPDATE weather.segment SET finalized_at=clock_timestamp() WHERE id=:id"),
                    {"id": segment.id},
                )
            keograms = Keograms(
                worker_db, Publisher(worker_db, buckets), REGION, asyncio.get_running_loop()
            )
            await keograms.sweep("skycam_east", now)
            published = await rows(
                admin,
                "SELECT m.id,m.class,m.public_not_before FROM weather.publication p "
                "JOIN weather.publication_asset pa ON pa.publication_id=p.id "
                "JOIN weather.media_asset m ON m.id=pa.asset_id WHERE p.product='keogram'",
            )
            expected = segment.ends_at + timedelta(seconds=120 + 630)
            assert segment.ends_at + timedelta(seconds=630) < datetime.now(UTC) < expected
            assert len(published) == 3
            assert all(item.public_not_before == expected for item in published)
            state = (
                await rows(
                    admin, "SELECT state,observed FROM weather.keogram WHERE segment_id=:id",
                    id=segment.id,
                )
            )[0]
            assert (state.state, state.observed) == ("published", 1440)
            async with api.connect() as connection:
                visible = list(
                    await connection.execute(
                        text(
                            "SELECT id FROM public_api.assets WHERE id = ANY(CAST(:ids AS uuid[]))"
                        ),
                        {"ids": [item.id for item in published]},
                    )
                )
                listed = list(
                    await connection.execute(
                        text(
                            "SELECT state,image_asset_id,thumbnail_asset_id,observed,gaps "
                            "FROM public_api.keograms WHERE id=:id"
                        ),
                        {"id": segment.id},
                    )
                )
            # Past the segment's own guard the row lists, but as pending with no file or
            # metadata until the newest bin's embargo lapses.
            assert visible == []
            assert [tuple(row) for row in listed] == [("pending", None, None, None, None)]
        finally:
            for store in buckets.values():
                await store.aclose()
            await admin.dispose()
            await worker_db.dispose()
            await api.dispose()

    asyncio.run(check())


def test_bin_merge_keeps_the_best_capture_and_superseded_segments_fail(tmp_path: Path) -> None:
    """`write_bins` merges by (distance, capture time, capture ID): a worse sample never replaces
    a better one, whichever block (or re-render) delivers it. A superseded segment's keogram
    fails and its bins go."""
    now = datetime.now(UTC).replace(microsecond=0)
    cycle = past_cycle(now)

    async def check() -> None:
        admin, worker_db = engine(), engine("weather_worker")
        try:
            async with worker_db.begin() as connection:
                await schedule.freeze(connection, SITE, [cycle])
            segment = (
                await rows(
                    admin,
                    "SELECT * FROM weather.segment WHERE camera_id='skycam_east' "
                    "AND kind='daytime'",
                )
            )[0]
            job = schedule.SegmentJob(
                "skycam_east",
                segment.cycle_date,
                "daytime",
                segment.revision,
                segment.starts_at,
                segment.ends_at,
                segment.frames,
            )
            start = int(segment.starts_at.timestamp()) * 1_000_000

            def sample(distance: int, offset_us: int, capture_id: str, value: int) -> Sample:
                captured = start + offset_us
                return Sample(5, distance, captured, capture_id, captured + 1, None,
                              bytes([value]) * (H * 3))

            async def write(*samples: Sample) -> Any:
                # Each call is one block's commit; bin 5 spans all of them.
                for item in samples:
                    async with worker_db.begin() as connection:
                        await write_bins(connection, job, REGION, [item], None)
                return (
                    await rows(
                        admin,
                        "SELECT distance,capture_id,strip FROM weather.keogram_bin "
                        "WHERE segment_id=:id AND bin=5",
                        id=segment.id,
                    )
                )[0]

            kept = await write(sample(10, 300, "w1", 1), sample(2, 400, "b", 2),
                               sample(5, 500, "w2", 3))
            assert (kept.distance, kept.capture_id, bytes(kept.strip)[:1]) == (2, "b", b"\x02")
            # Equal distance: the earlier capture; equal time too: the lower ID (C collation).
            kept = await write(sample(2, 350, "z", 4), sample(2, 350, "a10", 5),
                               sample(2, 350, "a9", 6))
            assert (kept.capture_id, bytes(kept.strip)[:1]) == ("a10", b"\x05")
            # A clock correction supersedes the segment: its keogram fails and its bins go.
            async with admin.begin() as connection:
                await connection.execute(
                    text("UPDATE weather.segment SET current=false WHERE id=:id"),
                    {"id": segment.id},
                )
            keograms = Keograms(
                worker_db, Publisher(worker_db, {}), REGION, asyncio.get_running_loop()
            )
            await keograms.sweep("skycam_east", now)
            state = (
                await rows(
                    admin, "SELECT state,reason FROM weather.keogram WHERE segment_id=:id",
                    id=segment.id,
                )
            )[0]
            assert (state.state, state.reason) == ("failed", "superseded")
            assert (
                await rows(
                    admin, "SELECT count(*) AS n FROM weather.keogram_bin WHERE segment_id=:id",
                    id=segment.id,
                )
            )[0].n == 0
        finally:
            await admin.dispose()
            await worker_db.dispose()

    asyncio.run(check())
