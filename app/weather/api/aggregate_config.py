# SPDX-License-Identifier: MIT
"""Bounded public support calibration shared by API and revision worker."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .metric_registry import REGISTRY, REGISTRY_REVISION
from .support_assembly import ASSEMBLY_REVISION, SupportPolicy

AGGREGATE_POLICY_FILE = Path("/etc/weather/aggregate-policy.json")


class AggregateConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AggregateConfiguration:
    policies: Mapping[str, SupportPolicy]
    pressure_endpoint_seconds: int | None
    pressure_minimum_fraction: Fraction | None
    document: Mapping[str, Any]
    revision: str


def support_binding(
    policies: Mapping[str, SupportPolicy],
    pressure_endpoint_seconds: int | None = None,
    pressure_minimum_fraction: Fraction | None = None,
) -> dict[str, Any]:
    if any(
        metric not in REGISTRY
        or REGISTRY[metric].source is None
        or not isinstance(policy, SupportPolicy)
        for metric, policy in policies.items()
    ):
        raise AggregateConfigurationError("Invalid aggregate support policies")
    if (pressure_endpoint_seconds is None) != (pressure_minimum_fraction is None):
        raise AggregateConfigurationError("Invalid aggregate endpoint policy")
    if pressure_endpoint_seconds is not None and (
        type(pressure_endpoint_seconds) is not int
        or not 1 <= pressure_endpoint_seconds <= 43200
        or not isinstance(pressure_minimum_fraction, Fraction)
        or not 0 < pressure_minimum_fraction <= 1
    ):
        raise AggregateConfigurationError("Invalid aggregate endpoint policy")
    return {
        "version": 1,
        "registry": REGISTRY_REVISION,
        "assembly": ASSEMBLY_REVISION,
        "timezone": "America/Denver",
        "policies": {metric: policy.binding() for metric, policy in sorted(policies.items())},
        "pressure_endpoint_seconds": pressure_endpoint_seconds,
        "pressure_minimum_fraction": None
        if pressure_minimum_fraction is None
        else str(pressure_minimum_fraction),
    }


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate support field")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("non-finite support value")


def load_aggregate_configuration(path: Path) -> AggregateConfiguration:
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("support file size")
        data = json.loads(
            raw, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_unique
        )
        if (
            type(data) is not dict
            or not {"version", "metrics"} <= set(data)
            or set(data) - {"version", "metrics", "pressure"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["metrics"]) is not dict
        ):
            raise ValueError("support file schema")
        policies = {}
        for metric, item in data["metrics"].items():
            if (
                metric not in REGISTRY
                or REGISTRY[metric].source is None
                or type(item) is not dict
                or not {"mode", "revision"} <= set(item)
                or set(item)
                - {
                    "mode",
                    "revision",
                    "hold_seconds",
                    "reported_policies",
                    "maximum_reported_seconds",
                    "authority",
                }
            ):
                raise ValueError("support policy schema")
            options = dict(item)
            if "reported_policies" in options:
                if type(options["reported_policies"]) is not list:
                    raise ValueError("reported support policies")
                options["reported_policies"] = tuple(options["reported_policies"])
            policy = SupportPolicy(**options)
            binding = REGISTRY[metric].source
            if binding is None or (
                policy.authority is not None and policy.authority not in binding.families
            ):
                raise ValueError("support authority")
            if metric in {"precipitation", "lightning_count"} and (
                policy.mode != "reported_interval" or policy.authority is None
            ):
                raise ValueError("amount authority")
            policies[metric] = policy
        endpoint, minimum = None, None
        pressure = data.get("pressure")
        if pressure is not None:
            if (
                type(pressure) is not dict
                or set(pressure) != {"endpoint_seconds", "minimum_fraction"}
                or type(pressure["endpoint_seconds"]) is not int
                or not 1 <= pressure["endpoint_seconds"] <= 43200
                or type(pressure["minimum_fraction"]) not in {int, Decimal}
            ):
                raise ValueError("pressure support policy")
            endpoint = pressure["endpoint_seconds"]
            minimum = Fraction(str(pressure["minimum_fraction"]))
            if not 0 < minimum <= 1:
                raise ValueError("pressure support fraction")
        document = support_binding(policies, endpoint, minimum)
        revision = (
            "aggregate_policy_v1_"
            + hashlib.sha256(
                json.dumps(
                    document,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
        )
        return AggregateConfiguration(
            MappingProxyType(policies), endpoint, minimum, MappingProxyType(document), revision
        )
    except Exception:
        raise AggregateConfigurationError("Invalid aggregate support policy file") from None
