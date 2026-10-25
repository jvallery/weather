# SPDX-License-Identifier: MIT
"""Idempotent America/Denver calendar-day and hour-slot rows, bounded by the pinned Python
time model."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.timemodel import hour_slots, weather_day

MAX_DAYS = 366


async def ensure_days(connection: AsyncConnection, start: date, end: date) -> int:
    """Insert missing days, and their 23, 24 or 25 UTC hour slots (#38), in the inclusive
    range; existing rows never change. Returns the number of days inserted."""
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
    slots = [slot for day in days for slot in hour_slots(day.local_date)]
    await connection.execute(
        text("""
        INSERT INTO weather.hour_slot(starts_at,local_date,utc_offset_minutes,fold)
        SELECT * FROM unnest(CAST(:starts AS timestamptz[]),CAST(:dates AS date[]),
                             CAST(:offsets AS smallint[]),CAST(:folds AS smallint[]))
        ON CONFLICT (starts_at) DO NOTHING
    """),
        {
            "starts": [slot.start for slot in slots],
            "dates": [slot.weather_day.local_date for slot in slots],
            "offsets": [slot.utc_offset_minutes for slot in slots],
            "folds": [slot.fold for slot in slots],
        },
    )
    return result.rowcount
