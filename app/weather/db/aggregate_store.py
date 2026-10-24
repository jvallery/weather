# SPDX-License-Identifier: MIT
"""Append computed evidence and atomically advance its fenced caller's current head."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from weather.api.aggregate_adapter import Computation, public_coverage
from weather.api.cursors import watermark
from weather.api.public_common import utc_instant
from weather.api.public_models import Aggregate
from weather.products.aggregate import ID


@dataclass(frozen=True)
class StoredComputation:
    id: str
    scope: str
    parameters: Mapping[str, Any]


def prepare(
    station_alias: str,
    value: Computation,
    *,
    boundary: str,
    policy_revision: str,
    evaluated_at: datetime,
    policy_document: Mapping[str, Any],
) -> StoredComputation:
    if (
        not 1 <= len(station_alias) <= 64
        or any(ord(c) < 32 for c in station_alias)
        or not ID.fullmatch(value.aggregate_id)
        or not ID.fullmatch(policy_revision)
    ):
        raise ValueError("Invalid aggregate persistence identity")
    aggregate = Aggregate.model_validate(value.aggregate.model_dump())
    evaluated = utc_instant(evaluated_at)
    policy_json = json.dumps(
        dict(policy_document), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(policy_json.encode()) > 65536:
        raise ValueError("Aggregate policy snapshot exceeded persistence budget")
    interval = aggregate.interval.model_dump(mode="json")
    scope = hashlib.sha256(
        json.dumps(
            [station_alias, interval, aggregate.metric, aggregate.statistic],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    identity = (
        "aggregate_"
        + hashlib.sha256(
            json.dumps(
                [scope, policy_revision, value.aggregate_id],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    exact = value.exact.value
    projected = (
        None if exact is None else (exact.numerator if exact.denominator == 1 else float(exact))
    )
    if (
        aggregate.value != projected
        or aggregate.unit != value.exact.unit
        or aggregate.coverage != public_coverage(value.exact.coverage)
        or aggregate.evidence_ids != value.exact.evidence_ids
        or aggregate.evidence_revision != value.exact.evidence_revision
    ):
        raise ValueError("Aggregate projection disagrees with exact evidence")
    numerator = str(exact.numerator) if exact is not None else None
    denominator = str(exact.denominator) if exact is not None else None
    if numerator is not None and max(len(numerator), len(denominator or "")) > 4096:
        raise ValueError("Aggregate exact number exceeded persistence budget")
    # Details stay private to the worker; the public view reconstructs its typed scalar DTO.
    details: dict[str, Any] | None = None
    if value.details is not None:
        from weather.products.aggregate import Lightning, PressureTrend, Wind

        if isinstance(value.details, Lightning):
            details = {
                "boundary_uncertain_count": value.details.boundary_uncertain_count,
                "observed_subtotal": value.details.observed_subtotal,
                "authority": value.details.authority,
            }
        elif isinstance(value.details, Wind):
            details = {
                "direction_state": value.details.direction_state,
                "resultant": value.details.resultant,
                "rose": [
                    {
                        "category": row.category,
                        "duration_seconds": str(row.duration_seconds),
                        "denominator_seconds": str(row.denominator_seconds),
                        "angle_start": None if row.angle_start is None else str(row.angle_start),
                        "angle_end": None if row.angle_end is None else str(row.angle_end),
                        "speed_min": None if row.speed_min is None else str(row.speed_min),
                        "speed_max": None if row.speed_max is None else str(row.speed_max),
                    }
                    for row in value.details.rose
                ],
            }
        elif isinstance(value.details, PressureTrend):
            details = {
                "midpoint_separation_seconds": None
                if value.details.midpoint_separation_seconds is None
                else str(value.details.midpoint_separation_seconds)
            }
    parameters: dict[str, Any] = {
        "id": identity,
        "scope": scope,
        "station": station_alias,
        "start": aggregate.interval.start,
        "end": aggregate.interval.end,
        "metric": aggregate.metric,
        "statistic": aggregate.statistic,
        "policy": policy_revision,
        "watermark": int(watermark(boundary)),
        "evaluated": evaluated,
        "policy_document": policy_json,
        "document": aggregate.model_dump_json(),
        "numerator": numerator,
        "denominator": denominator,
        "details": json.dumps(details, allow_nan=False, separators=(",", ":"))
        if details is not None
        else None,
    }
    return StoredComputation(identity, scope, MappingProxyType(parameters))


async def persist(
    connection: AsyncConnection,
    value: StoredComputation,
) -> Literal["created", "unchanged", "superseded"]:
    """Only call inside JobContext.commit's fenced catalog transaction."""
    params = dict(value.parameters)
    await connection.execute(
        text("""INSERT INTO weather.aggregate_head(scope_key)
        VALUES(:scope) ON CONFLICT(scope_key) DO NOTHING"""),
        params,
    )
    head = (
        (
            await connection.execute(
                text("""SELECT revision_id,watermark
        FROM weather.aggregate_head WHERE scope_key=:scope FOR UPDATE"""),
                params,
            )
        )
        .mappings()
        .one()
    )
    if head["watermark"] > params["watermark"]:
        return "superseded"
    await connection.execute(
        text("""INSERT INTO weather.aggregate_revision
        (id,scope_key,station_alias,starts_at,ends_at,metric,statistic,policy_revision,watermark,
         evaluated_at,policy_document,
         document,exact_numerator,exact_denominator,details)
        VALUES(:id,:scope,:station,:start,:end,:metric,:statistic,:policy,:watermark,
               :evaluated,CAST(:policy_document AS jsonb),
               CAST(:document AS jsonb),:numerator,:denominator,CAST(:details AS jsonb))
        ON CONFLICT(id) DO NOTHING"""),
        params,
    )
    existing = (
        (
            await connection.execute(
                text("""SELECT scope_key,document,policy_revision,policy_document,
        exact_numerator,exact_denominator,details FROM weather.aggregate_revision WHERE id=:id"""),
                params,
            )
        )
        .mappings()
        .one()
    )
    if (
        existing["scope_key"] != value.scope
        or existing["document"] != json.loads(params["document"])
        or existing["policy_revision"] != params["policy"]
        or existing["policy_document"] != json.loads(params["policy_document"])
        or existing["exact_numerator"] != params["numerator"]
        or existing["exact_denominator"] != params["denominator"]
        or existing["details"] != (json.loads(params["details"]) if params["details"] else None)
    ):
        raise ValueError("Aggregate identity is already bound to different evidence")
    if head["revision_id"] == value.id:
        await connection.execute(
            text("""UPDATE weather.aggregate_head SET watermark=:watermark
            WHERE scope_key=:scope"""),
            params,
        )
        return "unchanged"
    await connection.execute(
        text("""UPDATE weather.aggregate_head SET revision_id=:id,
        revision=revision+1,watermark=:watermark WHERE scope_key=:scope"""),
        params,
    )
    return "created"
