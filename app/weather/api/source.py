# SPDX-License-Identifier: MIT
"""Read-only SQL over the public_api views: parameterized, bounded, never logging driver errors.

Identifiers in the SQL come only from the registry; request values are always bind parameters.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .registry import COLUMNS, IMPORT_CAP_SECONDS, REPORT_SECONDS, Family, Source

EPOCH_ORIGIN = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
# Longer than any ingest transaction (drain 30 s, reproject pages 60 s): every row inserted at or
# before the snapshot has committed by the time the first page reads, so the set never changes.
SNAPSHOT_MARGIN_SECONDS = 120
CALM_MPS = 0.5
SPEED_EDGES = (0.5, 2.0, 5.0, 10.0, 20.0)
Row = Mapping[str, Any]


class SourceUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Weather source read failed")


def _columns(family: Family) -> str:
    return ",".join(COLUMNS[family])


def latest_sql(family: Family) -> str:
    # Newest valid row through the btree on source_ts (ix_<family>_source_ts), never a scan.
    return f"""SELECT id,observed_at,received_at,invalid_fields,{_columns(family)}
FROM public_api.{family} WHERE valid AND observed_at <= :now
ORDER BY observed_at DESC LIMIT 1"""


# Rain: each obs_st amount covers the 60 s ending at its source time (#73), trimmed so reports
# never overlap. An amount belongs to the window containing the end of its support.
RAIN_SQL = f"""WITH r AS (
  SELECT id,observed_at AS ts,received_at,precip_accumulated AS v,
    lag(observed_at) OVER (ORDER BY observed_at,id) AS prev
  FROM public_api.obs_st
  WHERE observed_at > CAST(:lo AS timestamptz) - interval '{REPORT_SECONDS} seconds'
    AND observed_at <= :now
), s AS (
  SELECT ts,received_at,v,
    greatest(ts - interval '{REPORT_SECONDS} seconds',
             coalesce(prev, ts - interval '{REPORT_SECONDS} seconds')) AS s
  FROM r WHERE v IS NOT NULL
)
SELECT w.name,sum(s.v) AS total,
  coalesce(sum(extract(epoch FROM s.ts - greatest(s.s, w.ws))), 0)::float8 AS support_seconds,
  max(s.ts) AS observed_at,(array_agg(s.received_at ORDER BY s.ts DESC))[1] AS received_at
FROM (VALUES ('hour', CAST(:hour_start AS timestamptz)),
             ('day', CAST(:day_start AS timestamptz))) AS w(name, ws)
LEFT JOIN s ON s.ts > w.ws
GROUP BY w.name"""

STATION_SQL = "SELECT alias FROM public_api.station ORDER BY alias LIMIT 1"
# Station history starts at the first valid report; the source_ts btree answers it without a scan.
# Every device at the site counts, so a replaced station continues the same history (P20).
# Imported history (#167) starts earlier when there is one; each branch is one index probe.
HISTORY_SQL = """SELECT min(first) AS first FROM (
  (SELECT observed_at AS first FROM public_api.obs_st
   WHERE valid AND observed_at < :now ORDER BY observed_at LIMIT 1)
  UNION ALL
  (SELECT start AS first FROM public_api.imported_history WHERE start < :now)) h"""
# A year of day bins sorts ~400k samples: in memory it takes 4.6 s, spilled to disk 9.2 s (measured
# 2026-10-28). Bounded: at most two aggregate statements per api pod, each a few sorts.
AGGREGATE_WORK_MEM = "64MB"
# The imported range (#167): before the first live report, from another source's records.
IMPORTED_SQL = "SELECT source,start,last FROM public_api.imported_history"
SNAPSHOT_SQL = f"SELECT now() - interval '{SNAPSHOT_MARGIN_SECONDS} seconds' AS snapshot"


def observations_sql(family: Family) -> str:
    # Keyset on (observed_at, id); the explicit lower bound lets the btree skip earlier pages.
    # The snapshot is database insert time: late drain/reproject rows keep an old received_at.
    return f"""SELECT id,observed_at,received_at,valid,invalid_fields,{_columns(family)}
FROM public_api.{family}
WHERE observed_at >= :after_time AND observed_at < :end AND inserted_at <= :snapshot
  AND (observed_at, id) > (CAST(:after_time AS timestamptz), CAST(:after_id AS uuid))
ORDER BY observed_at, id LIMIT :rows"""


def _cells_sql(source: Source, *, day: bool, wind: bool, intervals: bool = False) -> str:
    value = "wind_avg" if wind else source.column
    direction = ",wind_direction AS d" if wind else ""
    carry = ",d" if wind else ""
    if source.support == "sample":
        # [ts, min(next, ts + cap)): an instantaneous reading held until the next one.
        # Imported readings (#167) carry their own cap; live rows use the registry's.
        cap = "ts + coalesce(make_interval(secs => cap_seconds), CAST(:cap AS interval))"
        start, end = "ts", f"least(coalesce(next_ts, {cap}), {cap})"
        keep = "ts < CAST(:end AS timestamptz)"
        home = "ts >= bin_start AND ts < bin_end"
        # A held reading is an observed value during every bin it overlaps.
        observed = "secs > 0 OR home"
    else:
        # (ts - 60 s, ts], trimmed at the previous report; the amount belongs to its end.
        span = "ts - CAST(:span AS interval)"
        start = f"greatest({span}, coalesce(prev_ts, {span}))"
        end = "ts"
        # Every report whose support overlaps the query, including one ending after it.
        keep = f"ts > CAST(:start AS timestamptz) AND {span} < CAST(:end AS timestamptz)"
        home = "ts > bin_start AND ts <= bin_end"
        observed = "home"
    last = "greatest(sup.e - interval '1 microsecond', sup.s)"
    if intervals:
        # Arbitrary, possibly overlapping intervals (search predicates over whole videos): the
        # same membership as the bounds below, b.lo <= last support instant and b.hi > start.
        bins = f"""SELECT b.lo, b.hi
    FROM unnest(CAST(:los AS timestamptz[]), CAST(:his AS timestamptz[])) AS b(lo, hi)
    WHERE b.lo <= {last} AND b.hi > sup.s"""
    elif day:
        bounds = "CAST(:bounds AS timestamptz[])"
        bins = f"""SELECT ({bounds})[k] AS lo, ({bounds})[k + 1] AS hi
    FROM generate_series(width_bucket(sup.s, {bounds}), width_bucket({last}, {bounds})) AS k
    WHERE k BETWEEN 1 AND cardinality({bounds}) - 1"""
    else:
        stride = "CAST(:stride AS interval)"
        bins = f"""SELECT b AS lo, b + {stride} AS hi
    FROM generate_series(date_bin({stride}, sup.s, CAST(:origin AS timestamptz)),
                         date_bin({stride}, {last}, CAST(:origin AS timestamptz)), {stride}) AS b"""
    return f"""WITH raw AS (
  SELECT id,observed_at AS ts,{value} AS v{direction},cap_seconds
  FROM public_api.{source.family}_history
  WHERE observed_at >= CAST(:fetch_start AS timestamptz)
    AND observed_at < CAST(:fetch_end AS timestamptz)
    -- An imported row lacking this field neither supports nor interrupts it (#167): the source
    -- reported fields on different rows. Live rows keep their existing semantics.
    AND ({value} IS NOT NULL OR provenance = 'live')
), seq AS (
  SELECT raw.*,lag(ts) OVER w AS prev_ts,lead(ts) OVER w AS next_ts
  FROM raw WINDOW w AS (ORDER BY ts,id)
), sup AS (
  SELECT id,ts,v{carry},{start} AS s,{end} AS e FROM seq WHERE v IS NOT NULL AND {keep}
), cells AS (
  SELECT sup.*,greatest(bin.lo, CAST(:start AS timestamptz)) AS bin_start,
    least(bin.hi, CAST(:end AS timestamptz)) AS bin_end
  FROM sup CROSS JOIN LATERAL ({bins}) AS bin
), clipped AS (
  SELECT *,greatest(0, extract(epoch FROM least(e, bin_end) - greatest(s, bin_start)))::float8
    AS secs,({home}) AS home
  FROM cells WHERE bin_end > bin_start
), used AS (SELECT *,({observed}) AS observed FROM clipped WHERE secs > 0 OR home)
"""


def aggregate_sql(source: Source, *, day: bool, wind: bool, intervals: bool = False) -> str:
    directional = f"v >= {CALM_MPS} AND d IS NOT NULL"
    wind_columns = (
        f""",sum(secs) FILTER (WHERE v < {CALM_MPS}) AS calm_seconds,
  sum(secs) FILTER (WHERE {directional}) AS directional_seconds,
  sum(v * secs * sin(radians(d))) FILTER (WHERE {directional}) AS east,
  sum(v * secs * cos(radians(d))) FILTER (WHERE {directional}) AS north,
  sum(v * secs) FILTER (WHERE {directional}) AS weight"""
        if wind
        else ""
    )
    return (
        _cells_sql(source, day=day, wind=wind, intervals=intervals)
        + f"""SELECT bin_start,bin_end,sum(secs) AS support_seconds,
  sum(v * secs) / nullif(sum(secs), 0) AS mean,
  min(v) FILTER (WHERE observed) AS minimum,max(v) FILTER (WHERE observed) AS maximum,
  sum(v) FILTER (WHERE home) AS total,count(*) AS evidence_count,
  encode(sha256(convert_to(string_agg(id::text, ',' ORDER BY ts, id), 'UTF8')), 'hex')
    AS evidence_hash{wind_columns}
FROM used GROUP BY bin_start,bin_end ORDER BY bin_start"""
    )


def rose_sql(source: Source, *, day: bool) -> str:
    edges = ",".join(str(edge) for edge in SPEED_EDGES)
    return (
        _cells_sql(source, day=day, wind=True)
        + f"""SELECT bin_start,
  floor((d + 11.25 - 360 * floor((d + 11.25) / 360)) / 22.5)::int AS sector,
  width_bucket(v, ARRAY[{edges}]::float8[]) AS speed_bin,sum(secs) AS seconds
FROM used WHERE v >= {CALM_MPS} AND d IS NOT NULL AND secs > 0
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    )


@dataclass(frozen=True)
class Reading:
    """One consistent read of everything current conditions need."""

    station_alias: str | None
    obs_st: Row | None
    rapid_wind: Row | None
    rain: Mapping[str, Row]
    # The instant the rain windows were computed for: [at - 1 h, at] and [day start, at].
    at: datetime


@dataclass(frozen=True)
class BinSpec:
    day: bool
    start: datetime
    end: datetime
    stride: timedelta | None = None
    bounds: Sequence[datetime] = ()


class WeatherReader:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        statement_ms: int = 1500,
        aggregate_ms: int = 10000,
        aggregate_slots: int = 2,
    ) -> None:
        self._engine = engine
        self._statement_ms, self._aggregate_ms = statement_ms, aggregate_ms
        # Waiting, not fail-fast: excess aggregate requests queue behind the pool budget.
        self._aggregate_slots = asyncio.Semaphore(aggregate_slots)

    async def _run(
        self,
        statements: Sequence[tuple[str, dict[str, Any]]],
        timeout_ms: int,
        work_mem: str | None = None,
    ) -> list[list[Row]]:
        try:
            # The whole read, including waiting for a pooled connection, has one deadline.
            async with (
                asyncio.timeout(timeout_ms / 1000 + 2),
                self._engine.connect() as connection,
                connection.begin(),
            ):
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                if work_mem:
                    await connection.execute(text(f"SET LOCAL work_mem = '{work_mem}'"))
                results: list[list[Row]] = []
                for sql, parameters in statements:
                    result = await connection.execute(text(sql), parameters)
                    results.append([dict(row) for row in result.mappings().all()])
                return results
        except Exception:
            # Never retain or log driver errors, SQL or parameters.
            raise SourceUnavailable() from None

    async def ping(self) -> bool:
        try:
            await self._run([("SELECT 1", {})], self._statement_ms)
            return True
        except SourceUnavailable:
            return False

    async def current(self, now: datetime, hour_start: datetime, day_start: datetime) -> Reading:
        station, st, rw, rain = await self._run(
            [
                (STATION_SQL, {}),
                (latest_sql("obs_st"), {"now": now}),
                (latest_sql("rapid_wind"), {"now": now}),
                (
                    RAIN_SQL,
                    {
                        "lo": min(hour_start, day_start),
                        "now": now,
                        "hour_start": hour_start,
                        "day_start": day_start,
                    },
                ),
            ],
            self._statement_ms,
        )
        return Reading(
            station_alias=station[0]["alias"] if station else None,
            obs_st=st[0] if st else None,
            rapid_wind=rw[0] if rw else None,
            rain={row["name"]: row for row in rain},
            at=now,
        )

    async def history_start(self, now: datetime) -> datetime | None:
        rows = (await self._run([(HISTORY_SQL, {"now": now})], self._statement_ms))[0]
        return rows[0]["first"] if rows else None

    async def imported(self) -> Row | None:
        """The imported history's first and last reading and its source, or None."""
        rows = (await self._run([(IMPORTED_SQL, {})], self._statement_ms))[0]
        return rows[0] if rows and rows[0]["start"] is not None else None

    async def observations(
        self,
        family: Family,
        *,
        end: datetime,
        snapshot: datetime | None,
        after_time: datetime,
        after_id: UUID,
        rows: int,
    ) -> tuple[list[Row], datetime]:
        """One page and the insert-time snapshot it used (taken from the DB on the first page)."""
        if snapshot is None:
            ((row,),) = await self._run([(SNAPSHOT_SQL, {})], self._statement_ms)
            snapshot = row["snapshot"]
        (result,) = await self._run(
            [
                (
                    observations_sql(family),
                    {
                        "end": end,
                        "snapshot": snapshot,
                        "after_time": after_time,
                        "after_id": after_id,
                        "rows": rows,
                    },
                )
            ],
            self._statement_ms,
        )
        return result, snapshot

    async def events(self, sql: str, parameters: dict[str, Any]) -> list[Row]:
        """One event page, read in a single statement (its snapshot is in each row)."""
        (rows,) = await self._run([(sql, parameters)], self._statement_ms)
        return rows

    async def segments(self, start: datetime, end: datetime) -> list[Row]:
        """Playable catalog videos overlapping [start, end), for event video links."""
        from .events import SEGMENTS_SQL

        (rows,) = await self._run(
            [(SEGMENTS_SQL, {"start": start, "end": end})], self._statement_ms
        )
        return rows

    async def aggregates(
        self, source: Source, bins: BinSpec, *, cap: timedelta, wind: bool, rose: bool
    ) -> tuple[list[Row], list[Row]]:
        reach = max(cap, timedelta(seconds=REPORT_SECONDS), timedelta(seconds=IMPORT_CAP_SECONDS))
        parameters: dict[str, Any] = {
            "start": bins.start,
            "end": bins.end,
            "fetch_start": bins.start - reach,
            "fetch_end": bins.end + reach,
        }
        if source.support == "sample":
            parameters["cap"] = cap
        else:
            parameters["span"] = timedelta(seconds=REPORT_SECONDS)
        if bins.day:
            parameters["bounds"] = list(bins.bounds)
        else:
            parameters["stride"] = bins.stride
            parameters["origin"] = EPOCH_ORIGIN
        statements = [(aggregate_sql(source, day=bins.day, wind=wind), parameters)]
        if rose:
            statements.append((rose_sql(source, day=bins.day), parameters))
        try:
            async with asyncio.timeout(self._aggregate_ms / 1000):
                await self._aggregate_slots.acquire()
        except TimeoutError:
            raise SourceUnavailable() from None
        try:
            results = await self._run(statements, self._aggregate_ms, AGGREGATE_WORK_MEM)
        finally:
            self._aggregate_slots.release()
        return results[0], results[1] if rose else []
