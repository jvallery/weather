# SPDX-License-Identifier: MIT
"""Pure complete-window support assembly; raw public pages are never inputs.

The read adapter owns complete bounded membership/bookends and current safe quality.
Commissioned policy owns temporal meaning. Neither receipt spacing nor a packet's
report interval can supply an implicit hold cap or qualify an amount/count interval.
"""

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import Literal

from weather.db.public_evidence import EvidenceWindow, SupportEvidence
from weather.products import aggregate as engine

from .aggregate_adapter import Computation, compute
from .cursors import watermark
from .metric_registry import REGISTRY, REGISTRY_REVISION

ASSEMBLY_REVISION = "complete_qualified_support_v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_UNQUALIFIED = {
    "unknown",
    "requires-source-time-hold-policy",
    "v171-rain-firmware-unqualified-1",
    "v171-legacy-rain-support-unqualified-1",
}
_AMOUNTS = {"precipitation", "lightning_count"}


def _fraction(value: engine.Number) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, Fraction)):
        raise ValueError("Support numbers must be finite exact numeric values")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Support numbers must be finite exact numeric values")
    if isinstance(value, Decimal) and (not value.is_finite() or abs(value.adjusted()) > 4096):
        raise ValueError("Support numeric representation exceeds its bounded source format")
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _hash(prefix: str, value: object) -> str:
    return (
        prefix
        + hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class SupportPolicy:
    mode: Literal["forward_hold", "reported_interval"]
    revision: str
    hold_seconds: engine.Number | None = None
    reported_policies: tuple[str, ...] = ()
    maximum_reported_seconds: int | None = None
    authority: str | None = None

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.revision) or self.mode not in {
            "forward_hold",
            "reported_interval",
        }:
            raise ValueError("Explicit versioned support policy is required")
        if type(self.reported_policies) is not tuple or (
            len(set(self.reported_policies)) != len(self.reported_policies)
            or any(
                not _ID.fullmatch(item) or item in _UNQUALIFIED for item in self.reported_policies
            )
        ):
            raise ValueError("Reported support needs unique explicitly qualified policy identities")
        if self.authority is not None and not _ID.fullmatch(self.authority):
            raise ValueError("Invalid support authority")
        if self.mode == "forward_hold":
            if (
                self.hold_seconds is None
                or self.reported_policies
                or self.maximum_reported_seconds is not None
            ):
                raise ValueError("Forward holds require only a calibrated per-metric cap")
            cap = _fraction(self.hold_seconds)
            if not 0 < cap <= 86400 or (cap * 1_000_000).denominator != 1:
                raise ValueError("Hold cap must be positive whole microseconds within read bounds")
            object.__setattr__(self, "hold_seconds", cap)
        elif (
            self.hold_seconds is not None
            or not self.reported_policies
            or type(self.maximum_reported_seconds) is not int
            or not 1 <= self.maximum_reported_seconds <= 86400
        ):
            raise ValueError(
                "Reported support requires approved policies and an explicit read span"
            )

    def binding(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "revision": self.revision,
            "hold_seconds": None if self.hold_seconds is None else str(self.hold_seconds),
            "reported_policies": sorted(self.reported_policies),
            "maximum_reported_seconds": self.maximum_reported_seconds,
            "authority": self.authority,
        }


@dataclass(frozen=True, slots=True)
class ReadSpans:
    preceding_hold_seconds: int
    following_report_seconds: int


def required_read_spans(policies: Mapping[str, SupportPolicy]) -> ReadSpans:
    """Supply these explicit bounds to read_evidence_window; never use a raw page."""
    holds = [
        _fraction(policy.hold_seconds)
        for policy in policies.values()
        if policy.hold_seconds is not None
    ]
    reports = [
        policy.maximum_reported_seconds
        for policy in policies.values()
        if policy.maximum_reported_seconds is not None
    ]
    return ReadSpans(math.ceil(max(holds, default=Fraction())), max(reports, default=0))


@dataclass(frozen=True, slots=True)
class Assembly:
    window: engine.Interval
    supports: Mapping[str, tuple[engine.Support, ...]]
    policies: Mapping[str, SupportPolicy]
    evidence_revision: str
    policy_revision: str


def _row_binding(row: SupportEvidence) -> dict[str, object]:
    if not _ID.fullmatch(row.quality_revision):
        raise ValueError("Safe quality revision is required")
    return {
        "point": row.point.model_dump(mode="json"),
        "exact_value": None if row.exact_value is None else str(_fraction(row.exact_value)),
        "observation_id": str(row.observation_id),
        "family": row.family,
        "device_alias": row.device_alias,
        "publication_seq": row.publication_seq,
        "source_time_eligible": row.source_time_eligible,
        "quality_revision": row.quality_revision,
        "support_kind": row.support_kind,
        "support_policy": row.support_policy,
        "support_qualified": row.support_qualified,
        "reported_seconds": row.reported_seconds,
    }


def _qualified_value(row: SupportEvidence) -> Decimal | None:
    point, exact = row.point, row.exact_value
    if exact is not None:
        _fraction(exact)
    if point.value is None:
        if exact is not None:
            raise ValueError("Unavailable public evidence cannot retain a measured exact value")
        return None
    if (
        exact is None
        or (isinstance(point.value, int) and exact != point.value)
        or (isinstance(point.value, float) and float(exact) != point.value)
    ):
        raise ValueError("Exact support value differs from its qualified public projection")
    return exact if point.quality == "valid" else None


def assemble(
    window: engine.Interval,
    evidence: EvidenceWindow,
    policies: Mapping[str, SupportPolicy],
) -> Assembly:
    """Preserve stream ties/boundaries, then let the engine resolve overlapping support.

    EvidenceWindow must come from the complete-window reader with required_read_spans;
    membership completeness cannot be established from a caller's raw page or its limit.
    Unknown support is retained in input identity, never turned into qualified duration.
    """
    bound = int(watermark(evidence.watermark))
    if len(evidence.rows) > 50000:
        raise ValueError("Complete evidence exceeds the bounded assembly input")
    for metric, configured_policy in policies.items():
        entry = REGISTRY.get(metric)
        if entry is None or entry.source is None:
            raise ValueError("Support policies require direct public metric bindings")
        if (
            configured_policy.authority is not None
            and configured_policy.authority not in entry.source.families
        ):
            raise ValueError("Authority is outside the metric's registered source families")
        if metric in _AMOUNTS and (
            configured_policy.mode != "reported_interval" or configured_policy.authority is None
        ):
            raise ValueError("Amounts/counts require qualified interval support and one authority")
    rows: dict[str, SupportEvidence] = {}
    bindings: dict[str, dict[str, object]] = {}
    for row in evidence.rows:
        entry = REGISTRY.get(row.point.metric)
        if (
            entry is None
            or entry.source is None
            or row.family not in entry.source.families
            or not _ID.fullmatch(row.device_alias)
            or type(row.publication_seq) is not int
            or not 1 <= row.publication_seq <= bound
            or type(row.source_time_eligible) is not bool
        ):
            raise ValueError("Invalid complete-window public evidence binding")
        _qualified_value(row)
        binding = _row_binding(row)
        identity = row.point.evidence_id
        if identity in bindings and binding != bindings[identity]:
            raise ValueError("Conflicting evidence identity requires reconciliation")
        rows[identity], bindings[identity] = row, binding
    assembled: dict[str, list[engine.Support]] = defaultdict(list)
    streams: dict[tuple[str, str, str], list[SupportEvidence]] = defaultdict(list)
    # A valid member cannot win over an invalid/null tie at the same eligible
    # source position merely because only the valid member has a known interval.
    invalid_report_ties = {
        (row.point.metric, row.device_alias, row.family, row.point.observed_at)
        for row in rows.values()
        if row.source_time_eligible and (row.point.quality != "valid" or row.point.value is None)
    }
    for identity in sorted(rows):
        row = rows[identity]
        metric, policy = row.point.metric, policies.get(row.point.metric)
        if (
            policy is None
            or not row.source_time_eligible
            or (policy.authority is not None and policy.authority != row.family)
        ):
            continue
        if policy.mode == "forward_hold":
            streams[(metric, row.device_alias, row.family)].append(row)
            continue
        interval = row.point.support_interval
        qualified_interval = None
        if (
            row.support_qualified
            and row.support_policy in policy.reported_policies
            and row.support_kind in {"reported_interval", "preceding_minute"}
        ):
            if row.reported_seconds is None or interval is None:
                raise ValueError(
                    "Qualified reported evidence requires its explicit source interval"
                )
            seconds = _fraction(row.reported_seconds)
            if (
                seconds <= 0
                or policy.maximum_reported_seconds is None
                or seconds > policy.maximum_reported_seconds
                or (seconds * 1_000_000).denominator != 1
                or interval.end != row.point.observed_at
                or interval.start
                != row.point.observed_at - timedelta(microseconds=(seconds * 1_000_000).numerator)
            ):
                raise ValueError(
                    "Reported support exceeds or contradicts its qualified source bounds"
                )
            qualified_interval = engine.Interval(interval.start, interval.end)
        assembled[metric].append(
            engine.Support(
                qualified_interval,
                _qualified_value(row),
                identity,
                "valid"
                if row.point.quality == "valid"
                and (metric, row.device_alias, row.family, row.point.observed_at)
                not in invalid_report_ties
                else "invalid",
                _hash(
                    "support_policy_v1_",
                    [ASSEMBLY_REVISION, metric, policy.binding(), row.support_policy],
                ),
                row.family,
            )
        )
    for (metric, _device, family), members in sorted(streams.items()):
        policy = policies[metric]
        assert policy.hold_seconds is not None
        held = engine.forward_holds(
            (
                engine.Observation(
                    row.point.observed_at,
                    _qualified_value(row),
                    row.point.evidence_id,
                    row.point.quality == "valid"
                    and row.point.value is not None
                    and row.support_kind == "instantaneous",
                )
                for row in members
            ),
            cap_seconds=policy.hold_seconds,
        )
        for support in held:
            assembled[metric].append(
                engine.Support(
                    support.interval,
                    support.value,
                    support.evidence_id,
                    support.quality,
                    _hash(
                        "support_policy_v1_",
                        [ASSEMBLY_REVISION, metric, policy.binding(), support.policy_revision],
                    ),
                    family,
                )
            )
    policy_binding = {metric: policy.binding() for metric, policy in sorted(policies.items())}
    policy_revision = _hash("support_policy_v1_", [ASSEMBLY_REVISION, policy_binding])
    revision = _hash(
        "evidence_v1_",
        {
            "assembly": ASSEMBLY_REVISION,
            "registry": REGISTRY_REVISION,
            "window": [window.start.isoformat(), window.end.isoformat()],
            "watermark": evidence.watermark,
            "policies": policy_binding,
            "rows": [bindings[identity] for identity in sorted(bindings)],
        },
    )
    return Assembly(
        window,
        MappingProxyType(
            {
                metric: tuple(sorted(values, key=lambda item: item.evidence_id))
                for metric, values in sorted(assembled.items())
            }
        ),
        MappingProxyType(dict(policies)),
        revision,
        policy_revision,
    )


def compute_window(
    assembled: Assembly,
    metric: str,
    statistic: str,
    *,
    pressure_endpoint_seconds: int | None = None,
    pressure_minimum_fraction: Fraction | None = None,
) -> Computation:
    """One shared engine/public projection; downstream callers retain exact results."""
    authority = assembled.policies.get("lightning_count")
    lightning_authority: Literal["obs_st", "obs_air"] | None = None
    if authority is not None and authority.authority in {"obs_st", "obs_air"}:
        lightning_authority = "obs_st" if authority.authority == "obs_st" else "obs_air"
    return compute(
        assembled.window,
        metric,
        statistic,
        assembled.supports,
        evidence_revision=assembled.evidence_revision,
        lightning_authority=lightning_authority,
        pressure_endpoint_seconds=pressure_endpoint_seconds,
        pressure_minimum_fraction=pressure_minimum_fraction,
    )
