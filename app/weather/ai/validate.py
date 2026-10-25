# SPDX-License-Identifier: MIT
"""Untrusted Stage A output -> validated structured observations, or a fixed rejection code.

No model-written prose is ever published (C18). A reply must match the shipped VisualAnalysis
v2 schema exactly: enums and supplied evidence IDs, no free-text field and no unknown field
(a small, strict subset validator: no remote refs, formats or coercion). Semantic checks then
reject evidence IDs that were not supplied, a category reported twice, a window that does not
assess precipitation and visibility, and an intensity or sky cover on a category that cannot
carry it. Public text is rendered from the accepted enums by `render.py`. A rejection never
carries model text.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

CONTRACTS = Path(__file__).parent / "contracts"
MAX_CONTENT = 64 * 1024
CATEGORIES = (
    "cloud_cover",
    "cloud_motion",
    "sunlight",
    "rain",
    "snowfall",
    "ground_snow",
    "surface_wetness",
    "fog_visibility",
    "lightning",
    "lens_condition",
    "camera_artifact",
)
PRECIPITATION = frozenset({"rain", "snowfall"})


class Rejected(ValueError):
    """`code` is a fixed identifier, safe for metrics and the raw-response outcome column."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@cache
def schema() -> dict[str, Any]:
    """The shipped VisualAnalysis v2 schema, the only model output contract."""
    loaded: dict[str, Any] = json.loads((CONTRACTS / "visual-analysis.schema.json").read_text())
    return loaded


def _check(value: Any, rule: Mapping[str, Any], defs: Mapping[str, Any]) -> bool:
    if "$ref" in rule:
        return _check(value, defs[str(rule["$ref"]).removeprefix("#/$defs/")], defs)
    if "const" in rule and (value != rule["const"] or type(value) is not type(rule["const"])):
        return False
    if "enum" in rule and value not in rule["enum"]:
        return False
    kind = rule.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        properties = rule.get("properties", {})
        if any(name not in value for name in rule.get("required", ())):
            return False
        if rule.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(_check(value[k], properties[k], defs) for k in value if k in properties)
    if kind == "array":
        if not isinstance(value, list):
            return False
        if not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", len(value)):
            return False
        if rule.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(
            value
        ):
            return False
        return all(_check(item, rule.get("items", {}), defs) for item in value)
    if kind == "string" or "pattern" in rule or "minLength" in rule:
        if not isinstance(value, str):
            return False
        if not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", len(value)):
            return False
        return "pattern" not in rule or re.fullmatch(str(rule["pattern"]), value) is not None
    return True


def shape_valid(value: Any) -> bool:
    rules = schema()
    return _check(value, rules, rules.get("$defs", {}))


def parse(content: str | None, finish_reason: str | None) -> dict[str, Any]:
    """The JSON object of a completed reply; a single ```json fence is tolerated. Undecodable
    text (a lone surrogate) or nesting deep enough to exhaust recursion is malformed."""
    if finish_reason != "stop":
        raise Rejected("truncated" if finish_reason == "length" else "finish_reason")
    try:
        size = len(content.encode()) if content is not None else None
    except UnicodeEncodeError:
        raise Rejected("malformed_json") from None
    if content is None or size is None or size > MAX_CONTENT:
        raise Rejected("malformed_json")
    body = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    try:
        value: dict[str, Any] = json.loads(body)
        valid = isinstance(value, dict) and shape_valid(value)
    except ValueError, RecursionError:
        raise Rejected("malformed_json") from None
    if not valid:
        raise Rejected("schema")
    return value


# Stage A ---------------------------------------------------------------------------------------

INTENSITY_CATEGORIES = frozenset({"rain", "snowfall", "fog_visibility"})


@dataclass(frozen=True)
class Observation:
    id: str
    category: str
    assessment: str
    confidence: str
    window: int
    intensity: str | None = None
    sky_cover: str | None = None

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "assessment": self.assessment,
            "confidence": self.confidence,
            "intensity": self.intensity,
            "sky_cover": self.sky_cover,
            "window": self.window,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Observation:
        return cls(
            id=str(record["id"]),
            category=str(record["category"]),
            assessment=str(record["assessment"]),
            confidence=str(record["confidence"]),
            window=int(record["window"]),
            intensity=record.get("intensity"),
            sky_cover=record.get("sky_cover"),
        )


@dataclass(frozen=True)
class VisualResult:
    observations: tuple[Observation, ...]
    limitations: tuple[str, ...]


def visual(
    content: str | None,
    finish_reason: str | None,
    *,
    supplied: Sequence[str],
    window: int,
    nonce: str,
) -> VisualResult:
    """Validate one window's Stage A reply and bind its observations to this run."""
    value = parse(content, finish_reason)
    observations = value["observations"]
    categories = [o["category"] for o in observations]
    if len(set(categories)) != len(categories):
        raise Rejected("duplicate_category")
    # Precipitation and visibility are always assessed, even if only as not_assessable.
    if not set(categories) & PRECIPITATION or "fog_visibility" not in categories:
        raise Rejected("missing_assessment")
    accepted = []
    for position, item in enumerate(observations):
        if not set(item["evidence_ids"]) <= set(supplied):
            raise Rejected("unknown_evidence")
        if "intensity" in item and item["category"] not in INTENSITY_CATEGORIES:
            raise Rejected("misplaced_intensity")
        if "sky_cover" in item and item["category"] != "cloud_cover":
            raise Rejected("misplaced_sky_cover")
        accepted.append(
            Observation(
                id=f"v-{nonce}-{window}-{position}",
                category=item["category"],
                assessment=item["assessment"],
                confidence=item["confidence"],
                window=window,
                intensity=item.get("intensity"),
                sky_cover=item.get("sky_cover"),
            )
        )
    return VisualResult(tuple(accepted), tuple(value["limitations"]))
