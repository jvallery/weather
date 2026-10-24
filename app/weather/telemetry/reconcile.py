# SPDX-License-Identifier: MIT
"""First canonical values remain immutable; only disputed field qualification changes."""

from typing import Any


def disputed_fields(first_quality: dict[str, Any], second_quality: dict[str, Any]) -> list[str]:
    first, second = first_quality.get("fields", {}), second_quality.get("fields", {})
    fields = []
    for name in sorted(set(first) | set(second)):
        # Support policy evolution is separate from conflicting reported evidence.
        keys = ("state", "reported", "reported_unit", "canonical_unit")
        left = {key: first.get(name, {}).get(key) for key in keys}
        right = {key: second.get(name, {}).get(key) for key in keys}
        if left != right:
            fields.append(name)
    return fields


def qualify_conflict(quality: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Pure counterpart of the scoped database invalidation function."""
    result = {
        **quality,
        "fields": {name: dict(item) for name, item in quality.get("fields", {}).items()},
    }
    for name in fields:
        if name in result["fields"]:
            result["fields"][name].update(eligible=False, valid=False, reason="payload_conflict")
    return result
