# SPDX-License-Identifier: MIT
"""The observed-exposure recipe: every value that changes output math is part of its identity.

D10 v1 defaults: synthetic shutter 180 degrees (270 and 360 are selectable comparison recipes),
minimum exposure coverage 0.90 inclusive, slot coverage 99/90/50. v2 (#144) lowers the
exposure threshold to 0.25: the accumulator already normalizes by weight, so a frame with
partial coverage is a correct exposure, not a slate. Changing any value makes a new recipe id
(`ox1-...` for v1, `ox2-...` for v2), so a segment rendered under one recipe never mixes blocks
from another; `Recipe.from_id` lets a started segment finish under the recipe it began with.
The block size is a resource policy, not output math, and is deliberately not in the id.
"""

import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

RECIPE = "observed-exposure/v{version}"
VERSION = 2
# The exposure threshold each version's unsuffixed id means.
DEFAULT_EXPOSURE = {1: Fraction(9, 10), 2: Fraction(1, 4)}
_FRACTION = r"([0-9]+o[0-9]+)"
_ID = re.compile(rf"ox([12])-t(180|270|360)(?:-c{_FRACTION}-{_FRACTION}-{_FRACTION}-{_FRACTION})?")
SUPPORT_RULE = "midpoint-capped-half-interval/v1"
# Versioned "capture unavailable" treatment: a flat dark frame at the gap's own PTS.
SLATE = "flat-srgb-24/v1"
SLATE_RGB = (24, 24, 24)
MEZZANINE = "libx264rgb-intra-qp12/v1"
SHUTTERS = (180, 270, 360)
MAX_BLOCK = 60
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
# The commissioned capture cadence (#4: 0.5 Hz) and the collector's per-camera phases
# (capture.config.CaptureConfig.load); the pipeline must agree with capture.json.
DEFAULT_CADENCE_MS = 2000
DEFAULT_PHASE_MS = {"skycam_east": 0, "skycam_west": 333, "frontyard_1": 667}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class Recipe:
    shutter_degrees: int = 180
    min_exposure_coverage: Fraction = DEFAULT_EXPOSURE[VERSION]
    slot_normal: Fraction = Fraction(99, 100)
    slot_partial: Fraction = Fraction(9, 10)
    slot_available: Fraction = Fraction(1, 2)
    version: int = VERSION
    # Encoder provenance; filled from the running image, not part of the output math.
    encoder: str = field(default="unknown", compare=False)
    image: str = field(default="unpinned", compare=False)

    def __post_init__(self) -> None:
        if self.version not in DEFAULT_EXPOSURE:
            raise ValueError("Unknown recipe version")
        if self.shutter_degrees not in SHUTTERS:
            raise ValueError("Unsupported shutter angle")
        if not 0 < self.min_exposure_coverage <= 1:
            raise ValueError("Invalid exposure threshold")
        if not 0 < self.slot_available <= self.slot_partial <= self.slot_normal <= 1:
            raise ValueError("Invalid slot coverage thresholds")

    @property
    def id(self) -> str:
        """Directory-safe identity of the output math."""
        default = Recipe(min_exposure_coverage=DEFAULT_EXPOSURE[self.version], version=self.version)
        suffix = f"t{self.shutter_degrees}"
        if (
            self.min_exposure_coverage,
            self.slot_normal,
            self.slot_partial,
            self.slot_available,
        ) != (
            default.min_exposure_coverage,
            default.slot_normal,
            default.slot_partial,
            default.slot_available,
        ):
            suffix += "-c" + "-".join(
                f"{value.numerator}o{value.denominator}"
                for value in (
                    self.min_exposure_coverage,
                    self.slot_normal,
                    self.slot_partial,
                    self.slot_available,
                )
            )
        return f"ox{self.version}-{suffix}"

    @classmethod
    def from_id(cls, recipe_id: str, base: Recipe) -> Recipe:
        """The recipe a stored id names (a started segment keeps it), with `base`'s
        encoder and image provenance."""
        match = _ID.fullmatch(recipe_id)
        if match is None:
            raise ValueError("Unknown recipe id")
        version, shutter = int(match[1]), int(match[2])
        if match[3] is None:
            exposure = DEFAULT_EXPOSURE[version]
            normal, partial, available = cls.slot_normal, cls.slot_partial, cls.slot_available
        else:
            exposure, normal, partial, available = (
                Fraction(*map(int, value.split("o"))) for value in match.groups()[2:]
            )
        recipe = cls(
            shutter_degrees=shutter,
            min_exposure_coverage=exposure,
            slot_normal=normal,
            slot_partial=partial,
            slot_available=available,
            version=version,
            encoder=base.encoder,
            image=base.image,
        )
        if recipe.id != recipe_id:
            raise ValueError("Recipe id does not round-trip")
        return recipe

    def record(self) -> dict[str, Any]:
        """Public provenance: recorded in RenderResult and the sidecar."""
        return {
            "recipe": RECIPE.format(version=self.version),
            "recipe_id": self.id,
            "shutter_degrees": self.shutter_degrees,
            "exposure_threshold": str(self.min_exposure_coverage),
            "slot_thresholds": {
                "normal": str(self.slot_normal),
                "partial": str(self.slot_partial),
                "available": str(self.slot_available),
            },
            "support_rule": SUPPORT_RULE,
            "light": "srgb-linear-float32/v1",
            "slate": SLATE,
            "mezzanine": MEZZANINE,
            "encoder": self.encoder,
            "image": self.image,
        }


def from_environment(environ: dict[str, str] | None = None) -> Recipe:
    env = dict(os.environ if environ is None else environ)
    try:
        shutter = int(env.get("WEATHER_PIPELINE_SHUTTER_DEGREES", "180"))
    except ValueError:
        raise ValueError("Invalid shutter angle") from None
    image = env.get("WEATHER_IMAGE_DIGEST", "unpinned")
    if image != "unpinned" and not DIGEST.fullmatch(image):
        raise ValueError("Invalid image digest")
    return Recipe(shutter_degrees=shutter, image=image)
