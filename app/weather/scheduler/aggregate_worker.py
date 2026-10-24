# SPDX-License-Identifier: MIT
"""Deterministic closed-window aggregate jobs; no pools, files or activation here."""

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from prometheus_client import Counter
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.api.aggregate_config import support_binding
from weather.api.aggregate_source import (
    _PROCESS_SLOT,
    _Budget,
    _index,
    _inputs_for,
    _preflight_compute,
    _roles,
)
from weather.api.cursors import watermark
from weather.api.metric_registry import REGISTRY, PublicInputError
from weather.api.public_common import utc_instant
from weather.api.public_models import Interval as PublicInterval
from weather.api.support_assembly import (
    SupportPolicy,
    assemble,
    compute_window,
    required_read_spans,
)
from weather.db import aggregate_store, jobs
from weather.db.public_evidence import read_evidence_window
from weather.db.public_source import PublicSqlSource
from weather.products.aggregate import Interval
from weather.scheduler.registry import JobContext, Registry
from weather.timemodel.calendar import day_for, slot_for

WORKER_REVISION = "closed_aggregate_worker_v1"
MAX_PREPARED_BYTES = 16 * 1024 * 1024
MAX_COMPUTATION_REFERENCES = 50000
MAX_COMPUTATION_GAPS = 1000
PUBLIC_FAMILIES = {"obs_st", "rapid_wind", "evt_precip", "evt_strike", "obs_air", "obs_sky"}
SKIPPED = Counter("weather_aggregate_worker_skipped_total", "Bounded aggregate skips", ["reason"])
_REASONS = {"uncommissioned", "pressure_uncommissioned", "wind_uncommissioned", "expired", "budget"}


def _skip(reason: str) -> None:
    if reason not in _REASONS:
        raise ValueError("Unknown aggregate skip reason")
    SKIPPED.labels(reason).inc()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _stamp(value: datetime) -> str:
    return utc_instant(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _station(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Invalid public station alias")
    return value


def _prepared_size(value: aggregate_store.StoredComputation) -> int:
    def timestamp(item: Any) -> str:
        if not isinstance(item, datetime):
            raise TypeError("Unexpected aggregate persistence value")
        return _stamp(item)

    return len(
        json.dumps(
            dict(value.parameters),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=timestamp,
        ).encode()
    )


@dataclass(frozen=True)
class Change:
    id: UUID
    family: str
    station: str
    source_time: datetime
    watermark: int

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Change:
        try:
            if set(value) != {"id", "family", "station", "sourceTime", "watermark"}:
                raise ValueError
            if not isinstance(value["id"], str):
                raise ValueError
            identity = UUID(value["id"])
            if str(identity) != value["id"] or value["family"] not in PUBLIC_FAMILIES:
                raise ValueError
            if type(value["watermark"]) is not int or value["watermark"] < 1:
                raise ValueError
            watermark(str(value["watermark"]))
            # PostgreSQL jsonb emits timestamptz with +00:00, while internal
            # child payloads use canonical Z. Both must explicitly mean UTC.
            if not isinstance(value["sourceTime"], str):
                raise ValueError
            source = utc_instant(datetime.fromisoformat(value["sourceTime"]))
            return cls(
                identity, value["family"], _station(value["station"]), source, value["watermark"]
            )
        except ValueError, TypeError, KeyError, OverflowError:
            raise ValueError("Invalid internal telemetry change") from None


@dataclass(frozen=True)
class Target:
    kind: Literal["hour", "weather_day"]
    start: datetime
    end: datetime

    @classmethod
    def at(cls, kind: Literal["hour", "weather_day"], instant: datetime) -> Target:
        value = slot_for(instant) if kind == "hour" else day_for(instant)
        return cls(kind, value.start, value.end)

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Target:
        kind = value["kind"]
        if type(kind) is not str or kind not in {"hour", "weather_day"}:
            raise ValueError("Invalid aggregate window")
        start, end = utc_instant(value["start"]), utc_instant(value["end"])
        canonical = cls.at("hour" if kind == "hour" else "weather_day", start)
        if canonical.start != start or canonical.end != end:
            raise ValueError("Aggregate window is not a canonical UTC hour or weather day")
        return canonical


@dataclass(frozen=True)
class PlannedJob:
    key: str
    payload: Mapping[str, Any]
    not_before: datetime
    deadline: datetime


def affected_targets(change: Change, policies: Mapping[str, SupportPolicy]) -> tuple[Target, ...]:
    relevant = {}
    for name, policy in policies.items():
        entry = REGISTRY.get(name)
        if (
            entry is not None
            and entry.source is not None
            and change.family in entry.source.families
        ):
            relevant[name] = policy
    if not relevant:
        return ()
    spans = required_read_spans(relevant)
    lower = change.source_time - timedelta(seconds=spans.following_report_seconds)
    upper = change.source_time + timedelta(seconds=spans.preceding_hold_seconds)
    # Include the nominal source slot even for a reported interval ending exactly
    # on a boundary; no duration is inferred from this scheduling-only microsecond.
    upper = max(upper, change.source_time + timedelta(microseconds=1))
    targets = set()
    for kind in ("hour", "weather_day"):
        current = Target.at(kind, lower)
        while current.start < upper:
            targets.add(current)
            if len(targets) > 56:
                raise ValueError("Calibrated aggregate envelope exceeds its window budget")
            current = Target.at(kind, current.end)
    return tuple(sorted(targets, key=lambda t: (t.start, t.end, t.kind)))


def plan_change(
    change: Change,
    policies: Mapping[str, SupportPolicy],
    policy_revision: str,
    *,
    now: datetime,
    event_deadline: datetime,
) -> tuple[PlannedJob, ...]:
    now, event_deadline = utc_instant(now), utc_instant(event_deadline)
    result = []
    for target in affected_targets(change, policies):
        payload: dict[str, Any] = {
            "version": 1,
            "station": change.station,
            "kind": target.kind,
            "start": _stamp(target.start),
            "end": _stamp(target.end),
            "policy": policy_revision,
        }
        identity = [
            WORKER_REVISION,
            change.station,
            target.kind,
            payload["start"],
            payload["end"],
            policy_revision,
        ]
        close_deadline = target.end + timedelta(hours=48)
        if close_deadline > now:
            result.append(
                PlannedJob(
                    "aggregate-close:" + _hash(identity),
                    dict(payload),
                    target.end + timedelta(seconds=120),
                    close_deadline,
                )
            )
        if target.end <= now and event_deadline > now:
            late_payload = {**payload, "watermark": change.watermark}
            # Retry time never changes payload, due timestamp or deadline.
            # The original v4 event deadline is created at event_time + one day.
            result.append(
                PlannedJob(
                    "aggregate-late:" + _hash([*identity, change.watermark]),
                    late_payload,
                    event_deadline - timedelta(days=1),
                    event_deadline,
                )
            )
    return tuple(result)


class AggregateRevisionWorker:
    def __init__(
        self,
        engine: AsyncEngine,
        policies: Mapping[str, SupportPolicy],
        policy_document: Mapping[str, Any],
        *,
        pressure_endpoint_seconds: int | None = None,
        pressure_minimum_fraction: Fraction | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        maximum_inputs: int = 50000,
        maximum_work: int = 1_000_000,
        maximum_prepared_bytes: int = MAX_PREPARED_BYTES,
    ) -> None:
        if (
            type(maximum_inputs) is not int
            or not 1 <= maximum_inputs <= 50000
            or type(maximum_work) is not int
            or not 1 <= maximum_work <= 1_000_000
            or type(maximum_prepared_bytes) is not int
            or not 1 <= maximum_prepared_bytes <= MAX_PREPARED_BYTES
            or any(not isinstance(value, SupportPolicy) for value in policies.values())
        ):
            raise ValueError("Invalid aggregate worker policy or budgets")
        if (pressure_endpoint_seconds is None) != (pressure_minimum_fraction is None) or (
            pressure_endpoint_seconds is not None
            and (
                type(pressure_endpoint_seconds) is not int
                or not 1 <= pressure_endpoint_seconds <= 86400
                or pressure_minimum_fraction is None
                or not 0 < pressure_minimum_fraction <= 1
            )
        ):
            raise ValueError("Invalid explicit pressure endpoint policy")
        valid = {
            name: policy
            for name, policy in policies.items()
            if name in REGISTRY and REGISTRY[name].source is not None
        }
        if len(valid) != len(policies):
            _skip("uncommissioned")
        expected = support_binding(
            valid,
            pressure_endpoint_seconds=pressure_endpoint_seconds,
            pressure_minimum_fraction=pressure_minimum_fraction,
        )
        if dict(policy_document) != expected:
            raise ValueError("Aggregate policy document must equal its complete public binding")
        self.engine, self.policies = engine, MappingProxyType(valid)
        self.document = json.loads(json.dumps(expected, allow_nan=False))
        self.policy_revision = "aggregate_policy_v1_" + _hash(expected)
        self.endpoint, self.minimum = pressure_endpoint_seconds, pressure_minimum_fraction
        self.clock, self.maximum_inputs, self.maximum_work = clock, maximum_inputs, maximum_work
        self.maximum_prepared_bytes = maximum_prepared_bytes

    def register(self, registry: Registry) -> None:
        registry.register("aggregate.changed", self.changed, topics=("telemetry.changed",))
        registry.register("aggregate.conflict", self.conflict, topics=("telemetry.conflict",))
        registry.register("aggregate.recompute", self.recompute)

    def _recipes(self, target: Target) -> tuple[tuple[str, str], ...]:
        result = []
        for metric, entry in sorted(REGISTRY.items()):
            if entry.source is None or not any(
                s != "observed" for s in entry.definition.statistics
            ):
                continue
            policy = self.policies.get(metric)
            if policy is None or (
                metric in {"precipitation", "lightning_count"}
                and (
                    policy.mode != "reported_interval"
                    or policy.authority not in entry.source.families
                )
            ):
                _skip("uncommissioned")
                continue
            for statistic in entry.definition.statistics:
                if statistic == "observed":
                    continue
                if statistic == "vector_mean" and not all(
                    name in self.policies for name in _roles(metric, statistic)
                ):
                    _skip("wind_uncommissioned")
                    continue
                if statistic == "trend" and (
                    self.endpoint is None
                    or self.minimum is None
                    or (target.end - target.start).total_seconds() < 2 * self.endpoint
                ):
                    _skip("pressure_uncommissioned")
                    continue
                result.append((metric, statistic))
        return tuple(result)

    async def changed(self, context: JobContext) -> Mapping[str, Any]:
        event = Change.parse(context.job.payload)
        now = utc_instant(self.clock())
        reader = PublicSqlSource(
            self.engine, station_alias=event.station, maximum_raw_seconds=1, clock=lambda: now
        )
        authority = await reader._rows(
            text("""SELECT revision_seq
          FROM public_api.telemetry_evidence_revisions
          WHERE revision_seq=:revision AND id=:id AND message_type=:family
            AND station_alias=:station AND observed_at=:source"""),
            {
                "revision": event.watermark,
                "id": event.id,
                "family": event.family,
                "station": event.station,
                "source": event.source_time,
            },
        )
        if len(authority) != 1:
            raise ValueError("Telemetry change has no authoritative safe evidence revision")
        plans = plan_change(
            event, self.policies, self.policy_revision, now=now, event_deadline=context.job.deadline
        )
        if not plans:
            _skip("uncommissioned" if not affected_targets(event, self.policies) else "expired")

        async def write(connection: AsyncConnection) -> None:
            for plan in plans:
                await jobs.enqueue(
                    connection,
                    kind="aggregate.recompute",
                    key=plan.key,
                    payload=plan.payload,
                    not_before=plan.not_before,
                    deadline=plan.deadline,
                )

        result = {"scheduled": len(plans)}
        await context.commit(write, result=result)
        return result

    async def conflict(self, context: JobContext) -> Mapping[str, Any]:
        # v4's effective public qualification change emits telemetry.changed in
        # the same transaction. There were no aggregate ledgers before v4/v5.
        # Never inspect/repackage private conflict fields into a public job.
        result = {"reason": "covered_by_telemetry_changed"}
        await context.commit(result=result)
        return result

    async def _prepare_group(
        self,
        reader: PublicSqlSource,
        target: Target,
        roles: tuple[str, ...],
        recipes: tuple[tuple[str, str], ...],
        *,
        boundary: str,
        evaluated_at: datetime,
        station: str,
        remaining_bytes: int,
    ) -> tuple[list[aggregate_store.StoredComputation], int]:
        """The coroutine frame owns one group's evidence/index and releases both on return."""
        policies = {name: self.policies[name] for name in roles if name in self.policies}
        spans = required_read_spans(policies)
        evidence = await read_evidence_window(
            reader,
            PublicInterval(start=target.start, end=target.end),
            roles,
            preceding_hold_seconds=spans.preceding_hold_seconds,
            following_report_seconds=spans.following_report_seconds,
            snapshot_watermark=boundary,
            evaluated_at=evaluated_at,
            enforce_raw_range=False,
            maximum_points=self.maximum_inputs,
        )
        if len(evidence.rows) > self.maximum_inputs:
            raise PublicInputError("range_too_large", "Aggregate evidence exceeds group budget")
        index = await _index(evidence, _Budget(self.maximum_work))
        prepared = []
        prepared_bytes = 0
        window = Interval(target.start, target.end)
        for metric, statistic in recipes:
            await asyncio.sleep(0)
            # One admitted recipe does not spend another recipe's CPU allowance.
            budget = _Budget(self.maximum_work)
            selected = await _inputs_for(window, roles, policies, index, boundary, budget)
            await budget.charge(len(selected.rows))
            assembled = assemble(window, selected, policies)
            await _preflight_compute(assembled, statistic, budget)
            try:
                computation = compute_window(
                    assembled,
                    metric,
                    statistic,
                    pressure_endpoint_seconds=self.endpoint,
                    pressure_minimum_fraction=self.minimum,
                )
            except ValidationError as error:
                if any(issue["type"] == "too_long" for issue in error.errors(include_input=False)):
                    raise PublicInputError(
                        "range_too_large", "Aggregate item exceeds result budget"
                    ) from None
                raise
            if (
                len(computation.aggregate.evidence_ids) > MAX_COMPUTATION_REFERENCES
                or len(computation.aggregate.coverage.gaps) > MAX_COMPUTATION_GAPS
            ):
                raise PublicInputError("range_too_large", "Aggregate item exceeds result budget")
            item = aggregate_store.prepare(
                station,
                computation,
                boundary=boundary,
                policy_revision=self.policy_revision,
                evaluated_at=evaluated_at,
                policy_document=self.document,
            )
            # Include a conservative separator byte per item; the caller also
            # reserves the surrounding JSON array brackets.
            prepared_bytes += _prepared_size(item) + 1
            if prepared_bytes > remaining_bytes:
                raise PublicInputError(
                    "range_too_large", "Aggregate prepared output exceeds byte budget"
                )
            prepared.append(item)
        return prepared, prepared_bytes

    async def recompute(self, context: JobContext) -> Mapping[str, Any]:
        payload = context.job.payload
        if (
            set(payload)
            not in (
                {"version", "station", "kind", "start", "end", "policy"},
                {"version", "station", "kind", "start", "end", "policy", "watermark"},
            )
            or type(payload.get("version")) is not int
            or payload["version"] != 1
            or payload["policy"] != self.policy_revision
        ):
            raise ValueError("Invalid aggregate recompute binding")
        station, target = _station(payload["station"]), Target.parse(payload)
        now = utc_instant(self.clock())
        if target.end > now:
            raise ValueError("Active aggregate windows are never persisted")
        if not _PROCESS_SLOT.acquire(blocking=False):
            _skip("budget")
            raise PublicInputError("unavailable", "Aggregate worker is busy")
        try:
            recipes = self._recipes(target)
            prepared = []
            boundary = "0"
            if recipes:
                reader = PublicSqlSource(
                    self.engine, station_alias=station, maximum_raw_seconds=1, clock=lambda: now
                )
                current = await reader._rows(
                    text("SELECT public_api.telemetry_snapshot_watermark() AS watermark"), {}
                )
                if len(current) != 1:
                    raise ValueError("Aggregate publication boundary is unavailable")
                latest = watermark(str(current[0]["watermark"]))
                if "watermark" in payload:
                    if type(payload["watermark"]) is not int:
                        raise ValueError("Invalid aggregate event watermark")
                    boundary = watermark(str(payload["watermark"]))
                    if int(boundary) > int(latest):
                        raise ValueError("Aggregate event watermark is not committed")
                else:
                    boundary = latest
                groups: dict[tuple[str, ...], list[tuple[str, str]]] = {}
                for metric, statistic in recipes:
                    groups.setdefault(_roles(metric, statistic), []).append((metric, statistic))
                prepared_bytes = 2
                for roles, grouped_recipes in groups.items():
                    group_values, size = await self._prepare_group(
                        reader,
                        target,
                        roles,
                        tuple(grouped_recipes),
                        boundary=boundary,
                        evaluated_at=now,
                        station=station,
                        remaining_bytes=self.maximum_prepared_bytes - prepared_bytes,
                    )
                    prepared.extend(group_values)
                    prepared_bytes += size
                    # Only serialized persistence parameters cross group boundaries;
                    # no evidence window or index is retained from a completed group.
                    del group_values
            result: dict[str, Any] = {
                "results": len(prepared),
                "watermark": boundary,
                "created": 0,
                "unchanged": 0,
                "superseded": 0,
            }

            async def write(connection: AsyncConnection) -> None:
                for value in prepared:
                    result[await aggregate_store.persist(connection, value)] += 1

            await context.commit(write, result=result)
            return result
        except PublicInputError:
            _skip("budget")
            raise
        finally:
            _PROCESS_SLOT.release()
