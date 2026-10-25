# SPDX-License-Identifier: MIT
"""Frozen solar schedule, segment lifecycle and the scheduler's deterministic jobs.

The minute tick enqueues `schedule:<local date>` (freeze today and the next three local days)
and, for each recent or imminent segment boundary b, `finalize:<utc hour of b>` (due b + 30 s:
scheduled -> processing) and `deadline:<utc hour of b>` (due b + 2 h: anything still
unpublished becomes unavailable with a reason). Keys are idempotent. Every camera gets its
rows whether or not its pipeline runs, so a stopped camera still shows up as unavailable.

Cycles are written once and never changed: a later computation that disagrees is logged and
counted, not applied. The site comes from the optional `weather-site` Secret; without it the
schedule job fails visibly (metric, log, failed job) and nothing is invented.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import astral
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.db import jobs
from weather.db.days import ensure_days
from weather.publish import catalog
from weather.publish.model import EMBARGO, Product
from weather.publish.publisher import Publisher
from weather.scheduler.registry import JobContext, Registry
from weather.timemodel import (
    TZDATA_VERSION,
    ScheduleError,
    SiteConfig,
    SolarCycle,
    allocate_frames,
    day_for,
    load_site,
    solar_cycle,
    weather_day,
)
from weather.timemodel._instant import duration_seconds

from . import metrics
from .recipe import CAMERAS
from .render import SegmentJob

LOG = logging.getLogger("weather.pipeline")
SITE = "home"
DAYS_AHEAD = 3
SCHEDULE_KIND = "segment.schedule"
FINALIZE_KIND = "segment.finalize"
DEADLINE_KIND = "segment.deadline"
PROCESSING_AFTER = timedelta(seconds=30)
DEADLINE_AFTER = timedelta(hours=2)
LOOKBACK = timedelta(hours=6)
LATE_RENDER = timedelta(hours=12)
HOUR = timedelta(hours=1)


def hour_of(instant: datetime) -> datetime:
    return instant.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hour_key(instant: datetime) -> str:
    return hour_of(instant).strftime("%Y-%m-%dT%HZ")


def anchored(cycle: SolarCycle, dawn: datetime) -> SolarCycle:
    try:
        return SolarCycle(
            cycle_date=cycle.cycle_date,
            dawn=dawn,
            dusk=cycle.dusk,
            next_dawn=cycle.next_dawn,
            sunrise=cycle.sunrise,
            sunset=cycle.sunset,
            allocation=allocate_frames(
                duration_seconds(cycle.dusk - dawn), duration_seconds(cycle.next_dawn - cycle.dusk)
            ),
            config_hash=cycle.config_hash,
        )
    except ScheduleError:
        metrics.SCHEDULE_FAILURES.labels("discontinuity").inc()
        raise ScheduleError("New cycle cannot start at the frozen previous next dawn") from None


async def freeze(
    connection: AsyncConnection, site: SiteConfig, cycles: Sequence[SolarCycle]
) -> int:
    """Insert missing cycles and their six segment rows; existing rows never change."""
    if not cycles:
        return 0
    first = min(cycle.cycle_date for cycle in cycles)
    last = max(cycle.cycle_date for cycle in cycles)
    await ensure_days(connection, first, last + timedelta(days=1))
    inserted = 0
    for cycle in sorted(cycles, key=lambda item: item.cycle_date):
        frozen = (
            await connection.execute(
                text("""
            SELECT dawn,dusk,next_dawn,day_frames,night_frames FROM weather.solar_cycle
            WHERE site=:site AND cycle_date=:date
        """),
                {"site": SITE, "date": cycle.cycle_date},
            )
        ).one_or_none()
        if frozen is None:
            previous = (
                await connection.execute(
                    text("""
                SELECT next_dawn FROM weather.solar_cycle WHERE site=:site AND cycle_date=:date
            """),
                    {"site": SITE, "date": cycle.cycle_date - timedelta(days=1)},
                )
            ).scalar_one_or_none()
            if previous is not None and previous != cycle.dawn:
                # A changed offset or library moved dawn: the frozen past wins. The new cycle
                # starts at the frozen next dawn and its allocation follows its real lengths.
                cycle = anchored(cycle, previous)
                metrics.SCHEDULE_ANCHORED.inc()
                LOG.warning("New cycle anchored to the frozen previous next dawn")
            await connection.execute(
                text("""
                INSERT INTO weather.solar_cycle(site,cycle_date,dawn,dusk,next_dawn,day_frames,
                  night_frames,provenance_hash,astral_version,tzdata_version)
                VALUES (:site,:date,:dawn,:dusk,:next_dawn,:day,:night,:hash,:astral,:tzdata)
            """),
                {
                    "site": SITE,
                    "date": cycle.cycle_date,
                    "dawn": cycle.dawn,
                    "dusk": cycle.dusk,
                    "next_dawn": cycle.next_dawn,
                    "day": cycle.allocation.day_frames,
                    "night": cycle.allocation.night_frames,
                    "hash": site.config_hash,
                    "astral": astral.__version__,
                    "tzdata": TZDATA_VERSION,
                },
            )
            inserted += 1
            bounds = (cycle.dawn, cycle.dusk, cycle.next_dawn)
            frames = (cycle.allocation.day_frames, cycle.allocation.night_frames)
        else:
            bounds = (frozen.dawn, frozen.dusk, frozen.next_dawn)
            frames = (int(frozen.day_frames), int(frozen.night_frames))
            if bounds != (cycle.dawn, cycle.dusk, cycle.next_dawn):
                # Frozen: never rewritten. A deliberate change is a new segment revision.
                metrics.SCHEDULE_FAILURES.labels("drift").inc()
                LOG.warning("Recomputed cycle differs from the frozen one; keeping the frozen")
        for camera in CAMERAS:
            for kind, start, end, count in (
                ("daytime", bounds[0], bounds[1], frames[0]),
                ("overnight", bounds[1], bounds[2], frames[1]),
            ):
                await connection.execute(
                    text("""
                    INSERT INTO weather.segment(site,cycle_date,camera_id,kind,starts_at,ends_at,
                      frames)
                    VALUES (:site,:date,:camera,:kind,:start,:end,:frames)
                    ON CONFLICT DO NOTHING
                """),
                    {
                        "site": SITE,
                        "date": cycle.cycle_date,
                        "camera": camera,
                        "kind": kind,
                        "start": start,
                        "end": end,
                        "frames": count,
                    },
                )
    return inserted


# Scheduler jobs ------------------------------------------------------------------------------


async def periodic(connection: AsyncConnection, now: datetime) -> None:
    today = day_for(now).local_date
    await jobs.enqueue(
        connection,
        kind=SCHEDULE_KIND,
        key=f"schedule:{today.isoformat()}",
        payload={"start": today.isoformat()},
        not_before=now.astimezone(UTC),
        deadline=weather_day(today).end,
    )
    boundaries = (
        await connection.execute(
            text("""
        SELECT ends_at FROM weather.segment WHERE current
          AND ends_at >= CAST(:lo AS timestamptz) AND ends_at <= CAST(:hi AS timestamptz)
        GROUP BY ends_at ORDER BY ends_at
    """),
            {"lo": now - LOOKBACK, "hi": now + HOUR},
        )
    ).scalars()
    for boundary in boundaries:
        hour = hour_of(boundary)
        payload = {"hour": hour.isoformat()}
        await jobs.enqueue(
            connection,
            kind=FINALIZE_KIND,
            key=f"finalize:{hour_key(boundary)}",
            payload=payload,
            not_before=boundary + PROCESSING_AFTER,
            deadline=hour + HOUR + LOOKBACK,
        )
        await jobs.enqueue(
            connection,
            kind=DEADLINE_KIND,
            key=f"deadline:{hour_key(boundary)}",
            payload=payload,
            not_before=boundary + DEADLINE_AFTER,
            deadline=hour + HOUR + DEADLINE_AFTER + LOOKBACK,
        )


async def schedule_job(context: JobContext) -> None:
    start = date.fromisoformat(str(context.job.payload["start"]))
    try:
        site = load_site()
    except ScheduleError:
        metrics.SITE_CONFIGURED.set(0)
        metrics.SCHEDULE_FAILURES.labels("site").inc()
        LOG.error("Private site configuration unavailable; schedule not frozen")
        raise
    metrics.SITE_CONFIGURED.set(1)
    try:
        cycles = [solar_cycle(start + timedelta(days=n), site) for n in range(DAYS_AHEAD + 1)]
    except ScheduleError:
        metrics.SCHEDULE_FAILURES.labels("solar").inc()
        raise

    async def write(connection: AsyncConnection) -> None:
        await freeze(connection, site, cycles)

    await context.commit(write)


async def sweep_processing(connection: AsyncConnection, hour: datetime) -> int:
    result = await connection.execute(
        text("""
        UPDATE weather.segment SET state='processing',updated_at=clock_timestamp()
        WHERE current AND state='scheduled' AND ends_at >= :hour AND ends_at < :next
          AND ends_at + CAST(:after AS interval) <= clock_timestamp()
    """),
        {"hour": hour, "next": hour + HOUR, "after": PROCESSING_AFTER},
    )
    return result.rowcount


def due(alias: str = "") -> str:
    """Current, unfinished segments of the hour whose deadline has passed."""
    a = f"{alias}." if alias else ""
    return (
        f"{a}current AND {a}state IN ('scheduled','processing') AND {a}ends_at >= :hour"
        f" AND {a}ends_at < :next AND {a}ends_at + CAST(:after AS interval) <= clock_timestamp()"
    )


async def sweep_deadline(connection: AsyncConnection, hour: datetime) -> list[str]:
    """Segments still unfinished two hours after their boundary. One whose video is already
    committed (a publish whose acknowledgement was lost) takes the video's state; the rest
    become unavailable/deadline_missed. Returns the cameras that missed."""
    params = {"hour": hour, "next": hour + HOUR, "after": DEADLINE_AFTER}
    await connection.execute(
        text(f"""
        UPDATE weather.segment s SET state=v.state,reason=v.reason,updated_at=clock_timestamp()
        FROM weather.video v
        WHERE {due("s")} AND v.camera_id=s.camera_id
          AND v.cycle_date=s.cycle_date AND v.segment=s.kind
          AND NOT (v.state='unavailable' AND v.reason='deadline_missed')
    """),
        params,
    )
    rows = await connection.execute(
        text(f"""
        UPDATE weather.segment SET state='unavailable',reason='deadline_missed',
          updated_at=clock_timestamp()
        WHERE {due()}
        RETURNING camera_id
    """),
        params,
    )
    return [str(row.camera_id) for row in rows]


async def current_video(engine: AsyncEngine, job: SegmentJob) -> catalog.Current:
    identity = Product.video(
        camera_id=job.camera,
        cycle_date=job.cycle_date,
        segment=job.kind,
        starts_at=job.starts_at,
        ends_at=job.ends_at,
        frames=job.frames,
        public_not_before=job.ends_at + EMBARGO,
        state="unavailable",
        reason="identity",
        files=(),
    )
    async with engine.connect() as connection:
        return await catalog.current(connection, identity)


def is_placeholder(current: catalog.Current) -> bool:
    return current.state == "unavailable" and current.reason == "deadline_missed"


async def publish_missed(engine: AsyncEngine, hour: datetime) -> int:
    """Commit a database-only `unavailable` video for each missed segment that has no video,
    through #18's fenced commit, so the public catalog shows it (R03). No object is uploaded,
    so no bucket key is needed. A pipeline that publishes later supersedes it."""
    async with engine.connect() as connection:
        missed = list(
            await connection.execute(
                text(f"""
            SELECT s.camera_id,s.cycle_date,s.kind,s.revision,s.starts_at,s.ends_at,s.frames
            FROM weather.segment s WHERE {due("s")}
              AND NOT EXISTS (SELECT 1 FROM weather.video v WHERE v.camera_id=s.camera_id
                AND v.cycle_date=s.cycle_date AND v.segment=s.kind)
        """),
                {"hour": hour, "next": hour + HOUR, "after": DEADLINE_AFTER},
            )
        )
    publisher = Publisher(engine, {})
    count = 0
    for row in missed:
        job = SegmentJob(
            str(row.camera_id),
            row.cycle_date,
            str(row.kind),
            int(row.revision),
            row.starts_at,
            row.ends_at,
            int(row.frames),
        )
        if (await current_video(engine, job)).revision:
            continue
        product = Product.video(
            camera_id=job.camera,
            cycle_date=job.cycle_date,
            segment=job.kind,
            starts_at=job.starts_at,
            ends_at=job.ends_at,
            frames=job.frames,
            public_not_before=job.ends_at + EMBARGO,
            state="unavailable",
            reason="deadline_missed",
            files=(),
        )
        try:
            await publisher.publish(product, expected_revision=0, deadline=datetime.now(UTC) + HOUR)
            count += 1
        except catalog.RevisionConflict, catalog.StaleFence, catalog.Withdrawn:
            # The pipeline (or a withdrawal) got there first; its state wins.
            continue
    return count


async def finalize_job(context: JobContext) -> None:
    hour = datetime.fromisoformat(str(context.job.payload["hour"]))

    async def write(connection: AsyncConnection) -> None:
        await sweep_processing(connection, hour)

    await context.commit(write)


async def deadline_job(context: JobContext) -> None:
    hour = datetime.fromisoformat(str(context.job.payload["hour"]))
    missed: list[str] = []
    # Catalog first (idempotent: a segment that already has a video is skipped), then the
    # segment states, which then follow the committed videos.
    await publish_missed(context.engine, hour)

    async def write(connection: AsyncConnection) -> None:
        missed.extend(await sweep_deadline(connection, hour))

    await context.commit(write)
    for camera in missed:
        metrics.DEADLINES.labels(camera).inc()
        LOG.warning("Segment unpublished two hours after its boundary (%s)", camera)


def register(registry: Registry) -> None:
    registry.register(SCHEDULE_KIND, schedule_job)
    registry.register(FINALIZE_KIND, finalize_job)
    registry.register(DEADLINE_KIND, deadline_job)


# Pipeline segment access ---------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentRow:
    id: UUID
    state: str
    job: SegmentJob
    # The durable release point (#19) and the recipe whose blocks it describes.
    mezzanine_through: datetime | None = None
    mezzanine_recipe: str | None = None


async def active(engine: AsyncEngine, camera: str, now: datetime) -> list[SegmentRow]:
    """Current segments of a camera that have started and are not finished, oldest first.

    A segment made terminal by the deadline sweep (a placeholder, or a committed video whose
    acknowledgement was lost) is not finalized; it stays here for LATE_RENDER after its end, so
    the pipeline still publishes (superseding a placeholder, or a no-op duplicate) and records
    its RenderResult and `finalized_at`."""
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("""
            SELECT id,state,cycle_date,kind,revision,starts_at,ends_at,frames,mezzanine_through,
              mezzanine_recipe
            FROM weather.segment WHERE current AND camera_id=:camera AND starts_at <= :now
              AND (state IN ('scheduled','processing')
                   OR (finalized_at IS NULL AND ends_at > CAST(:late AS timestamptz)))
            ORDER BY starts_at
        """),
            {"camera": camera, "now": now, "late": now - LATE_RENDER},
        )
        return [
            SegmentRow(
                id=cast(UUID, row.id),
                state=str(row.state),
                job=SegmentJob(
                    camera=camera,
                    cycle_date=row.cycle_date,
                    kind=str(row.kind),
                    revision=int(row.revision),
                    starts_at=row.starts_at,
                    ends_at=row.ends_at,
                    frames=int(row.frames),
                ),
                mezzanine_through=row.mezzanine_through,
                mezzanine_recipe=row.mezzanine_recipe,
            )
            for row in rows
        ]


async def mark_processing(engine: AsyncEngine, segment: UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE weather.segment SET state='processing',updated_at=clock_timestamp()
            WHERE id=:id AND state='scheduled'
        """),
            {"id": segment},
        )


async def mark_through(
    engine: AsyncEngine, segment: UUID, through: datetime, recipe: str | None = None
) -> None:
    """No later block will read a frame captured before `through` (#19 may release those
    chunks' video duty). Only ever moves forward, and only for the recipe whose durable blocks
    it describes: a renderer of another recipe can never advance it."""
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE weather.segment SET mezzanine_through=:through,
              mezzanine_recipe=COALESCE(CAST(:recipe AS text),mezzanine_recipe),
              updated_at=clock_timestamp()
            WHERE id=:id AND (mezzanine_through IS NULL OR mezzanine_through < :through)
              AND (CAST(:recipe AS text) IS NULL OR mezzanine_recipe IS NULL
                   OR mezzanine_recipe = CAST(:recipe AS text))
        """),
            {"id": segment, "through": through, "recipe": recipe},
        )


async def mark_durable(engine: AsyncEngine, segment: UUID) -> None:
    """The whole mezzanine is fsynced on staging: #19 may now release its raw chunks."""
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE weather.segment SET mezzanine_durable_at=clock_timestamp(),
              updated_at=clock_timestamp()
            WHERE id=:id AND mezzanine_durable_at IS NULL
        """),
            {"id": segment},
        )


async def finish(
    engine: AsyncEngine,
    segment: UUID,
    result: dict[str, Any],
    *,
    outcome: tuple[str, str | None] | None = None,
) -> None:
    """Record the render result; `outcome` overrides its state (a withdrawn product)."""
    state, reason = outcome if outcome is not None else (result["state"], result["reason"])
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            UPDATE weather.segment SET state=:state,reason=:reason,slot_coverage=:coverage,
              render_result=CAST(:result AS jsonb),finalized_at=clock_timestamp(),
              updated_at=clock_timestamp()
            WHERE id=:id
        """),
            {
                "id": segment,
                "state": state,
                "reason": reason,
                "coverage": result["slots"]["fraction"],
                "result": json.dumps(result, sort_keys=True),
            },
        )
