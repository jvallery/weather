# SPDX-License-Identifier: MIT
"""Idempotent America/Denver calendar-day rows, bounded by the pinned Python time model."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.timemodel import weather_day

MAX_DAYS = 366


async def ensure_days(connection: AsyncConnection, start: date, end: date) -> int:
    """Insert missing days in the inclusive range; existing rows never change."""
    count = (end - start).days + 1
    if not 1 <= count <= MAX_DAYS:
        raise ValueError("Invalid day range")
    days = [weather_day(start + timedelta(days=offset)) for offset in range(count)]
    result = await connection.execute(
        text("""
        INSERT INTO weather.weather_day(local_date,starts_at,ends_at)
        SELECT * FROM unnest(CAST(:dates AS date[]),CAST(:starts AS timestamptz[]),
                             CAST(:ends AS timestamptz[]))
        ON CONFLICT (local_date) DO NOTHING
    """),
        {
            "dates": [day.local_date for day in days],
            "starts": [day.start for day in days],
            "ends": [day.end for day in days],
        },
    )
    return result.rowcount
