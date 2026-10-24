# SPDX-License-Identifier: MIT
"""Public metric vocabulary and its private source bindings (column names never come from input).

Support kinds:
- ``sample``: an instantaneous reading held from its source time until the next reading,
  capped at twice the expected cadence; longer silence is a gap.
- ``interval``: a reported amount or average over the report interval ending at the source time
  (Tempest v171: 60 s, trimmed so consecutive reports never overlap). Amounts belong to the
  window that contains the end of their support.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from weather.config import Settings

Family = Literal["obs_st", "rapid_wind"]
SupportKind = Literal["sample", "interval"]
# obs_st reports cover the minute ending at the source time (#73).
REPORT_SECONDS = 60
SCALAR = ("mean", "min", "max")
STATISTICS = ("observed", "mean", "min", "max", "sum", "vector_mean", "rose")


class PublicError(ValueError):
    """A sanitized client-visible failure; ``unavailable`` maps to 503, the rest to 400."""

    def __init__(
        self, code: str, message: str, suggested_resolution_seconds: int | None = None
    ) -> None:
        super().__init__(message)
        self.code, self.message = code, message
        self.suggested_resolution_seconds = suggested_resolution_seconds


def unavailable() -> PublicError:
    return PublicError("unavailable", "Weather data is temporarily unavailable.")


@dataclass(frozen=True)
class Source:
    family: Family
    column: str
    support: SupportKind


@dataclass(frozen=True)
class Entry:
    metric: str
    label: str
    unit: str
    statistics: tuple[str, ...]
    source: Source | None
    conversions: tuple[tuple[str, Fraction, Fraction], ...] = ()
    current: bool = True


@dataclass(frozen=True)
class Cadence:
    interval_seconds: float
    hold_seconds: float

    @property
    def cap_seconds(self) -> float:
        """A sample supports at most two expected intervals; beyond that is a gap."""
        return 2 * self.interval_seconds


def cadences(settings: Settings) -> dict[Family, Cadence]:
    return {
        "obs_st": Cadence(settings.api_obs_st_interval_seconds, settings.api_obs_st_hold_seconds),
        "rapid_wind": Cadence(
            settings.api_rapid_wind_interval_seconds, settings.api_rapid_wind_hold_seconds
        ),
    }


DEFAULT_CADENCES: dict[Family, Cadence] = {
    "obs_st": Cadence(60, 150),
    "rapid_wind": Cadence(3, 15),
}

_SPEED = (("km/h", Fraction(18, 5), Fraction(0)), ("mph", Fraction(3125, 1397), Fraction(0)))
_RAIN = (("in", Fraction(5, 127), Fraction(0)),)


def _st(column: str, support: SupportKind = "sample") -> Source:
    return Source("obs_st", column, support)


ENTRIES = (
    Entry(
        "temperature",
        "Temperature",
        "degC",
        ("observed", *SCALAR),
        _st("air_temperature"),
        (("degF", Fraction(9, 5), Fraction(32)),),
    ),
    Entry("humidity", "Humidity", "%", ("observed", *SCALAR), _st("relative_humidity")),
    Entry(
        "wind_lull",
        "Wind lull",
        "m/s",
        ("observed", *SCALAR),
        _st("wind_lull", "interval"),
        _SPEED,
    ),
    Entry(
        "wind_avg",
        "Average wind",
        "m/s",
        ("observed", *SCALAR),
        _st("wind_avg", "interval"),
        _SPEED,
    ),
    Entry(
        "wind_gust",
        "Wind gust",
        "m/s",
        ("observed", "max"),
        _st("wind_gust", "interval"),
        _SPEED,
    ),
    Entry(
        "wind_direction",
        "Wind direction",
        "deg",
        ("observed", "vector_mean", "rose"),
        _st("wind_direction", "interval"),
    ),
    Entry(
        "wind_speed",
        "Wind speed (3 s)",
        "m/s",
        ("observed", *SCALAR),
        Source("rapid_wind", "wind_speed", "sample"),
        _SPEED,
    ),
    Entry(
        "precipitation",
        "Rain per report",
        "mm",
        ("observed", "sum"),
        _st("precip_accumulated", "interval"),
        _RAIN,
        current=False,
    ),
    Entry("rain_last_hour", "Rain, last hour", "mm", (), None, _RAIN),
    Entry("rain_today", "Rain today", "mm", (), None, _RAIN),
    Entry(
        "station_pressure",
        "Station pressure",
        "hPa",
        ("observed", *SCALAR),
        _st("station_pressure"),
        (("inHg", Fraction(10_000_000, 338_638_864), Fraction(0)),),
    ),
    Entry("illuminance", "Illuminance", "lx", ("observed", *SCALAR), _st("illuminance")),
    Entry("uv", "UV index", "index", ("observed", *SCALAR), _st("uv")),
    Entry(
        "irradiance", "Solar irradiance", "W/m2", ("observed", *SCALAR), _st("solar_radiation")
    ),
    Entry(
        "lightning_count",
        "Lightning strikes",
        "1",
        ("observed", "sum"),
        _st("lightning_strike_count", "interval"),
    ),
    Entry(
        "lightning_distance",
        "Lightning distance",
        "km",
        ("observed", "min"),
        _st("lightning_strike_avg_distance", "interval"),
        (("mi", Fraction(15625, 25146), Fraction(0)),),
    ),
)
REGISTRY = {entry.metric: entry for entry in ENTRIES}
# Each family's public view and the columns it exposes (all double precision, gated).
COLUMNS: dict[Family, tuple[str, ...]] = {
    "obs_st": tuple(
        sorted({e.source.column for e in ENTRIES if e.source and e.source.family == "obs_st"})
    ),
    "rapid_wind": ("wind_direction", "wind_speed"),
}


def entry(metric: str) -> Entry:
    found = REGISTRY.get(metric)
    if found is None:
        raise PublicError("unknown_metric", "Unknown public weather metric.")
    return found


def sourced(metric: str) -> tuple[Entry, Source]:
    found = entry(metric)
    if found.source is None:
        raise PublicError(
            "unsupported_statistic", "This derived metric is available only in current conditions."
        )
    return found, found.source


def require_statistic(metric: str, statistic: str) -> None:
    if statistic not in STATISTICS:
        raise PublicError("unsupported_statistic", "Unknown statistic.")
    if statistic not in entry(metric).statistics:
        raise PublicError("unsupported_statistic", "Statistic is unsupported for this metric.")
