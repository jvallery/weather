# SPDX-License-Identifier: MIT
"""Source-owned job handlers and the periodic work the scheduler tick enqueues."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.db.days import ensure_days
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
    """Routes publication events so they never quarantine. Consumers (search #25) extend it."""


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


def register(registry: Registry) -> None:
    registry.register(DAYS_KIND, days)
    registry.register(MEDIA_KIND, media_changed, topics=("media.published", "media.withdrawn"))
