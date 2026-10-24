# SPDX-License-Identifier: MIT
"""Bounded complete-window evidence, separate from public raw page subsets."""

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from weather.api.cursors import watermark
from weather.api.metric_registry import PublicInputError
from weather.api.public_common import utc_instant
from weather.api.public_models import HistoryPoint, Interval, ObservationQuery, enforce_raw_window

from .public_mapping import history_point, instant, qualified_reported_support
from .public_source import PublicReadUnavailable, PublicSqlSource, observation_parameters


@dataclass(frozen=True)
class SupportEvidence:
    point: HistoryPoint
    exact_value: Decimal | None
    quality_revision: str
    observation_id: UUID
    family: str
    device_alias: str
    publication_seq: int
    source_time_eligible: bool
    support_kind: str
    support_policy: str
    support_qualified: bool
    reported_seconds: int | float | None


@dataclass(frozen=True)
class EvidenceWindow:
    watermark: str
    rows: tuple[SupportEvidence, ...]


EVIDENCE_SQL = text("""WITH boundary AS MATERIALIZED (
  SELECT coalesce(CAST(:watermark AS bigint),
                  public_api.telemetry_snapshot_watermark()) AS watermark
), bindings AS MATERIALIZED (
  SELECT * FROM jsonb_to_recordset(CAST(:bindings AS jsonb))
    AS b(metric text,family text,field text,unit text)
), revisions AS MATERIALIZED (
  SELECT DISTINCT ON (r.id,r.message_type COLLATE "C") r.*
  FROM public_api.telemetry_evidence_revisions r CROSS JOIN boundary
  WHERE r.revision_seq<=boundary.watermark AND r.publication_seq<=boundary.watermark
    AND r.observed_at>=:lower AND r.observed_at<=:upper
    AND r.message_type IN (SELECT family FROM bindings)
  ORDER BY r.id,r.message_type COLLATE "C",r.revision_seq DESC
), raw AS MATERIALIZED (
  SELECT p.id,p.device_alias,p.message_type,p.observed_at,p.received_at,
    p.publication_seq,b.metric,b.unit,
    p."values"->>b.field AS value_text,jsonb_typeof(p."values"->b.field) AS value_kind,
    p.field_quality->b.field AS field_quality,p.quality,
    CASE WHEN b.metric='lightning_distance' THEN
      p."values"->>'lightning_strike_count' END AS companion_value_text,
    CASE WHEN b.metric='lightning_distance' THEN
      jsonb_typeof(p."values"->'lightning_strike_count') END AS companion_value_kind,
    CASE WHEN b.metric='lightning_distance' THEN
      p.field_quality->'lightning_strike_count' END AS companion_field_quality,
    coalesce(p.quality->'valid'='true'::jsonb,false)
      AND p.observed_at<=:now AND p.received_at<=:now AS source_time_eligible
  FROM revisions p JOIN bindings b ON b.family=p.message_type
  CROSS JOIN boundary
  WHERE p.publication_seq<=boundary.watermark AND p.station_alias=:station
    AND p.observed_at>=:lower AND p.observed_at<=:upper
), preceding_times AS MATERIALIZED (
  SELECT device_alias,message_type,metric,max(observed_at) AS observed_at
  FROM raw WHERE observed_at<:start AND source_time_eligible
  GROUP BY device_alias,message_type,metric
), preceding AS MATERIALIZED (
  SELECT r.* FROM raw r JOIN preceding_times p
    USING(device_alias,message_type,metric,observed_at)
  WHERE r.source_time_eligible
), chosen AS (
  SELECT * FROM raw WHERE observed_at>=:start
  UNION ALL SELECT * FROM preceding
), limited AS MATERIALIZED (
  SELECT * FROM chosen ORDER BY observed_at,message_type COLLATE "C",id,metric COLLATE "C"
  LIMIT :fetch_limit
)
SELECT boundary.watermark,limited.* FROM boundary LEFT JOIN limited ON true
ORDER BY limited.observed_at,limited.message_type COLLATE "C",limited.id,limited.metric COLLATE "C"
""")

_KINDS = {"preceding_minute", "reported_interval", "instantaneous", "unknown"}
_POLICIES = {
    "v171-rain-firmware-unqualified-1",
    "v171-legacy-rain-support-unqualified-1",
    "v171-wind-report-interval-1",
    "requires-source-time-hold-policy",
    "unknown",
}


def quality_revision(row: dict[str, object]) -> str:
    """Hash only the safe projection vocabulary, never private quality JSON."""
    field = row.get("field_quality")
    top = row.get("quality")
    projected: dict[str, object] | None = None
    if isinstance(field, dict):
        projected = {
            key: field.get(key)
            for key in (
                "state",
                "valid",
                "eligible",
                "reason",
                "reported_unit",
                "canonical_unit",
                "range_policy",
            )
        }
        support = field.get("support")
        projected["support"] = (
            {key: support.get(key) for key in ("kind", "qualified", "reported_seconds", "policy")}
            if isinstance(support, dict)
            else None
        )
    payload: dict[str, object] = {
        "field_quality": projected,
        "quality": {key: top.get(key) for key in ("valid", "reason")}
        if isinstance(top, dict)
        else None,
    }
    if row.get("metric") == "lightning_distance":
        payload["companion_quality_revision"] = quality_revision(
            {"field_quality": row.get("companion_field_quality"), "quality": top}
        )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "quality_v1_" + hashlib.sha256(raw).hexdigest()


async def read_evidence_window(
    reader: PublicSqlSource,
    window: Interval,
    metrics: Sequence[str],
    *,
    preceding_hold_seconds: int,
    following_report_seconds: int,
    snapshot_watermark: str | None = None,
    evaluated_at: datetime | None = None,
    enforce_raw_range: bool = True,
    maximum_points: int = 5000,
) -> EvidenceWindow:
    """Return a whole bounded window plus per-stream preceding hold boundary.

    Caller supplies calibrated maximum forward-hold and preceding-report spans;
    no policy is inferred. The result includes all rows from start through the
    inclusive end+report span, and all rows at the latest source-time-eligible timestamp per
    (device alias,family,metric) before start within the supplied hold span.
    A caller-bound UTC evaluation instant freezes clock qualification across
    continuation reads; it cannot be later than the current reader clock.
    Aggregate callers may explicitly bypass the raw-export range limit; exact
    input-count and database read budgets still apply.
    Field-invalid/null boundaries remain included. Oversize reads fail instead
    of returning a paged subset. Root owns authority, support math and revisions.
    """
    if (
        any(
            type(v) is not int or not 0 <= v <= 86400
            for v in (preceding_hold_seconds, following_report_seconds)
        )
        or type(maximum_points) is not int
        or not 1 <= maximum_points <= 50000
        or type(enforce_raw_range) is not bool
    ):
        raise ValueError("Invalid bounded evidence read budgets")
    current = instant(reader._clock())
    if evaluated_at is None:
        now = current
    else:
        try:
            if not isinstance(evaluated_at, datetime):
                raise ValueError
            now = utc_instant(evaluated_at)
            if now > current:
                raise ValueError
        except ValueError, OverflowError:
            raise ValueError("Invalid bounded evidence evaluation instant") from None
    query = ObservationQuery(start=window.start, end=window.end, metrics=list(metrics), limit=1)
    if enforce_raw_range:
        enforce_raw_window(query, maximum_seconds=reader._raw_seconds)
    parameters = observation_parameters(query, None, reader._station)
    parameters.update(
        watermark=int(watermark(snapshot_watermark)) if snapshot_watermark is not None else None,
        lower=window.start - timedelta(seconds=preceding_hold_seconds),
        upper=window.end + timedelta(seconds=following_report_seconds),
        now=now,
        fetch_limit=maximum_points + 1,
    )
    rows = await reader._rows(EVIDENCE_SQL, parameters)
    if not rows:
        raise PublicReadUnavailable()
    bound = watermark(str(rows[0]["watermark"]))
    if snapshot_watermark is not None and bound != snapshot_watermark:
        raise PublicReadUnavailable()
    members = [r for r in rows if r["id"] is not None]
    if len(members) > maximum_points:
        raise PublicInputError(
            "range_too_large",
            "Full-window evidence exceeds its configured read budget; split the work interval.",
        )
    result = []
    try:
        for row in members:
            quality = row.get("field_quality")
            support = quality.get("support", {}) if isinstance(quality, dict) else {}
            if not isinstance(support, dict):
                support = {}
            seconds = support.get("reported_seconds")
            if (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not math.isfinite(seconds)
                or not 0 <= seconds <= 86400
            ):
                seconds = None
            kind = support.get("kind")
            policy = support.get("policy")
            point = history_point(row, now)
            result.append(
                SupportEvidence(
                    point=point,
                    exact_value=Decimal(row["value_text"]) if point.value is not None else None,
                    quality_revision=quality_revision(dict(row)),
                    observation_id=row["id"],
                    family=str(row["message_type"]),
                    device_alias=str(row["device_alias"]),
                    publication_seq=int(row["publication_seq"]),
                    source_time_eligible=row["source_time_eligible"] is True
                    and point.observed_at <= now
                    and point.received_at <= now,
                    support_kind=kind if isinstance(kind, str) and kind in _KINDS else "unknown",
                    support_policy=policy
                    if isinstance(policy, str) and policy in _POLICIES
                    else "unknown",
                    support_qualified=qualified_reported_support(support),
                    reported_seconds=seconds,
                )
            )
        return EvidenceWindow(watermark=bound, rows=tuple(result))
    except Exception:
        raise PublicReadUnavailable() from None
