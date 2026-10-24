# SPDX-License-Identifier: MIT
"""Scheduler job deriving measured events from canonical telemetry (#41).

The tick enqueues one ``events`` job per 5-minute slot (a fixed idempotency key and deadline, so
repeated ticks are no-ops). Each job reconciles the 48 hours before its slot end, which absorbs
late spool drains, and every local day that closed in that window. Retrying a job with the same
inputs writes nothing. Inference is never involved.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.db import jobs
from weather.scheduler.registry import JobContext, Registry
from weather.timemodel import day_for, weather_day

from . import rules
from .rules import Authority, Candidate, Observation
from .store import TOPIC, current_states, decide, settle

KIND = "events"
CHANGED_KIND = "weather_event_changed"
CADENCE = timedelta(minutes=5)
LOOKBACK = timedelta(hours=48)
# A closed day's gust maximum waits this long for ordinary late input before it is derived.
GRACE = timedelta(minutes=15)
DEADLINE = timedelta(minutes=15)
# Keys settled per job. The job's write transaction holds its own row, so its heartbeat cannot
# extend the 60 s lease meanwhile; a backlog (a storm's strikes, a backfill) continues in the
# next slot, where already-settled keys are no-ops.
MAX_SETTLES = 1000
WRITES = Counter(
    "weather_events_revisions_total", "Event revisions written by the measured rules", ["action"]
)
ROW_FAMILIES = {
    "precipitation_onset": "evt_precip",
    "lightning_strike": "evt_strike",
    "lightning_interval": "obs_st",
}
ROWS_SQL = """SELECT o.id,d.station_id AS station,o.source_ts,o."values",o.quality
FROM weather.{table} o JOIN weather.device d ON d.id=o.device_id WHERE {where}"""
# Only reports that claim lightning are lightning-interval evidence.
LIGHTNING = (
    "CASE WHEN jsonb_typeof(o.\"values\"->'lightning_strike_count')='number'"
    " THEN (o.\"values\"->>'lightning_strike_count')::numeric > 0 ELSE false END"
)


def _observation(row: Mapping[str, Any]) -> Observation:
    return Observation(
        id=cast(UUID, row["id"]),
        station=cast(UUID, row["station"]),
        source_ts=cast(datetime, row["source_ts"]),
        values=cast(dict[str, Any], row["values"]),
        quality=cast(dict[str, Any], row["quality"]),
    )


async def _rows(
    connection: AsyncConnection, table: str, where: str, **parameters: Any
) -> list[Observation]:
    result = await connection.execute(text(ROWS_SQL.format(table=table, where=where)), parameters)
    return [_observation(dict(row)) for row in result.mappings()]


def closed_days(start: datetime, end: datetime) -> list[date]:
    """Local days whose gust maximum is due: closed (plus grace) by ``end``, within the window."""
    days = []
    local = day_for(start - GRACE).local_date
    while (day := weather_day(local)).end + GRACE <= end:
        if day.end + GRACE > start:
            days.append(local)
        local += timedelta(days=1)
    return days


class Rules:
    """Re-reads evidence for one key; called only while that key's lock is held."""

    def __init__(self, authority: Authority, end: datetime) -> None:
        self.authority, self.end = authority, end

    def deriver(self, key: str) -> Any:
        family, _, rest = key.partition(":")

        async def derive(connection: AsyncConnection) -> Candidate | None:
            if family in ROW_FAMILIES:
                rows = await _rows(connection, ROW_FAMILIES[family], "o.id=:id", id=UUID(rest))
                return rules.derive(family, rows[0], self.authority) if rows else None
            station, _, local = rest.partition(":")
            day = weather_day(date.fromisoformat(local))
            if day.end + GRACE > self.end:
                return None
            rows = await _rows(
                connection,
                "obs_st",
                "d.station_id=:station AND o.source_ts > :start AND o.source_ts <= :end",
                station=UUID(station),
                start=day.start,
                end=day.end,
            )
            return rules.daily_gust(day, UUID(station), rows)

        return derive


async def reconcile(
    connection: AsyncConnection,
    *,
    end: datetime,
    authority: Authority = rules.LIGHTNING_AUTHORITY,
    max_settles: int = MAX_SETTLES,
) -> dict[str, int]:
    """Bring measured events for [end - LOOKBACK, end) and its closed days up to date."""
    start = end - LOOKBACK
    prepared: dict[str, Candidate | None] = {}
    window = "o.source_ts >= :start AND o.source_ts < :end"
    for family, table in ROW_FAMILIES.items():
        where = window + (" AND " + LIGHTNING if family == "lightning_interval" else "")
        for row in await _rows(connection, table, where, start=start, end=end):
            prepared[f"{family}:{row.id}"] = rules.derive(family, row, authority)
    days = closed_days(start, end)
    for local in days:
        day = weather_day(local)
        rows = await _rows(
            connection,
            "obs_st",
            "o.source_ts > :start AND o.source_ts <= :end",
            start=day.start,
            end=day.end,
        )
        for station in {row.station for row in rows}:
            prepared[rules.gust_key(station, local)] = rules.daily_gust(day, station, rows)
    # Existing events in the window that no longer have a candidate get re-derived (withdrawn).
    keys = set(prepared) | set(await _existing(connection, start, end, days))
    states = await current_states(connection, sorted(keys))
    counts = {"created": 0, "revised": 0, "withdrawn": 0, "unchanged": 0, "deferred": 0}
    derive = Rules(authority, end)
    settled = 0
    for key in sorted(keys):
        if key in prepared and decide(prepared[key], states.get(key)) is None:
            counts["unchanged"] += 1
            continue
        if settled >= max_settles:
            counts["deferred"] += 1
            continue
        settled += 1
        action = await settle(
            connection,
            key,
            derive.deriver(key),
            family=key.partition(":")[0],
            author_kind="rule",
            now=end,
        )
        counts[action] += 1
        if action != "unchanged":
            WRITES.labels(action).inc()
    return counts


async def _existing(
    connection: AsyncConnection, start: datetime, end: datetime, days: Iterable[date]
) -> list[str]:
    # The anchor is the evidence's source time: the start of a one-second event, the end of a
    # report-interval event. Gust events are found by their day.
    result = await connection.execute(
        text("""
        SELECT e.logical_key FROM weather.weather_event e
        JOIN weather.weather_event_revision r ON r.event_id=e.id AND r.revision=e.revision
        WHERE r.visibility <> 'withdrawn' AND (
          (e.rule_family IN ('precipitation_onset','lightning_strike')
            AND r.starts_at >= :start AND r.starts_at < :end)
          OR (e.rule_family = 'lightning_interval' AND r.ends_at >= :start AND r.ends_at < :end)
          OR (e.rule_family = 'daily_gust_max'
            AND r.measurement->>'day' = ANY(CAST(:days AS text[]))))
    """),
        {"start": start, "end": end, "days": [day.isoformat() for day in days]},
    )
    return [str(key) for key in result.scalars()]


async def events(context: JobContext) -> dict[str, int]:
    end = datetime.fromisoformat(str(context.job.payload["end"]))
    counts: dict[str, int] = {}

    async def write(connection: AsyncConnection) -> None:
        counts.update(await reconcile(connection, end=end))

    await context.commit(write, result=counts)
    return counts


async def changed(context: JobContext) -> None:
    """Routes ``weather_event.changed`` so the outbox never quarantines it; search (#25)
    replaces this with indexing. Completion is the whole effect."""
    await context.commit()


async def enqueue(connection: AsyncConnection, now: datetime) -> None:
    epoch = int(now.timestamp())
    slot = datetime.fromtimestamp(epoch - epoch % int(CADENCE.total_seconds()), UTC)
    end = slot + CADENCE
    await jobs.enqueue(
        connection,
        kind=KIND,
        key=f"events:{slot.strftime('%Y-%m-%dT%H:%MZ')}",
        payload={"end": end.isoformat()},
        not_before=end,
        deadline=end + DEADLINE,
    )


def register(registry: Registry) -> None:
    registry.register(KIND, events)
    registry.register(CHANGED_KIND, changed, topics=(TOPIC,))
