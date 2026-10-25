# SPDX-License-Identifier: MIT
"""Source-owned job handlers and the periodic work the scheduler tick enqueues."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from weather.ai import store as analysis
from weather.db import jobs
from weather.db.days import ensure_days
from weather.events import job as events
from weather.pipeline import schedule as segments
from weather.scheduler.registry import JobContext, Registry
from weather.timemodel import day_for, weather_day

DAYS_KIND = "days"
DAYS_AHEAD = 7
MEDIA_KIND = "media.changed"


async def days(context: JobContext) -> None:
    """Keep weather_day populated ahead of time; existing days never change."""
    start = date.fromisoformat(str(context.job.payload["start"]))
    end = date.fromisoformat(str(context.job.payload["end"]))

    async def write(connection: AsyncConnection) -> None:
        await ensure_days(connection, start, end)

    await context.commit(write)


async def media_changed(context: JobContext) -> None:
    """Publication events: a committed video queues its AI description (#23); a withdrawn video
    loses its search documents and visual events in the same transaction."""
    payload = context.job.payload
    now = datetime.now(UTC)

    async def write(connection: AsyncConnection) -> None:
        if payload.get("product") == "video" and "publication_id" in payload:
            await analysis.on_published(connection, UUID(str(payload["publication_id"])), now)
        elif payload.get("target") == "video" and "id" in payload:
            await analysis.on_withdrawn(connection, UUID(str(payload["id"])), now)

    await context.commit(write)


async def periodic(connection: AsyncConnection, now: datetime) -> None:
    """Idempotent: one deterministic key per local day, bound to that day's deadline."""
    today = day_for(now).local_date
    await jobs.enqueue(
        connection,
        kind=DAYS_KIND,
        key=f"days:{today.isoformat()}",
        payload={
            "start": today.isoformat(),
            "end": (today + timedelta(days=DAYS_AHEAD)).isoformat(),
        },
        not_before=now.astimezone(UTC),
        deadline=weather_day(today).end,
    )
    await events.enqueue(connection, now)
    # Solar schedule (>=3 local days ahead) and segment boundary jobs (#15).
    await segments.periodic(connection, now)


def register(registry: Registry) -> None:
    registry.register(DAYS_KIND, days)
    registry.register(MEDIA_KIND, media_changed, topics=("media.published", "media.withdrawn"))
    events.register(registry)
    segments.register(registry)
