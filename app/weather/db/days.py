# SPDX-License-Identifier: MIT
"""Idempotent inclusive America/Denver calendar-day/hour-slot initialization."""

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_days(
    connection: AsyncConnection, start: date, end: date, *, site: str = "local"
) -> None:
    if end < start or not 1 <= len(site) <= 64:
        raise ValueError("Invalid day range or site")
    await connection.execute(
        text("SELECT weather.ensure_days(:start,:end,:site)"),
        {"start": start, "end": end, "site": site},
    )
