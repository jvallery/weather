# SPDX-License-Identifier: MIT
"""Private observer configuration and explicitly packaged Denver timezone rules."""

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

import astral

# The pinned wheel contains timezone data and version constants, without py.typed.
import tzdata  # type: ignore[import-untyped]

from .errors import ScheduleError

TIMEZONE = "America/Denver"
TZDATA_VERSION: str = tzdata.__version__
IANA_VERSION: str = tzdata.IANA_VERSION
HORIZON_MODEL = "standard-unobstructed-solar-centre-civil-6deg/v1"
# A commissioned dawn/dusk correction is a local horizon tweak, never a different day.
MAX_OFFSET_SECONDS = 3600


@cache
def site_timezone() -> ZoneInfo:
    """Load the pinned wheel's TZif, bypassing system TZPATH without changing it."""
    with files("tzdata.zoneinfo").joinpath("America", "Denver").open("rb") as source:
        return ZoneInfo.from_file(source, key=TIMEZONE)


def _number(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ScheduleError("Invalid private site number")
    try:
        number = float(value)
    except ValueError, OverflowError:
        raise ScheduleError("Invalid private site number") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ScheduleError("Private site number outside its allowed range")
    return number if number else 0.0


def _integer(value: object) -> int:
    number: int | None = None
    if type(value) is int:
        number = value
    elif isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            pass
    if number is None or not -MAX_OFFSET_SECONDS <= number <= MAX_OFFSET_SECONDS:
        raise ScheduleError("Invalid private site offset")
    return number


@dataclass(frozen=True, repr=False)
class SiteConfig:
    """Private input. Its representation deliberately contains no field values."""

    latitude: float
    longitude: float
    timezone: str = TIMEZONE
    dawn_offset_seconds: int = 0
    dusk_offset_seconds: int = 0
    elevation_meters: float = 0.0
    horizon_model: str = HORIZON_MODEL

    def __post_init__(self) -> None:
        object.__setattr__(self, "latitude", _number(self.latitude, minimum=-90, maximum=90))
        object.__setattr__(self, "longitude", _number(self.longitude, minimum=-180, maximum=180))
        object.__setattr__(
            self,
            "elevation_meters",
            _number(self.elevation_meters, minimum=-math.inf, maximum=math.inf),
        )
        for field in ("dawn_offset_seconds", "dusk_offset_seconds"):
            object.__setattr__(self, field, _integer(getattr(self, field)))
        if self.timezone != TIMEZONE:
            raise ScheduleError("Unsupported site timezone")
        if not isinstance(self.horizon_model, str) or not self.horizon_model:
            raise ScheduleError("Invalid horizon model")

    def __repr__(self) -> str:
        return "SiteConfig(<private>)"

    @property
    def config_hash(self) -> str:
        """SHA256 of sorted compact UTF-8 JSON of effective config and model pins."""
        payload = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": self.timezone,
            "dawn_offset_seconds": self.dawn_offset_seconds,
            "dusk_offset_seconds": self.dusk_offset_seconds,
            "elevation_meters": self.elevation_meters,
            "horizon_model": self.horizon_model,
            "astral_version": astral.__version__,
            "tzdata_version": TZDATA_VERSION,
            "iana_version": IANA_VERSION,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> Self:
        allowed = {
            "latitude",
            "longitude",
            "timezone",
            "dawn_offset_seconds",
            "dusk_offset_seconds",
            "elevation_meters",
            "horizon_model",
        }
        if set(values) - allowed or not {"latitude", "longitude"} <= values.keys():
            raise ScheduleError("Invalid private site configuration fields")
        timezone, horizon = (
            values.get("timezone", TIMEZONE),
            values.get("horizon_model", HORIZON_MODEL),
        )
        if not isinstance(timezone, str) or not isinstance(horizon, str):
            raise ScheduleError("Invalid private site model")
        return cls(
            latitude=_number(values["latitude"], minimum=-90, maximum=90),
            longitude=_number(values["longitude"], minimum=-180, maximum=180),
            timezone=timezone,
            dawn_offset_seconds=_integer(values.get("dawn_offset_seconds", 0)),
            dusk_offset_seconds=_integer(values.get("dusk_offset_seconds", 0)),
            elevation_meters=_number(
                values.get("elevation_meters", 0),
                minimum=-math.inf,
                maximum=math.inf,
            ),
            horizon_model=horizon,
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ScheduleError("Duplicate private site configuration field")
        values[key] = value
    return values


def load_site(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> SiteConfig:
    """Read one JSON file, or WEATHER_SITE_* env values; invalid files never fall back."""
    env = os.environ if environ is None else environ
    configured_path = path if path is not None else env.get("WEATHER_SITE_FILE")
    if configured_path is not None:
        try:
            values = json.loads(
                Path(configured_path).read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
            )
        except OSError, ValueError:
            raise ScheduleError("Cannot load private site configuration") from None
        if not isinstance(values, dict):
            raise ScheduleError("Private site configuration must be an object")
        return SiteConfig.from_mapping(values)
    fields = (
        "latitude",
        "longitude",
        "timezone",
        "dawn_offset_seconds",
        "dusk_offset_seconds",
        "elevation_meters",
        "horizon_model",
    )
    return SiteConfig.from_mapping(
        {
            field: env["WEATHER_SITE_" + field.upper()]
            for field in fields
            if "WEATHER_SITE_" + field.upper() in env
        }
    )
