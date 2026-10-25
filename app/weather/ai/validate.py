# SPDX-License-Identifier: MIT
"""Untrusted model output -> validated observations and claims, or a fixed rejection code.

Schema-valid text is still untrusted. Shape comes from the shipped JSON schemas (a small,
strict subset validator: no remote refs, formats or coercion). Semantic rules then reject:
truncation; unknown, stale or undeclared evidence IDs; any number that is not a supplied
rendering of a cited metric (so invented, stale, converted and irrelevant numbers fail);
markup, URLs and instruction-like text; people, vehicles, deliveries and household activity;
`possible`/`not_assessable` evidence stated as fact; and absence of precipitation claimed without
full-coverage measured zero. A rejection never carries model text.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .evidence import FULL_COVERAGE, Metric, Packet

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
def schema(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((CONTRACTS / f"{name}.schema.json").read_text())
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


def shape_valid(name: str, value: Any) -> bool:
    rules = schema(name)
    return _check(value, rules, rules.get("$defs", {}))


def parse(content: str | None, finish_reason: str | None, name: str) -> dict[str, Any]:
    """The JSON object of a completed reply; a single ```json fence is tolerated."""
    if finish_reason != "stop":
        raise Rejected("truncated" if finish_reason == "length" else "finish_reason")
    if content is None or len(content.encode()) > MAX_CONTENT:
        raise Rejected("malformed_json")
    body = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    try:
        value = json.loads(body)
    except ValueError:
        raise Rejected("malformed_json") from None
    if not isinstance(value, dict) or not shape_valid(name, value):
        raise Rejected("schema")
    return value


# Text rules -----------------------------------------------------------------------------------

MARKUP = re.compile(r"[<>{}\[\]`|\\]|://|\bwww\.|\bjavascript:|&[a-z]+;|\*\*|__|^#", re.I | re.M)
INJECTION = re.compile(
    r"\b(ignore|disregard|override|instruction|instructions|prompt|jailbreak|assistant|"
    r"as an ai|language model|system message|developer mode|tool call|function call|"
    r"api key|password|token)\b",
    re.I,
)
PERSONAL = re.compile(
    r"\b(person|persons|people|man|men|woman|women|child|children|kid|kids|boy|girl|someone|"
    r"somebody|anyone|pedestrian|pedestrians|neighbou?rs?|resident|residents|visitor|visitors|"
    r"face|faces|human|humans|figure|figures|walker|jogger|cyclist|bicycle|bike|"
    r"car|cars|truck|trucks|vehicle|vehicles|van|vans|suv|motorcycle|bus|license|licence|plate|"
    r"plates|driveway traffic|parked|parking|driving|driver|delivery|deliveries|package|packages|"
    r"parcel|parcels|courier|mail|mailman|postal|amazon|ups|fedex|usps|dog|dogs|cat|cats|pet|"
    r"pets|family|household|home owner|homeowner|occupant|occupants|lights on|garage door|"
    r"trash|bins|lawn mowing|mowing)\b",
    re.I,
)
NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|dozen|half|quarter|percent)\b",
    re.I,
)
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
HEDGES = re.compile(
    r"\b(possible|possibly|may|might|appears?|appeared|suggests?|likely|uncertain|unclear|"
    r"could not|cannot|can't|not assessable|not visible|hard to|difficult to|unable to|limited)\b",
    re.I,
)
ABSENCE = re.compile(
    r"\b(no|without|absence of|did not|didn't|never|zero)\b[^.;]{0,30}\b(rain|rainfall|"
    r"precipitation|snow|snowfall|showers?|drizzle)\b|\b(stayed|remained|was|were) dry\b",
    re.I,
)
# "No rain was visible in the sampled frames" scopes absence to the imagery, not the weather.
VISUAL_SCOPE = re.compile(r"\b(visible|seen|sampled|frames?|imagery|footage)\b", re.I)
UNIT_TAIL = re.compile(r"\s?(°C|°F|%|m/s|km/h|mph|mm|inHg|in|hPa|strikes?)(?![A-Za-z/])")


def _normalize(value: str) -> str:
    return (
        value.replace("−", "-")
        .replace(" ", " ")
        .replace(" ", " ")
        .replace("º", "°")
        .replace("˚", "°")
    )


def _plain(value: str, code_prefix: str) -> str:
    """Shared screens for every model string; returns the normalized text."""
    text = _normalize(value)
    if any(ord(ch) < 32 or 0x7F <= ord(ch) < 0xA0 or 0x200B <= ord(ch) <= 0x200F for ch in text):
        raise Rejected(f"{code_prefix}_control")
    if MARKUP.search(text):
        raise Rejected(f"{code_prefix}_markup")
    if INJECTION.search(text):
        raise Rejected(f"{code_prefix}_injection")
    if PERSONAL.search(text):
        raise Rejected(f"{code_prefix}_personal")
    return text


def _no_numbers(text: str, code_prefix: str) -> None:
    if any(ch.isdigit() for ch in text) or NUMBER_WORDS.search(text):
        raise Rejected(f"{code_prefix}_number")


# Stage A ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    id: str
    category: str
    assessment: str
    confidence: str
    description: str
    window: int

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "assessment": self.assessment,
            "confidence": self.confidence,
            "description": self.description,
            "window": self.window,
        }


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
    """Validate one window's Stage A reply and rebind its observation IDs."""
    value = parse(content, finish_reason, "visual-analysis")
    declared = set(value["evidence_ids"])
    if not declared <= set(supplied):
        raise Rejected("unknown_evidence")
    observations = value["observations"]
    if len({o["observation_id"] for o in observations}) != len(observations):
        raise Rejected("duplicate_observation")
    categories = {o["category"] for o in observations}
    # Precipitation and visibility are always assessed, even if only as not_assessable.
    if not categories & PRECIPITATION or "fog_visibility" not in categories:
        raise Rejected("missing_assessment")
    accepted = []
    for position, item in enumerate(observations):
        if not set(item["evidence_ids"]) <= declared:
            raise Rejected("undeclared_evidence")
        text = _plain(item["description"], "visual")
        _no_numbers(text, "visual")
        accepted.append(
            Observation(
                id=f"v-{nonce}-{window}-{position}",
                category=item["category"],
                assessment=item["assessment"],
                confidence=item["confidence"],
                description=" ".join(text.split()),
                window=window,
            )
        )
    limitations = []
    for limitation in value["limitations"]:
        text = _plain(limitation, "visual")
        _no_numbers(text, "visual")
        limitations.append(" ".join(text.split()))
    return VisualResult(tuple(accepted), tuple(limitations))


# Stage C ---------------------------------------------------------------------------------------

# Words that tie a number to its metric; a true value cited for the wrong quantity fails.
SUBJECTS = {
    ("temperature", "min"): ("low", "minimum", "coolest", "coldest", "ranged", "range"),
    ("temperature", "max"): ("high", "maximum", "warmest", "hottest", "peak", "ranged", "range"),
    ("temperature", "mean"): ("average", "mean"),
    ("humidity", "mean"): ("humidity",),
    ("wind_avg", "mean"): ("average wind", "mean wind", "sustained", "average speed"),
    ("wind_gust", "max"): ("gust",),
    ("precipitation", "sum"): ("rain", "precipitation"),
    ("station_pressure", "mean"): ("pressure",),
    ("lightning_count", "sum"): ("lightning", "strike"),
}
TEMPERATURE_WORDS = ("temperature", "°")


@dataclass(frozen=True)
class Claim:
    text: str
    basis: str
    evidence_ids: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        return {"text": self.text, "basis": self.basis, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class Narrative:
    claims: tuple[Claim, ...]
    limitations: tuple[str, ...]


def numbers_grounded(text: str, cited: Sequence[Metric]) -> int:
    """Count matched (number, unit) renderings; raise on any number that is not one of them."""
    allowed: dict[tuple[str, str], list[Metric]] = {}
    for metric in cited:
        for number, unit in metric.renderings:
            allowed.setdefault((number, _unit(unit)), []).append(metric)
    matched = 0
    lowered = text.lower()
    for found in NUMBER.finditer(text):
        tail = UNIT_TAIL.match(text, found.end())
        if tail is None:
            raise Rejected("ungrounded_number")
        metrics = allowed.get((found.group(), _unit(tail.group(1))))
        if not metrics:
            raise Rejected("ungrounded_number")
        if not any(_about(lowered, m) for m in metrics):
            raise Rejected("irrelevant_number")
        matched += 1
    if NUMBER_WORDS.search(text):
        raise Rejected("ungrounded_number")
    return matched


def _unit(unit: str) -> str:
    return "strike" if unit.startswith("strike") else unit


def _about(lowered: str, metric: Metric) -> bool:
    """Whole-word subject match: "rain" is not in "terrain", "low" not in "below"."""
    words = SUBJECTS[(metric.metric, metric.statistic)]
    subject = any(re.search(rf"\b{re.escape(word)}", lowered) for word in words)
    if metric.metric == "temperature":
        return subject and any(word in lowered for word in TEMPERATURE_WORDS)
    return subject


def narrative(
    content: str | None,
    finish_reason: str | None,
    *,
    packet: Packet,
    observations: Sequence[Observation],
) -> Narrative:
    """Validate a Stage C reply against the packet and validated observations."""
    value = parse(content, finish_reason, "weather-narrative")
    metrics = packet.by_id()
    seen = {o.id: o for o in observations}
    claims = []
    for item in value["summary_claims"]:
        ids = item["evidence_ids"]
        unknown = [i for i in ids if i not in metrics and i not in seen]
        if unknown:
            raise Rejected("unknown_evidence")
        cited_metrics = [metrics[i] for i in ids if i in metrics]
        cited_visual = [seen[i] for i in ids if i in seen]
        basis = item["basis"]
        if basis == "visual" and (cited_metrics or not cited_visual):
            raise Rejected("basis_mismatch")
        if basis == "instrument" and (cited_visual or not cited_metrics):
            raise Rejected("basis_mismatch")
        text = _plain(item["text"], "claim")
        count = numbers_grounded(text, cited_metrics)
        if basis == "instrument" and count == 0:
            raise Rejected("instrument_without_value")
        weak = [o for o in cited_visual if o.assessment in ("possible", "not_assessable")]
        if weak and not HEDGES.search(text):
            raise Rejected("uncertain_as_fact")
        if ABSENCE.search(text) and not (basis == "visual" and VISUAL_SCOPE.search(text)):
            rain = packet.find("precipitation", "sum")
            if (
                rain is None
                or rain.id not in ids
                or rain.value != 0
                or rain.coverage < FULL_COVERAGE
            ):
                raise Rejected("unsupported_absence")
        claims.append(Claim(" ".join(text.split()), basis, tuple(ids)))
    for event in value["events"]:
        # Events are validated for injection but never published from synthesis (#41 owns
        # visual events, derived from observations).
        _plain(event["title"], "claim")
        if any(i not in metrics and i not in seen for i in event["evidence_ids"]):
            raise Rejected("unknown_evidence")
    limitations = []
    for limitation in value["limitations"]:
        text = _plain(limitation, "claim")
        _no_numbers(text, "claim")
        limitations.append(" ".join(text.split()))
    return Narrative(tuple(claims), tuple(limitations))
