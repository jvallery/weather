# SPDX-License-Identifier: MIT
"""Untrusted model output -> validated observations and claims, or a fixed rejection code.

Schema-valid text is still untrusted. Shape comes from the shipped JSON schemas (a small,
strict subset validator: no remote refs, formats or coercion). Text is NFKC-normalized and
must be Latin script (plus °, – and ’) with no format characters. Semantic rules then reject:
truncation; unknown, stale or undeclared evidence IDs; any number, numeric character or
spelled number that is not a supplied rendering of a cited metric bound to that metric's
subject word (so invented, stale, converted, irrelevant and swapped numbers fail); markup, URLs
and instruction-like text; people, vehicles, deliveries and household activity; weather words
without a cited `visible` observation of that category (or a `possible` one with an adjacent
hedge); and absence of precipitation or lightning without a cited full-coverage measured zero,
unless scoped to the imagery ("not visible in the frames"). A rejection never carries model
text.
"""

import json
import re
import unicodedata
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


def _words(*items: str) -> str:
    """A regex alternation of whole phrases, longest first; a space matches any whitespace."""
    ordered = sorted(set(items), key=lambda item: (-len(item), item))
    return "|".join(r"\s+".join(re.escape(part) for part in item.split()) for item in ordered)


MARKUP = re.compile(r"[<>{}\[\]`|\\]|://|\bwww\.|\bjavascript:|&[a-z]+;|\*\*|__|^#", re.I | re.M)
INJECTION = re.compile(
    r"\b(ignore|disregard|override|instruction|instructions|prompt|jailbreak|assistant|"
    r"as an ai|language model|system message|developer mode|tool call|function call|"
    r"api key|password|token)\b",
    re.I,
)
# Base forms; a trailing "s"/"es" is matched for every entry, so plurals need no listing.
PERSONAL_WORDS = (
    # People
    "person", "people", "man", "men", "woman", "women", "child", "children", "kid", "boy",
    "girl", "baby", "babies", "toddler", "infant", "teen", "teenager", "adult", "lady", "ladies",
    "gentleman", "gentlemen", "guy", "someone", "somebody", "anyone", "anybody", "everyone",
    "pedestrian", "passerby", "passersby", "neighbor", "neighbour", "resident", "visitor",
    "guest", "stranger", "intruder", "crowd", "face", "human", "figure", "silhouette", "walker",
    "jogger", "runner", "hiker", "cyclist", "biker", "rider", "skateboarder", "worker", "crew",
    "contractor", "gardener", "landscaper", "mailman", "postman", "mail carrier", "courier",
    "driver", "motorist", "commuter", "occupant", "family", "families", "household", "homeowner",
    "home owner",
    # Vehicles
    "car", "truck", "vehicle", "van", "minivan", "suv", "sedan", "coupe", "jeep", "taxi", "bus",
    "motorcycle", "motorbike", "scooter", "moped", "bike", "bicycle", "tractor", "trailer", "rv",
    "lorry", "automobile", "license", "licence", "plate", "headlight", "taillight",
    "brake light", "traffic", "parked", "parking", "driving", "drove", "driveway traffic",
    # Deliveries
    "delivery", "deliveries", "deliver", "delivered", "delivering", "package", "parcel", "mail",
    "mailbox", "postal", "amazon", "ups", "fedex", "usps", "dhl",
    # Household and personal activity
    "walk", "walked", "walking", "stroll", "strolled", "strolling", "stroller", "jogging",
    "jogged", "mowing", "mower", "lawn mowing", "trash", "bin", "garbage", "recycling",
    "garage door", "lights on", "porch light", "doorbell", "sprinkler", "dog", "puppy",
    "puppies", "cat", "kitten", "pet",
)  # fmt: skip
PERSONAL = re.compile(rf"\b(?:{_words(*PERSONAL_WORDS)})(?:e?s)?\b", re.I)
NUMBER_WORDS = re.compile(
    r"\b(?:"
    + _words(
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "dozen",
        "half",
        "quarter",
        "percent",
        "single",
        "once",
        "twice",
    )  # fmt: skip
    + r")s?\b",
    re.I,
)
# No look-behind: ".5" and the "40" in "x40" are numbers too.
NUMBER = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)")
HEDGES = re.compile(
    r"\b(possible|possibly|may|might|perhaps|probably|appears?|appeared|seems?|seemed|"
    r"suggests?|suggested|likely|uncertain|unclear|could not|cannot|can't|not assessable|"
    r"not visible|hard to|difficult to|unable to|limited)\b",
    re.I,
)
# Stating that something could not be judged is not a claim about it either way.
INABILITY = re.compile(
    r"\b(?:could not|couldn't|cannot|can't|unable to|impossible to|not possible to|"
    r"not assessable|hard to|difficult to)\b",
    re.I,
)
UNIT_TAIL = re.compile(r"\s?(°C|°F|%|m/s|km/h|mph|mm|inHg|in|hPa|strikes?)(?![A-Za-z/])")
# A clause ends at , ; ! ? or a full stop that is not a decimal point.
BOUNDARY = re.compile(r"[,;!?]|(?<!\d)\.|\.(?!\d)")
TOKEN = re.compile(r"[A-Za-z’']+")
# Hedges and inability phrases count only this many words either side of the weather word.
ADJACENT_WORDS = 3

# Weather words and the observation categories a positive claim using them must cite.
RAIN_WORDS = (
    "rain", "rains", "rained", "raining", "rainy", "rainfall", "raindrop", "raindrops",
    "drizzle", "drizzled", "drizzling", "drizzly", "shower", "showers", "showery", "downpour",
    "downpours", "sprinkles",
)  # fmt: skip
MIXED_WORDS = (
    "precipitation", "precipitating", "sleet", "sleeting", "hail", "hailing", "hailstones",
    "graupel", "wintry mix",
)  # fmt: skip
SNOW_WORDS = (
    "snow", "snows", "snowed", "snowing", "snowy", "snowfall", "snowflakes", "flurries",
    "flurry", "blizzard",
)  # fmt: skip
LIGHTNING_WORDS = (
    "lightning", "thunder", "thundering", "thunderstorm", "thunderstorms", "thundery",
)  # fmt: skip
FOG_WORDS = ("fog", "foggy", "mist", "misty", "haze", "hazy", "smog")
FROST_WORDS = ("frost", "frosty", "frosted", "dew", "icy")
WEATHER_CATEGORIES: dict[str, frozenset[str]] = {
    word: categories
    for words, categories in (
        (RAIN_WORDS, frozenset({"rain"})),
        (MIXED_WORDS, PRECIPITATION),
        (SNOW_WORDS, frozenset({"snowfall", "ground_snow"})),
        (LIGHTNING_WORDS, frozenset({"lightning"})),
        (FOG_WORDS, frozenset({"fog_visibility"})),
        (FROST_WORDS, frozenset({"surface_wetness", "ground_snow"})),
    )
    for word in words
}
WEATHER = re.compile(rf"\b(?:{_words(*WEATHER_CATEGORIES)})\b", re.I)

NEGATORS = _words(
    "no", "not", "without", "absence of", "absent", "lack of", "lacking", "did not", "didn't",
    "never", "zero", "nor", "free of", "none",
)  # fmt: skip
TRAILING = _words("not", "never", "held off", "stayed away", "absent", "missing")


def _absence(words: tuple[str, ...]) -> list[re.Pattern[str]]:
    alternation = _words(*words)
    return [
        re.compile(rf"\b(?:{NEGATORS})\b[^.;]{{0,30}}?\b(?:{alternation})\b", re.I),
        re.compile(rf"\b(?:{alternation})\b[^.;]{{0,20}}?\b(?:{TRAILING})\b", re.I),
        re.compile(rf"\b(?:{alternation})[- ]free\b", re.I),
    ]


DRY = [
    re.compile(r"\bnot\s+a\s+(?:single\s+)?(?:drop|flake)\b", re.I),
    re.compile(
        r"\bdry\s+(?:day|days|weather|conditions|spell|afternoon|morning|evening|night|skies|"
        r"period|hours?|interval|segment)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:stayed|remained|was|were|stays|remains|is|are|kept|staying|remaining)\s+"
        r"(?:[a-z]+\s+)?dry\b",
        re.I,
    ),
    re.compile(r"\bbone[- ]dry\b", re.I),
]
# Each absence family and the measured zero (metric, statistic) that may support it.
ABSENCE_GROUPS: tuple[tuple[list[re.Pattern[str]], tuple[str, str] | None], ...] = (
    (_absence(RAIN_WORDS + MIXED_WORDS + SNOW_WORDS) + DRY, ("precipitation", "sum")),
    (_absence(LIGHTNING_WORDS), ("lightning_count", "sum")),
    (_absence(FOG_WORDS + FROST_WORDS), None),
)
_VERB = r"(?:visible|seen|evident|apparent|observed|detected|discernible)"
_SCOPE = (
    r"\s+(?:in|on|across|within)\s+(?:the\s+|any\s+)?(?:sampled\s+|available\s+)?"
    r"(?:frames?|imagery|images?|footage|keogram)\b"
)
_ANY = _words(*WEATHER_CATEGORIES)


def _scoped(tail: str) -> tuple[re.Pattern[str], ...]:
    """'No rain was visible in the frames': absence scoped to the imagery, not the weather."""
    return (
        re.compile(
            rf"\bno\s+(?:[a-z]+\s+){{0,2}}?(?:{_ANY})\b(?:\s+[a-z]+){{0,2}}?\s+"
            rf"(?:(?:was|were|is|are)\s+)?{_VERB}{tail}",
            re.I,
        ),
        re.compile(
            rf"\b(?:{_ANY})\b(?:\s+[a-z]+){{0,2}}?\s+(?:was|were|is|are)\s+not\s+{_VERB}{tail}",
            re.I,
        ),
        re.compile(
            r"\b(?:frames?|imagery|images?|footage|keogram)\s+(?:show|showed|shows|reveal|"
            rf"revealed|reveals)\s+no\s+(?:visible\s+)?(?:{_ANY})\b",
            re.I,
        ),
    )


# Claims and limitations must name the imagery; a Stage A description is about its frames.
VISUAL_ABSENCE = _scoped(_SCOPE)
FRAME_ABSENCE = _scoped(f"(?:{_SCOPE})?")
ALLOWED_SYMBOLS = frozenset("°–’")


def _normalize(value: str) -> str:
    """Compatibility forms folded (NFKC), so look-alike digits and spaces are plain ASCII."""
    text = value.replace("−", "-").replace("º", "°").replace("˚", "°")
    return unicodedata.normalize("NFKC", text)


def _latin(ch: str) -> bool:
    if " " <= ch <= "~" or ch in ALLOWED_SYMBOLS:
        return True
    return unicodedata.category(ch).startswith("L") and unicodedata.name(ch, "").startswith(
        "LATIN "
    )


def _plain(value: str, code_prefix: str) -> str:
    """Shared screens for every model string; returns the normalized text."""
    if any(not "0" <= ch <= "9" and unicodedata.numeric(ch, None) is not None for ch in value):
        raise Rejected(f"{code_prefix}_number")
    text = _normalize(value)
    if any(
        ord(ch) < 32 or 0x7F <= ord(ch) < 0xA0 or unicodedata.category(ch) == "Cf" for ch in text
    ):
        raise Rejected(f"{code_prefix}_control")
    if not all(_latin(ch) for ch in text):
        raise Rejected(f"{code_prefix}_script")
    if MARKUP.search(text):
        raise Rejected(f"{code_prefix}_markup")
    if INJECTION.search(text):
        raise Rejected(f"{code_prefix}_injection")
    if PERSONAL.search(text):
        raise Rejected(f"{code_prefix}_personal")
    return text


def _no_numbers(text: str, code_prefix: str) -> None:
    if any(unicodedata.numeric(ch, None) is not None for ch in text) or NUMBER_WORDS.search(text):
        raise Rejected(f"{code_prefix}_number")


def _clause(text: str, position: int) -> tuple[int, int]:
    """The clause around `position`; separators inside parentheses do not end it."""
    start, end, depth = 0, len(text), 0
    boundaries = {m.start() for m in BOUNDARY.finditer(text)}
    for index, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif index in boundaries and depth == 0:
            if index < position:
                start = index + 1
            elif index >= position:
                end = index
                break
    return start, end


def _near(text: str, span: tuple[int, int], pattern: re.Pattern[str]) -> bool:
    """Whether `pattern` occurs within ADJACENT_WORDS words of `span`, in the same clause."""
    tokens = [m.span() for m in TOKEN.finditer(text)]
    first = next(i for i, (_, end) in enumerate(tokens) if end > span[0])
    last = max(i for i, (start, _) in enumerate(tokens) if start < span[1])
    low, high = _clause(text, span[0])
    start = max(tokens[max(0, first - ADJACENT_WORDS)][0], low)
    end = min(tokens[min(len(tokens) - 1, last + ADJACENT_WORDS)][1], high)
    return pattern.search(text, start, end) is not None


def _blank(text: str, spans: Sequence[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _absences(text: str, scope: Sequence[re.Pattern[str]]) -> tuple[str, list[Any]]:
    """Blank imagery-scoped phrases, then find absence claims and the metric each needs.

    Returns the text with both blanked (what remains can only make positive claims) and one
    entry per absence claim: its supporting (metric, statistic), or None if nothing can."""
    text = _blank(text, [m.span() for pattern in scope for m in pattern.finditer(text)])
    spans, supports = [], []
    for patterns, support in ABSENCE_GROUPS:
        for pattern in patterns:
            for found in pattern.finditer(text):
                if INABILITY.search(found.group()):
                    continue
                spans.append(found.span())
                supports.append(support)
    return _blank(text, spans), supports


def _positive(text: str, rest: str) -> list[tuple[tuple[int, int], frozenset[str]]]:
    """Weather words in `rest` that claim the weather happened (not "could not be judged")."""
    found = []
    for match in WEATHER.finditer(rest):
        if not _near(text, match.span(), INABILITY):
            word = " ".join(match.group().lower().split())
            found.append((match.span(), WEATHER_CATEGORIES[word]))
    return found


def _frame_rules(text: str, assessment: str, code_prefix: str) -> None:
    """A Stage A description: no absence beyond the frames, and weak evidence stays hedged."""
    rest, absences = _absences(text, FRAME_ABSENCE)
    if absences:
        raise Rejected(f"{code_prefix}_absence")
    for span, _ in _positive(text, rest):
        if assessment == "visible":
            continue
        if assessment in ("possible", "not_assessable") and _near(text, span, HEDGES):
            continue
        raise Rejected(f"{code_prefix}_uncertain_as_fact")


def _limitation(value: str, code_prefix: str) -> str:
    """Limitations cite nothing: absence must name the imagery and weather must be hedged."""
    text = _plain(value, code_prefix)
    _no_numbers(text, code_prefix)
    rest, absences = _absences(text, VISUAL_ABSENCE)
    if absences:
        raise Rejected(f"{code_prefix}_absence")
    if any(not _near(text, span, HEDGES) for span, _ in _positive(text, rest)):
        raise Rejected(f"{code_prefix}_uncertain_as_fact")
    return " ".join(text.split())


def public_observation(assessment: str, description: str) -> bool:
    """Only visible observations, or hedged possible ones, may reach public text."""
    return assessment == "visible" or (
        assessment == "possible" and HEDGES.search(description) is not None
    )


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
        _frame_rules(text, item["assessment"], "visual")
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
    limitations = tuple(_limitation(item, "visual") for item in value["limitations"])
    return VisualResult(tuple(accepted), limitations)


# Stage C ---------------------------------------------------------------------------------------

# Words that tie a number to its metric. Each number binds to the nearest subject before it in
# its clause (else the nearest after it), and that subject must name the number's metric, so a
# true value cited for the wrong quantity, or two values swapped, fails.
SUBJECTS = {
    ("temperature", "min"): (
        "low", "lowest", "minimum", "min", "coolest", "coldest", "bottomed", "range", "ranged",
        "ranging",
    ),
    ("temperature", "max"): (
        "high", "highest", "maximum", "max", "warmest", "hottest", "peak", "peaked", "topped",
        "range", "ranged", "ranging",
    ),
    ("temperature", "mean"): ("average", "mean", "averaged"),
    ("humidity", "mean"): ("humidity",),
    ("wind_avg", "mean"): (
        "average wind", "mean wind", "sustained", "average speed", "wind speed",
    ),
    ("wind_gust", "max"): ("gust", "gusted", "gusting"),
    ("precipitation", "sum"): ("rain", "rainfall", "precipitation"),
    ("station_pressure", "mean"): ("pressure",),
    ("lightning_count", "sum"): ("lightning", "strike"),
}  # fmt: skip
SUBJECT_METRICS: dict[str, frozenset[tuple[str, str]]] = {
    word: frozenset(key for key, words in SUBJECTS.items() if word in words)
    for words in SUBJECTS.values()
    for word in words
}
SUBJECT = re.compile(rf"\b(?:{_words(*SUBJECT_METRICS)})(?:e?s)?\b", re.I)
RANGE_SUBJECTS = frozenset({"range", "ranged", "ranging"})


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


@dataclass(frozen=True)
class Grounding:
    count: int
    subjects: tuple[tuple[int, int], ...]


def _subject(found: str) -> str:
    word = " ".join(found.lower().split())
    return next(w for w in (word, word[:-1], word[:-2]) if w in SUBJECT_METRICS)


def ground(text: str, cited: Sequence[Metric]) -> Grounding:
    """Bind every number to a supplied rendering of a cited metric and its subject word.

    Raises on any number, numeric character or spelled number that is not one of them, and on
    a cited zero total at partial coverage (that is an absence claim the data cannot make)."""
    text = _normalize(text)
    allowed: dict[tuple[str, str], list[Metric]] = {}
    for metric in cited:
        for number, unit in metric.renderings:
            allowed.setdefault((number, _unit(unit)), []).append(metric)
    subjects = [(m.start(), m.end(), _subject(m.group())) for m in SUBJECT.finditer(text)]
    spans: list[tuple[int, int]] = []
    bound: list[tuple[int, int]] = []
    ranges: dict[int, set[str]] = {}
    for found in NUMBER.finditer(text):
        tail = UNIT_TAIL.match(text, found.end())
        if tail is None:
            raise Rejected("ungrounded_number")
        metrics = allowed.get((found.group(), _unit(tail.group(1))))
        if not metrics:
            raise Rejected("ungrounded_number")
        low, high = _clause(text, found.start())
        before = [s for s in subjects if s[0] >= low and s[1] <= found.start()]
        after = [s for s in subjects if s[0] >= tail.end() and s[1] <= high]
        subject = max(before, key=lambda s: s[1]) if before else min(after, default=None)
        if subject is None:
            raise Rejected("irrelevant_number")
        chosen = [m for m in metrics if (m.metric, m.statistic) in SUBJECT_METRICS[subject[2]]]
        if not chosen:
            raise Rejected("irrelevant_number")
        statistics = {m.statistic for m in chosen}
        if subject[2] in RANGE_SUBJECTS:
            # "ranged from <min> to <max>": a minimum after the maximum is a swap.
            seen = ranges.setdefault(subject[0], set())
            if statistics == {"min"} and "max" in seen:
                raise Rejected("irrelevant_number")
            seen |= statistics if len(statistics) == 1 else set()
        if any(
            m.statistic == "sum" and m.value == 0 and m.coverage < FULL_COVERAGE for m in chosen
        ):
            raise Rejected("unsupported_absence")
        spans.append((found.start(), tail.end()))
        bound.append((subject[0], subject[1]))
    for index, ch in enumerate(text):
        if unicodedata.numeric(ch, None) is not None and not any(a <= index < b for a, b in spans):
            raise Rejected("ungrounded_number")
    if NUMBER_WORDS.search(text):
        raise Rejected("ungrounded_number")
    return Grounding(len(spans), tuple(bound))


def numbers_grounded(text: str, cited: Sequence[Metric]) -> int:
    """Count matched (number, unit) renderings; raise on any number that is not one of them."""
    return ground(text, cited).count


def _unit(unit: str) -> str:
    return "strike" if unit.startswith("strike") else unit


def _claim_rules(
    text: str,
    grounding: Grounding,
    ids: Sequence[str],
    packet: Packet,
    cited_visual: Sequence[Observation],
) -> None:
    """Absence needs a cited full-coverage measured zero; weather needs matching evidence."""
    rest, absences = _absences(text, VISUAL_ABSENCE)
    for support in absences:
        metric = packet.find(*support) if support else None
        if (
            metric is None
            or metric.id not in ids
            or metric.value != 0
            or metric.coverage < FULL_COVERAGE
        ):
            raise Rejected("unsupported_absence")
    # A weather word that names a cited measurement ("0.4 mm of rain") is the instrument's.
    for span, categories in _positive(text, _blank(rest, grounding.subjects)):
        support = [o for o in cited_visual if o.category in categories]
        if any(o.assessment == "visible" for o in support):
            continue
        if any(o.assessment == "possible" for o in support):
            if _near(text, span, HEDGES):
                continue
            raise Rejected("uncertain_as_fact")
        # Nothing cited, or only not_observed / not_assessable, which never back a claim.
        raise Rejected("unsupported_weather")
    weak = [o for o in cited_visual if o.assessment in ("possible", "not_assessable")]
    if weak and not HEDGES.search(text):
        raise Rejected("uncertain_as_fact")


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
        grounding = ground(text, cited_metrics)
        if basis == "instrument" and grounding.count == 0:
            raise Rejected("instrument_without_value")
        _claim_rules(text, grounding, ids, packet, cited_visual)
        claims.append(Claim(" ".join(text.split()), basis, tuple(ids)))
    for event in value["events"]:
        # Events are validated for injection but never published from synthesis (#41 owns
        # visual events, derived from observations).
        _plain(event["title"], "claim")
        if any(i not in metrics and i not in seen for i in event["evidence_ids"]):
            raise Rejected("unknown_evidence")
    limitations = tuple(_limitation(item, "claim") for item in value["limitations"])
    return Narrative(tuple(claims), limitations)
