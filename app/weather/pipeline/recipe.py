# SPDX-License-Identifier: MIT
"""The observed-exposure recipe: every value that changes output math is part of its identity.

D10 v1 defaults: synthetic shutter 180 degrees (270 and 360 are selectable comparison recipes),
minimum exposure coverage 0.90 inclusive, slot coverage 99/90/50. Changing any of these makes
a new recipe id, so a segment rendered under one recipe never mixes blocks from another. The
block size is a resource policy, not output math, and is deliberately not in the id.
"""

import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

RECIPE = "observed-exposure/v1"
SUPPORT_RULE = "midpoint-capped-half-interval/v1"
# Versioned "capture unavailable" treatment: a flat dark frame at the gap's own PTS.
SLATE = "flat-srgb-24/v1"
SLATE_RGB = (24, 24, 24)
MEZZANINE = "libx264rgb-intra-qp12/v1"
SHUTTERS = (180, 270, 360)
MAX_BLOCK = 60
CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
# Collector defaults (capture.config.CaptureConfig.load); the pipeline must agree with them.
DEFAULT_PHASE_MS = {"skycam_east": 0, "skycam_west": 333, "frontyard_1": 667}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class Recipe:
    shutter_degrees: int = 180
    min_exposure_coverage: Fraction = Fraction(9, 10)
    slot_normal: Fraction = Fraction(99, 100)
    slot_partial: Fraction = Fraction(9, 10)
    slot_available: Fraction = Fraction(1, 2)
    # Encoder provenance; filled from the running image, not part of the output math.
    encoder: str = field(default="unknown", compare=False)
    image: str = field(default="unpinned", compare=False)

    def __post_init__(self) -> None:
        if self.shutter_degrees not in SHUTTERS:
            raise ValueError("Unsupported shutter angle")
        if not 0 < self.min_exposure_coverage <= 1:
            raise ValueError("Invalid exposure threshold")
        if not 0 < self.slot_available <= self.slot_partial <= self.slot_normal <= 1:
            raise ValueError("Invalid slot coverage thresholds")

    @property
    def id(self) -> str:
        """Directory-safe identity of the output math."""
        default = Recipe()
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
        return f"ox1-{suffix}"

    def record(self) -> dict[str, Any]:
        """Public provenance: recorded in RenderResult and the sidecar."""
        return {
            "recipe": RECIPE,
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
