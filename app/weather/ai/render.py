# SPDX-License-Identifier: MIT
"""Public text from accepted observations and station aggregates: fixed templates only (C18).

No model-written prose is ever published. Every public sentence, limitation and search text is
rendered here from validated enums (category, assessment, confidence, intensity, sky cover,
limitation codes), window counts and station values formatted by `evidence.py`.

- `visible` with medium or high confidence renders plainly; `possible`, or `visible` with low
  confidence, always renders hedged ("possibly show light rain").
- Absence is worded only where it can be proven: "No rain was visible in the sampled frames in
  all 3 reviewed hour windows" when every reviewed window assessed rain as `not_observed` with
  medium or high confidence, no darkness or infrared limitation was reported, and the station
  measured none; a measured zero rain total only at full station coverage.
- A positive total never prints as zero ("less than 0.1 mm"), and a partial-coverage value
  says so.
- `not_assessable` and the model's limitation codes become fixed limitation sentences.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .evidence import FULL_COVERAGE, Metric, Packet, join
from .validate import Observation

# Bump when any template below changes; it is part of every revision's prompt hash.
RENDER_REVISION = "templates-v2"
MAX_CLAIMS = 16
MAX_LIMITATIONS = 8
CAMERA_FRAMES = {
    "skycam_east": "east-sky",
    "skycam_west": "west-sky",
    "frontyard_1": "front-yard",
}
# Scene categories in the order they are described; lens and artifact codes are limitations.
SCENE = (
    "cloud_cover",
    "cloud_motion",
    "sunlight",
    "rain",
    "snowfall",
    "ground_snow",
    "surface_wetness",
    "fog_visibility",
    "lightning",
)
SKY = {
    "clear": "clear skies",
    "few": "a few clouds",
    "scattered": "scattered clouds",
    "broken": "broken clouds",
    "overcast": "overcast skies",
}
FOG = {"light": "haze", "moderate": "fog", "heavy": "dense fog"}
PLAIN = {
    "cloud_cover": "clouds",
    "cloud_motion": "moving clouds",
    "sunlight": "sunlight and shadows",
    "rain": "rain",
    "snowfall": "falling snow",
    "ground_snow": "snow on the ground",
    "surface_wetness": "wet surfaces",
    "fog_visibility": "fog or reduced visibility",
    "lightning": "lightning",
}
# Categories whose absence from the frames is worth saying, how, and the station metric whose
# positive value would contradict it (then the measured sentence speaks instead).
ABSENT = {
    "rain": "rain",
    "snowfall": "falling snow",
    "fog_visibility": "fog",
    "lightning": "lightning",
}
CONTRADICTED_BY = {
    "rain": ("precipitation", "sum"),
    "snowfall": ("precipitation", "sum"),
    "lightning": ("lightning_count", "sum"),
}
# Limitation codes under which "not visible" proves nothing.
BLIND = frozenset({"darkness", "infrared"})
UNASSESSED = {
    "rain": "Rain",
    "snowfall": "Falling snow",
    "fog_visibility": "Fog and visibility",
    "lightning": "Lightning",
    "cloud_cover": "Cloud cover",
}
LIMITATIONS = {
    "darkness": "Darkness limited what the frames show.",
    "infrared": "Infrared night imaging limited what the frames show.",
    "lens_obstruction": "Droplets or an obstruction on the lens limited the view.",
    "glare": "Glare limited the view.",
    "motion_blur": "Motion blur limited the view.",
    "compression": "Compression artifacts limited the view.",
    "missing_frames": "Some sampled frames showed missing capture.",
    "sparse_sampling": "Sparse sampling limits timing within each hour.",
}
CATEGORY_LIMITATIONS = {
    "lens_condition": "Droplets or an obstruction on the lens limited the view.",
    "camera_artifact": "Camera artifacts limited the view.",
}


def phrase(observation: Observation) -> str:
    """The fixed noun phrase for one observation."""
    category, intensity = observation.category, observation.intensity
    if category == "cloud_cover" and observation.sky_cover:
        return SKY[observation.sky_cover]
    if category == "fog_visibility" and intensity:
        return FOG[intensity]
    if category in ("rain", "snowfall") and intensity:
        return f"{intensity} {PLAIN[category]}"
    return PLAIN[category]


def hedged(observation: Observation) -> bool:
    return observation.assessment == "possible" or (
        observation.assessment == "visible" and observation.confidence == "low"
    )


def shown(observation: Observation) -> bool:
    """Observations that public text may mention as present (hedged or not)."""
    return observation.category in SCENE and observation.assessment in ("visible", "possible")


def plural(count: int, singular: str, many: str | None = None) -> str:
    """'1 strike', '3 strikes'."""
    return f"{count} {singular if count == 1 else many or singular + 's'}"


def _hours(count: int, total: int) -> str:
    if total == 1:
        return "the one reviewed hour window"
    if count == total:
        return "both reviewed hour windows" if total == 2 else f"all {total} reviewed hour windows"
    return f"{count} of {plural(total, 'reviewed hour window')}"


def visual_claims(
    observations: Sequence[Observation],
    reviewed: int,
    camera: str,
    *,
    codes: Mapping[str, int] | None = None,
    packet: Packet | None = None,
) -> list[dict[str, Any]]:
    """One sentence per (category, phrase, hedge), then provable absences, in scene order."""
    frames = f"sampled {CAMERA_FRAMES.get(camera, 'camera')} frames"
    groups: dict[tuple[int, str, bool], list[Observation]] = {}
    for item in observations:
        if shown(item):
            key = (SCENE.index(item.category), phrase(item), hedged(item))
            groups.setdefault(key, []).append(item)
    claims = []
    for (_, noun, hedge), items in sorted(groups.items()):
        count = len({o.window for o in items})
        verb = "possibly show" if hedge else "show"
        claims.append(
            {
                "text": f"The {frames} {verb} {noun} in {_hours(count, reviewed)}.",
                "basis": "visual",
                "evidence_ids": [o.id for o in items][:100],
            }
        )
    if not reviewed or BLIND & set(codes or {}):
        return claims
    for category, noun in ABSENT.items():
        assessed = [o for o in observations if o.category == category]
        absent = {
            o.window
            for o in assessed
            if o.assessment == "not_observed" and o.confidence in ("medium", "high")
        }
        measured = (
            packet.find(*CONTRADICTED_BY[category])
            if packet and category in CONTRADICTED_BY
            else None
        )
        if len(absent) == reviewed == len(assessed) and not (measured and measured.value > 0):
            scope = _hours(reviewed, reviewed)
            claims.append(
                {
                    "text": f"No {noun} was visible in the {frames} in {scope}.",
                    "basis": "visual",
                    "evidence_ids": [o.id for o in assessed][:100],
                }
            )
    return claims


def amount(metric: Metric) -> str:
    """A metric's renderings; a positive value that rounds to zero reads "less than 0.1 mm",
    and a count of one is singular."""
    shown = []
    for number, unit in metric.renderings:
        if metric.value > 0 and float(number) == 0:
            step = "0." + "0" * (len(number.split(".")[1]) - 1) + "1" if "." in number else "1"
            shown.append("less than " + join(step, unit))
        elif unit == "strikes" and number == "1":
            shown.append("1 strike")
        else:
            shown.append(join(number, unit))
    return shown[0] + (f" ({', '.join(shown[1:])})" if len(shown) > 1 else "")


def _partial(*metrics: Metric) -> str:
    return (
        " over partial station coverage" if any(m.coverage < FULL_COVERAGE for m in metrics) else ""
    )


def measured_claims(packet: Packet) -> list[dict[str, Any]]:
    """Deterministic instrument sentences; every number is the station's, formatted by code,
    and qualified when the station's coverage was partial."""
    claims: list[tuple[str, list[Metric]]] = []
    low, high = packet.find("temperature", "min"), packet.find("temperature", "max")
    if low and high:
        claims.append(
            (
                f"Measured temperature ranged from {amount(low)} to {amount(high)}"
                f"{_partial(low, high)}.",
                [low, high],
            )
        )
    wind = packet.find("wind_avg", "mean")
    if wind:
        claims.append((f"The mean measured wind was {amount(wind)}{_partial(wind)}.", [wind]))
    gust = packet.find("wind_gust", "max")
    if gust:
        claims.append(
            (f"The strongest measured wind gust was {amount(gust)}{_partial(gust)}.", [gust])
        )
    rain = packet.find("precipitation", "sum")
    # A zero is an absence claim, made only at full coverage; a partial total is a lower bound.
    if rain and (rain.value > 0 or rain.coverage >= FULL_COVERAGE):
        claims.append((f"Measured rain total: {amount(rain)}{_partial(rain)}.", [rain]))
    strikes = packet.find("lightning_count", "sum")
    if strikes and strikes.value > 0:
        claims.append(
            (f"The station counted {amount(strikes)} of lightning{_partial(strikes)}.", [strikes])
        )
    return [
        {"text": text, "basis": "instrument", "evidence_ids": [m.id for m in cited]}
        for text, cited in claims
    ]


def limitations(
    observations: Sequence[Observation],
    reviewed: int,
    codes: Mapping[str, int],
    *,
    keogram: bool,
    unreviewed: bool,
    withheld: Sequence[str],
) -> list[str]:
    """Fixed limitation sentences, at most eight, most consequential first."""
    out = []
    if unreviewed:
        out.append("Some hours could not be reviewed visually.")
    if not keogram:
        out.append("No keogram was available; hours were described from frames.")
    if withheld:
        out.append("Station data was incomplete for: " + ", ".join(withheld) + ".")
    for category, noun in UNASSESSED.items():
        windows = {
            o.window
            for o in observations
            if o.category == category and o.assessment == "not_assessable"
        }
        if windows:
            out.append(f"{noun} could not be assessed in {_hours(len(windows), reviewed)}.")
    for category, sentence in CATEGORY_LIMITATIONS.items():
        if any(
            o.category == category and o.assessment in ("visible", "possible") for o in observations
        ):
            out.append(sentence)
    out.extend(LIMITATIONS[code] for code in sorted(codes) if code in LIMITATIONS)
    unique = list(dict.fromkeys(out))
    return unique[:MAX_LIMITATIONS]


def window_text(observations: Sequence[Observation]) -> str | None:
    """One hour window's search text: present scene, hedged notes and frame-scoped absences."""
    parts = []
    for item in sorted(
        (o for o in observations if shown(o)), key=lambda o: SCENE.index(o.category)
    ):
        parts.append(f"possibly {phrase(item)}" if hedged(item) else phrase(item))
    for item in observations:
        if item.category in ABSENT and item.assessment == "not_observed":
            parts.append(f"no {ABSENT[item.category]} visible in the frames")
    if not parts:
        return None
    text = "; ".join(dict.fromkeys(parts)) + "."
    return text[0].upper() + text[1:]


def claims(
    packet: Packet,
    observations: Sequence[Observation],
    reviewed: int,
    camera: str,
    codes: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """The published claims: visual sentences, then measured ones, at most sixteen."""
    measured = measured_claims(packet)
    visual = visual_claims(observations, reviewed, camera, codes=codes, packet=packet)
    visual = visual[: MAX_CLAIMS - len(measured)]
    return [*visual, *measured]
