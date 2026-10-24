# SPDX-License-Identifier: MIT
"""Canonical public vocabulary. Protocol bindings are private implementation metadata.

Cadence, rain support, hold caps and lightning authority require commissioning. A registry
entry advertises calculation vocabulary; it does not qualify source support or fabricate data.
"""

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import Field, model_validator

from .public_common import MetricID, Number, OpaqueID, Positive, PublicModel, Unit


def _revision(prefix: str, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()


class PublicInputError(ValueError):
    def __init__(
        self, code: str, message: str, suggested_resolution_seconds: int | None = None
    ) -> None:
        self.code, self.message = code, message
        self.suggested_resolution_seconds = suggested_resolution_seconds
        super().__init__(message)


class Conversion(PublicModel):
    """display = canonical * scale_numerator / scale_denominator + offset ratio."""

    unit: Unit
    scale_numerator: Annotated[int, Field(strict=True)]
    scale_denominator: Annotated[int, Field(gt=0, strict=True)]
    offset_numerator: Annotated[int, Field(strict=True)]
    offset_denominator: Annotated[int, Field(gt=0, strict=True)]


class MetricDefinition(PublicModel):
    metric: MetricID
    label: Annotated[str, Field(min_length=1, max_length=128)]
    canonical_unit: Unit
    display_units: Annotated[tuple[Unit, ...], Field(min_length=1, max_length=16)]
    conversions: Annotated[tuple[Conversion, ...], Field(min_length=1, max_length=16)]
    statistics: Annotated[tuple[MetricID, ...], Field(min_length=1, max_length=16)]
    expected_interval_seconds: Positive | None
    public: Literal[True] = True
    revision: OpaqueID

    @model_validator(mode="after")
    def unique_conversion_units(self) -> MetricDefinition:
        units = tuple(item.unit for item in self.conversions)
        if len(set(units)) != len(units) or units != self.display_units:
            raise ValueError("Display units must name exactly the explicit conversions")
        if self.canonical_unit not in units or len(set(self.statistics)) != len(self.statistics):
            raise ValueError("Canonical units and unique statistics are required")
        identity = next(item for item in self.conversions if item.unit == self.canonical_unit)
        if identity.scale_numerator != identity.scale_denominator or identity.offset_numerator != 0:
            raise ValueError("The canonical unit conversion must be the identity")
        return self


class MetricRegistry(PublicModel):
    revision: OpaqueID
    metrics: Annotated[tuple[MetricDefinition, ...], Field(max_length=256)]


@dataclass(frozen=True)
class SourceBinding:
    """Only numeric safe-view fields; never interpolate these from request input."""

    families: tuple[str, ...]
    field: str


@dataclass(frozen=True)
class RegistryEntry:
    definition: MetricDefinition
    source: SourceBinding | None


def _conversion(
    unit: str, scale: Fraction = Fraction(1), offset: Fraction = Fraction()
) -> Conversion:
    return Conversion(
        unit=unit,
        scale_numerator=scale.numerator,
        scale_denominator=scale.denominator,
        offset_numerator=offset.numerator,
        offset_denominator=offset.denominator,
    )


def _entry(
    metric: str,
    label: str,
    unit: str,
    statistics: tuple[str, ...],
    field: str | None,
    families: tuple[str, ...] = ("obs_st",),
    extra: tuple[Conversion, ...] = (),
) -> RegistryEntry:
    conversions = (_conversion(unit), *extra)
    definition = MetricDefinition(
        metric=metric,
        label=label,
        canonical_unit=unit,
        display_units=tuple(item.unit for item in conversions),
        conversions=conversions,
        statistics=("observed", *statistics),
        expected_interval_seconds=None,
        revision=f"{metric}_v1",
    )
    definition = MetricDefinition.model_validate(
        {
            **definition.model_dump(),
            "revision": _revision(
                "metric_v1_", definition.model_dump(mode="json", exclude={"revision"})
            ),
        }
    )
    return RegistryEntry(definition, SourceBinding(families, field) if field else None)


_SCALAR = ("mean", "min", "max")
_SPEED = (_conversion("km/h", Fraction(18, 5)), _conversion("mph", Fraction(3125, 1397)))
_RAIN = (_conversion("in", Fraction(5, 127)),)
_ENTRIES = (
    _entry(
        "temperature",
        "Temperature",
        "degC",
        _SCALAR,
        "air_temperature",
        ("obs_st", "obs_air"),
        (_conversion("degF", Fraction(9, 5), Fraction(32)),),
    ),
    _entry("humidity", "Humidity", "%", _SCALAR, "relative_humidity", ("obs_st", "obs_air")),
    _entry("wind_lull", "Wind lull", "m/s", _SCALAR, "wind_lull", ("obs_st", "obs_sky"), _SPEED),
    _entry(
        "wind_avg",
        "Mean reported wind speed",
        "m/s",
        _SCALAR,
        "wind_avg",
        ("obs_st", "obs_sky"),
        _SPEED,
    ),
    _entry("wind_speed", "Rapid wind speed", "m/s", _SCALAR, "wind_speed", ("rapid_wind",), _SPEED),
    _entry(
        "wind_gust",
        "Reported wind gust",
        "m/s",
        ("max",),
        "wind_gust",
        ("obs_st", "obs_sky"),
        _SPEED,
    ),
    _entry(
        "wind_direction",
        "Wind from direction",
        "deg",
        ("vector_mean",),
        "wind_direction",
        ("obs_st", "obs_sky", "rapid_wind"),
    ),
    _entry(
        "precipitation",
        "Observed precipitation amount",
        "mm",
        ("observed_accumulation",),
        "precip_accumulated",
        ("obs_st", "obs_sky"),
        _RAIN,
    ),
    _entry("rain_last_hour", "Observed precipitation, last hour", "mm", (), None, extra=_RAIN),
    _entry("rain_today", "Observed precipitation, local weather day", "mm", (), None, extra=_RAIN),
    _entry(
        "station_pressure",
        "Station pressure",
        "hPa",
        (*_SCALAR, "trend"),
        "station_pressure",
        ("obs_st", "obs_air"),
        (_conversion("inHg", Fraction(10_000_000, 338_638_864)),),
    ),
    _entry("illuminance", "Illuminance", "lx", _SCALAR, "illuminance", ("obs_st", "obs_sky")),
    _entry("uv", "UV index", "index", _SCALAR, "uv", ("obs_st", "obs_sky")),
    _entry(
        "irradiance",
        "Solar irradiance",
        "W/m2",
        (*_SCALAR, "solar_energy"),
        "solar_radiation",
        ("obs_st", "obs_sky"),
    ),
    _entry(
        "lightning_count",
        "Reported lightning count",
        "1",
        ("count",),
        "lightning_strike_count",
        ("obs_st", "obs_air"),
    ),
    _entry(
        "lightning_distance",
        "Reported average lightning distance",
        "km",
        (),
        "lightning_strike_avg_distance",
        ("obs_st", "obs_air"),
        (_conversion("mi", Fraction(15625, 25146)),),
    ),
)
REGISTRY = {entry.definition.metric: entry for entry in _ENTRIES}
REGISTRY_REVISION = _revision(
    "registry_v1_", [entry.definition.model_dump(mode="json") for entry in _ENTRIES]
)


def metric_definition(metric: str) -> MetricDefinition:
    entry = REGISTRY.get(metric)
    if entry is None:
        raise PublicInputError("unknown_metric", "Unknown public weather metric.")
    return entry.definition


def validate_selection(metrics: list[str] | tuple[str, ...], statistic: str = "observed") -> None:
    if not 1 <= len(metrics) <= 32 or len(set(metrics)) != len(metrics):
        raise PublicInputError(
            "invalid_request", "Select between one and 32 unique public metrics."
        )
    for metric in metrics:
        if statistic not in metric_definition(metric).statistics:
            raise PublicInputError(
                "unsupported_statistic", "Statistic is unsupported for a metric."
            )


def convert(metric: str, value: Number, unit: str) -> Fraction:
    definition = metric_definition(metric)
    conversion = next((item for item in definition.conversions if item.unit == unit), None)
    if conversion is None:
        raise PublicInputError("unsupported_unit", "Unsupported public display unit.")
    if isinstance(value, bool):
        raise ValueError("Measured zero must not be a boolean")
    return Fraction(str(value)) * Fraction(
        conversion.scale_numerator, conversion.scale_denominator
    ) + Fraction(conversion.offset_numerator, conversion.offset_denominator)


def discovery() -> MetricRegistry:
    return MetricRegistry(
        revision=REGISTRY_REVISION, metrics=tuple(entry.definition for entry in _ENTRIES)
    )


router = APIRouter()


@router.get(
    "/api/v1/weather/metrics", response_model=MetricRegistry, operation_id="getMetricRegistry"
)
def get_metric_registry() -> MetricRegistry:
    return discovery()
