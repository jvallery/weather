# SPDX-License-Identifier: MIT
"""Pure safe-view mappers; no inferred cadence, support, values or source age."""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any
from uuid import UUID

from weather.api.metric_registry import REGISTRY
from weather.api.public_models import Current, HistoryPoint, Interval, Metric, Quality


class PublicMappingError(ValueError):
    pass


@dataclass(frozen=True)
class FreshnessPolicy:
    expected_interval_seconds: float | None = None
    hold_seconds: float | None = None

    def __post_init__(self) -> None:
        values = self.expected_interval_seconds, self.hold_seconds
        if (values[0] is None) != (values[1] is None) or any(
            value is not None
            and (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not 0 < value <= 86400
            )
            for value in values
        ):
            raise PublicMappingError("Invalid commissioned freshness policy")


_REASONS = {
    "payload_conflict": "Conflicting payloads disagree on this measurement.",
    "null": "The source reported no measurement.",
    "absent": "The source field is absent.",
    "invalid_type": "The source field has an invalid type.",
    "non_finite": "The source field is not a finite measurement.",
    "non_integer": "The source field is not an integer measurement.",
    "out_of_range": "The source value is outside its declared valid range.",
    "future_timestamp": "The source timestamp is not eligible.",
    "sensor_failed": "The source sensor is not qualified.",
    "numeric_storage_bound": "The source numeric representation is unsupported.",
    "unknown": "The source measurement is unavailable.",
}


def instant(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PublicMappingError("Invalid source timestamp")
    return value.astimezone(UTC)


def public_number(value: Any, kind: Any, metric: str) -> int | float | None:
    """Decode PostgreSQL numeric text; integer lightning never passes through float."""
    if kind != "number" or not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        number = Decimal(value)
        if not number.is_finite():
            return None
        if metric == "lightning_count":
            return int(number) if number >= 0 and number == number.to_integral_value() else None
        if number == number.to_integral_value():
            return int(number)
        result = float(number)
        return result if math.isfinite(result) else None
    except InvalidOperation, ValueError, OverflowError:
        return None


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _qualified(
    row: Mapping[str, Any],
    metric: str,
    now: datetime,
) -> tuple[int | float | None, Quality, str | None]:
    field, quality = _object(row.get("field_quality")), _object(row.get("quality"))
    observed, received = instant(row["observed_at"]), instant(row["received_at"])
    if observed > now or received > now:
        return None, "invalid", "The source or receipt clock is ahead of the response clock."
    number = public_number(row.get("value_text"), row.get("value_kind"), metric)
    if (
        number is not None
        and field.get("valid") is True
        and field.get("eligible") is True
        and quality.get("valid") is True
    ):
        return number, "valid", None
    state = field.get("state")
    reason = _REASONS.get(str(field.get("reason")), "The source measurement is unavailable.")
    if number is None and field.get("valid") is True and row.get("value_kind") == "number":
        reason = "The source numeric representation is unsupported."
    return None, "missing" if state in {"absent", "null"} else "invalid", reason


def current_dto(
    rows: Sequence[Mapping[str, Any]],
    station_alias: str,
    policies: Mapping[str, FreshnessPolicy],
    now: datetime,
) -> Current:
    now = instant(now)
    indexed = {str(row["metric"]): row for row in rows if row.get("metric") is not None}
    if (
        len(indexed) != sum(row.get("metric") is not None for row in rows)
        or any(metric not in REGISTRY for metric in indexed)
        or any(row.get("station_alias") != station_alias for row in indexed.values())
    ):
        raise PublicMappingError("Invalid current source vocabulary")
    metrics = []
    for metric, entry in REGISTRY.items():
        policy = policies.get(metric, FreshnessPolicy())
        row = indexed.get(metric)
        base: dict[str, Any] = {
            "metric": metric,
            "unit": entry.definition.canonical_unit,
            "expected_interval_seconds": policy.expected_interval_seconds,
            "value": None,
            "observed_at": None,
            "received_at": None,
            "valid_until": None,
            "quality": "missing",
            "freshness": "unavailable",
            "reason": "No source observation is available."
            if entry.source is not None
            else "The derived metric source is not commissioned.",
        }
        if row is not None:
            if row.get("unit") != entry.definition.canonical_unit:
                raise PublicMappingError("Invalid current canonical unit")
            value, quality, reason = _qualified(row, metric, now)
            base.update(
                value=value,
                quality=quality,
                reason=reason,
                observed_at=instant(row["observed_at"]),
                received_at=instant(row["received_at"]),
            )
            if value is not None:
                if policy.hold_seconds is None:
                    base.update(freshness="stale", reason="Freshness policy is not commissioned.")
                else:
                    expiry = base["observed_at"] + timedelta(seconds=policy.hold_seconds)
                    base.update(
                        valid_until=expiry,
                        freshness="fresh" if now < expiry else "stale",
                        reason=None if now < expiry else "Source observation expired.",
                    )
        metrics.append(Metric.model_validate(base))
    return Current(
        generated_at=now,
        station_alias=station_alias,
        data_state="live" if any(item.value is not None for item in metrics) else "unavailable",
        metrics=tuple(metrics),
    )


def history_point(row: Mapping[str, Any], now: datetime) -> HistoryPoint:
    metric = str(row["metric"])
    entry = REGISTRY.get(metric)
    family, identity = row["message_type"], row["id"]
    if (
        entry is None
        or entry.source is None
        or family not in entry.source.families
        or not isinstance(identity, UUID)
        or row.get("unit") != entry.definition.canonical_unit
    ):
        raise PublicMappingError("Invalid observation source vocabulary")
    value, quality, reason = _qualified(row, metric, instant(now))
    observed = instant(row["observed_at"])
    support = _object(_object(row.get("field_quality")).get("support"))
    interval = None
    seconds = support.get("reported_seconds")
    if (
        value is not None
        and support.get("qualified") is True
        and support.get("kind") in {"preceding_minute", "reported_interval"}
        and isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and math.isfinite(seconds)
        and 0 < seconds <= 86400
    ):
        micros = Fraction(str(seconds)) * 1_000_000
        if micros.denominator == 1:
            interval = Interval(start=observed - timedelta(microseconds=int(micros)), end=observed)
    evidence = (
        "obs_v1_"
        + hashlib.sha256((str(family) + "\0" + identity.hex + "\0" + metric).encode()).hexdigest()
    )
    return HistoryPoint(
        observed_at=observed,
        received_at=instant(row["received_at"]),
        metric=metric,
        value=value,
        unit=entry.definition.canonical_unit,
        quality=quality,
        evidence_id=evidence,
        reason=reason,
        support_interval=interval,
    )
