# SPDX-License-Identifier: MIT
"""Renderer against real PostgreSQL and Garage: frozen schedule, boundary jobs, a stopped
camera, and exactly-once publication through #18 under kills.

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
                    Fraction((anchored.dusk - anchored.dawn) / timedelta(microseconds=1), 10**6),
                    Fraction(
                        (anchored.next_dawn - anchored.dusk) / timedelta(microseconds=1), 10**6
                    ),
                )
            )
            # A cycle that cannot start there (dusk before the frozen dawn) fails visibly.
            impossible = replace(moved_day, dusk=frozen[-1].next_dawn - timedelta(seconds=1))
            with pytest.raises(ScheduleError, match="frozen previous"):
                schedule.anchored(impossible, frozen[-1].next_dawn)
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

    async def commit(self, staged: Any) -> Any:
        self.kill_commit.check()  # objects uploaded and read back, intent registered
        return await super().commit(staged)


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
    for camera in ("skycam_east", "frontyard_1"):
        synthetic.write_chunks(
            tmp_path,
            camera,
            [
                synthetic.Frame(
                    int(dawn.timestamp() + s) * synthetic.NS,
                    synthetic.jpeg(synthetic.sky(W, H, s)),
                )
                for s in range(240)
            ],
            geometry=(W, H),
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
                expected=Expected(camera, 1000, 0, W, H),
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
