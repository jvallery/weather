# SPDX-License-Identifier: MIT
"""Read-only SQL source for current DTOs and stable flattened observation pages."""

import base64
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.api.cursors import Snapshot, watermark
from weather.api.metric_registry import REGISTRY, PublicInputError, validate_selection
from weather.api.public_common import utc_instant
from weather.api.public_models import (
    AggregateQuery,
    Aggregates,
    History,
    Interval,
    ObservationQuery,
    enforce_raw_window,
)
from weather.api.public_service import CurrentReading, SourcePage

from .public_connection import DEFAULT_BUDGET, PublicDatabaseBudget
from .public_mapping import FreshnessPolicy, current_dto, history_point, instant

CURRENT_SQL = text("""WITH latest AS MATERIALIZED (
  SELECT max(o.observed_at) AS newest_valid_obs_st_at
  FROM public_api.telemetry_publications o
  WHERE o.station_alias=:station AND o.message_type='obs_st'
    AND o.observed_at<=:now AND o.received_at<=:now
    AND o.quality->'valid'='true'::jsonb
    AND EXISTS(SELECT 1 FROM unnest(CAST(:obs_st_fields AS text[])) AS f(field)
               WHERE jsonb_typeof(o."values"->f.field)='number')
)
SELECT c.station_alias,c.metric,c.unit,c.observed_at,c.received_at,
  c.value::text AS value_text,jsonb_typeof(c.value) AS value_kind,
  c.field_quality,c.quality,latest.newest_valid_obs_st_at
FROM latest LEFT JOIN public_api.metric_current c ON c.station_alias=:station
ORDER BY c.metric COLLATE "C"
""")

OBSERVATIONS_SQL = text("""WITH boundary AS MATERIALIZED (
  SELECT coalesce(CAST(:watermark AS bigint),
                  public_api.telemetry_snapshot_watermark()) AS watermark
), bindings AS MATERIALIZED (
  SELECT * FROM jsonb_to_recordset(CAST(:bindings AS jsonb))
    AS b(metric text,family text,field text,unit text)
), page AS MATERIALIZED (
  SELECT p.id,p.message_type,p.observed_at,p.received_at,b.metric,b.unit,
    p."values"->>b.field AS value_text,jsonb_typeof(p."values"->b.field) AS value_kind,
    p.field_quality->b.field AS field_quality,p.quality
  FROM public_api.telemetry_publications p JOIN bindings b ON b.family=p.message_type
  CROSS JOIN boundary
  WHERE p.publication_seq<=boundary.watermark AND p.station_alias=:station
    AND p.observed_at>=:start AND p.observed_at<:end
    AND (CAST(:after_time AS timestamptz) IS NULL OR
         (p.observed_at,p.message_type COLLATE "C",p.id,b.metric COLLATE "C")>
         (CAST(:after_time AS timestamptz),CAST(:after_family AS text) COLLATE "C",
          CAST(:after_id AS uuid),CAST(:after_metric AS text) COLLATE "C"))
  ORDER BY p.observed_at,p.message_type COLLATE "C",p.id,b.metric COLLATE "C"
  LIMIT :fetch_limit
)
SELECT boundary.watermark,page.* FROM boundary LEFT JOIN page ON true
ORDER BY page.observed_at,page.message_type COLLATE "C",page.id,page.metric COLLATE "C"
""")


class PublicReadUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Weather source read is unavailable")


AggregateReader = Callable[[AggregateQuery, Snapshot | None], Awaitable[SourcePage[Aggregates]]]


def _invalid_position() -> PublicInputError:
    return PublicInputError("invalid_cursor", "Invalid export source position.")


def encode_position(row: Mapping[str, Any]) -> str:
    value = [
        instant(row["observed_at"]).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        str(row["message_type"]),
        str(row["id"]),
        str(row["metric"]),
    ]
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
    token = "p1_" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(token) > 512:
        raise PublicReadUnavailable()
    return token


def decode_position(value: str, query: ObservationQuery) -> tuple[datetime, str, UUID, str]:
    try:
        if len(value) > 512 or re.fullmatch(r"p1_[A-Za-z0-9_-]+", value) is None:
            raise ValueError("position shape")
        encoded = value[3:]
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) != 4 or any(type(v) is not str for v in data):
            raise ValueError("position fields")
        at, family, identity, metric = utc_instant(data[0]), data[1], UUID(data[2]), data[3]
        binding = REGISTRY[metric].source
        if (
            metric not in query.metrics
            or binding is None
            or family not in binding.families
            or not query.start <= at < query.end
            or encode_position(
                {"observed_at": at, "message_type": family, "id": identity, "metric": metric}
            )
            != value
        ):
            raise ValueError("position binding")
        return at, family, identity, metric
    except ValueError, TypeError, KeyError, UnicodeError:
        raise _invalid_position() from None


def observation_parameters(
    query: ObservationQuery,
    snapshot: Snapshot | None,
    station_alias: str,
) -> dict[str, Any]:
    validate_selection(query.metrics)
    if query.cursor is not None:
        raise _invalid_position()
    bindings = []
    for metric in query.metrics:
        entry = REGISTRY[metric]
        if entry.source is None:
            raise PublicInputError(
                "unsupported_statistic",
                "Selected metric has no direct source observations; request an aggregate.",
            )
        for family in entry.source.families:
            bindings.append(
                {
                    "metric": metric,
                    "family": family,
                    "field": entry.source.field,
                    "unit": entry.definition.canonical_unit,
                }
            )
    after = (
        decode_position(snapshot.position, query)
        if snapshot is not None
        else (
            None,
            "",
            UUID(int=0),
            "",
        )
    )
    return {
        "station": station_alias,
        "start": query.start,
        "end": query.end,
        "watermark": int(watermark(snapshot.watermark)) if snapshot is not None else None,
        "bindings": json.dumps(bindings, separators=(",", ":")),
        "after_time": after[0],
        "after_family": after[1],
        "after_id": after[2],
        "after_metric": after[3],
        "fetch_limit": query.limit + 1,
    }


class PublicSqlSource:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        station_alias: str,
        maximum_raw_seconds: int,
        policies: Mapping[str, FreshnessPolicy] | None = None,
        budget: PublicDatabaseBudget = DEFAULT_BUDGET,
        aggregate_source: AggregateReader | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not 1 <= len(station_alias) <= 64
            or any(ord(c) < 32 for c in station_alias)
            or type(maximum_raw_seconds) is not int
            or maximum_raw_seconds <= 0
            or any(metric not in REGISTRY for metric in (policies or {}))
            or any(not isinstance(policy, FreshnessPolicy) for policy in (policies or {}).values())
        ):
            raise ValueError("Invalid public source configuration")
        self._engine, self._budget, self._clock = engine, budget, clock
        self._station, self._raw_seconds = station_alias, maximum_raw_seconds
        self._policies = MappingProxyType(dict(policies or {}))
        self._aggregate_source = aggregate_source

    async def _rows(self, statement: Any, parameters: dict[str, Any]) -> list[Mapping[str, Any]]:
        try:
            async with self._engine.connect() as connection, connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(
                    text("SELECT set_config('statement_timeout',:timeout,true)"),
                    {"timeout": str(self._budget.statement_timeout_ms) + "ms"},
                )
                rows = (await connection.execute(statement, parameters)).mappings().all()
                return [dict(row) for row in rows]
        except Exception:
            # Do not retain/log driver exceptions, SQL parameters, URLs or private text.
            raise PublicReadUnavailable() from None

    async def current(self) -> CurrentReading:
        now = instant(self._clock())
        fields = sorted(
            {
                entry.source.field
                for entry in REGISTRY.values()
                if entry.source is not None and "obs_st" in entry.source.families
            }
        )
        try:
            rows = await self._rows(
                CURRENT_SQL,
                {
                    "station": self._station,
                    "now": now,
                    "obs_st_fields": fields,
                },
            )
            if not rows:
                raise PublicReadUnavailable()
            newest = rows[0]["newest_valid_obs_st_at"]
            now = instant(self._clock())
            return CurrentReading(
                current=current_dto(rows, self._station, self._policies, now),
                newest_valid_obs_st_at=instant(newest)
                if newest is not None and instant(newest) <= now
                else None,
            )
        except Exception:
            raise PublicReadUnavailable() from None

    async def observations(
        self,
        query: ObservationQuery,
        snapshot: Snapshot | None,
    ) -> SourcePage[History]:
        enforce_raw_window(query, maximum_seconds=self._raw_seconds)
        parameters = observation_parameters(query, snapshot, self._station)
        now = instant(self._clock())
        try:
            rows = await self._rows(OBSERVATIONS_SQL, parameters)
            if not rows:
                raise PublicReadUnavailable()
            bound = watermark(str(rows[0]["watermark"]))
            if snapshot is not None and bound != snapshot.watermark:
                raise PublicReadUnavailable()
            now = instant(self._clock())
            members = [row for row in rows if row["id"] is not None]
            returned = members[: query.limit]
            next_position = encode_position(returned[-1]) if len(members) > query.limit else None
            return SourcePage(
                data=History(
                    generated_at=now,
                    interval=Interval(start=query.start, end=query.end),
                    resolution_seconds=0,
                    points=tuple(history_point(row, now) for row in returned),
                    gaps=(),
                    next_cursor=None,
                ),
                watermark=bound,
                next_position=next_position,
            )
        except Exception:
            raise PublicReadUnavailable() from None

    async def aggregates(
        self,
        query: AggregateQuery,
        snapshot: Snapshot | None,
    ) -> SourcePage[Aggregates]:
        if self._aggregate_source is not None:
            return await self._aggregate_source(query, snapshot)
        raise PublicReadUnavailable()

    async def aclose(self) -> None:
        await self._engine.dispose()
