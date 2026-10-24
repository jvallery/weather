# SPDX-License-Identifier: MIT
"""WeatherFlow UDP v171 field mappings; no packet, identity or endpoint logging.

Primary protocol: https://weatherflow.github.io/Tempest/api/udp/v171/
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

PARSER_REVISION = "tempest-v171-1"
FAMILIES = frozenset(
    {
        "obs_st",
        "rapid_wind",
        "evt_precip",
        "evt_strike",
        "device_status",
        "hub_status",
        "obs_air",
        "obs_sky",
    }
)
MISSING = object()


class DuplicateKey(ValueError):
    pass


# Real Tempest datagrams are 100-700 B; larger ones are rejected before the spool.
MAX_DATAGRAM_BYTES = 4096
# v171 obs_st/obs_sky rain is the amount over the minute ending at the source timestamp.
RAIN_SUPPORT_SECONDS = 60
Validity = Literal["valid", "null", "absent", "invalid"]


def json_text(value: Any) -> str:
    """Keep finite protocol decimal tokens as numbers without binary-float rounding."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite private JSON number")
        return str(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" + json_text(value[key])
                for key in sorted(value)
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(json_text(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True)
class FieldSpec:
    name: str
    reported_unit: str | None
    canonical_unit: str | None = None
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None
    integer: bool = False
    choices: frozenset[int] | None = None
    text: bool = False


def number(
    name: str,
    unit: str | None,
    minimum: Decimal | int | None = None,
    maximum: Decimal | int | None = None,
    *,
    integer: bool = False,
    choices: frozenset[int] | None = None,
) -> FieldSpec:
    return FieldSpec(
        name, unit, "hPa" if unit == "mb" else unit, minimum, maximum, integer, choices
    )


WIND_LULL = number("wind_lull", "m/s", 0)
WIND_AVG = number("wind_avg", "m/s", 0)
WIND_GUST = number("wind_gust", "m/s", 0)
DIRECTION = number("wind_direction", "deg", 0, 360)
WIND_INTERVAL = number("wind_sample_interval", "s", 1, integer=True)
PRESSURE = number("station_pressure", "mb", 0)
TEMPERATURE = number("air_temperature", "degC", Decimal("-273.15"))
HUMIDITY = number("relative_humidity", "%", 0, 100)
LIGHT = number("illuminance", "lx", 0)
UV = number("uv", "index", 0)
SOLAR = number("solar_radiation", "W/m2", 0)
RAIN = number("precip_accumulated", "mm", 0)
DISTANCE = number("lightning_strike_avg_distance", "km", 0)
STRIKES = number("lightning_strike_count", "1", 0, integer=True)
BATTERY = number("battery", "V", 0)
REPORT = number("report_interval", "min", Decimal("0.000001"))
PRECIP_ST = number("precipitation_type", "1", integer=True, choices=frozenset({0, 1, 2, 3}))
PRECIP_SKY = number("precipitation_type", "1", integer=True, choices=frozenset({0, 1, 2}))

# Positions start after source timestamp at array index zero. Legacy order is different.
ARRAY_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "obs_st": (
        WIND_LULL,
        WIND_AVG,
        WIND_GUST,
        DIRECTION,
        WIND_INTERVAL,
        PRESSURE,
        TEMPERATURE,
        HUMIDITY,
        LIGHT,
        UV,
        SOLAR,
        RAIN,
        PRECIP_ST,
        DISTANCE,
        STRIKES,
        BATTERY,
        REPORT,
    ),
    "rapid_wind": (number("wind_speed", "m/s", 0), DIRECTION),
    "evt_precip": (),
    "evt_strike": (number("distance", "km", 0), number("energy", None, 0)),
    "obs_air": (
        PRESSURE,
        TEMPERATURE,
        HUMIDITY,
        STRIKES,
        DISTANCE,
        number("battery", None, 0),
        REPORT,  # v171 AIR battery unit is unspecified.
    ),
    "obs_sky": (
        LIGHT,
        UV,
        RAIN,
        WIND_LULL,
        WIND_AVG,
        WIND_GUST,
        DIRECTION,
        BATTERY,
        REPORT,
        SOLAR,
        number("local_day_rain_accumulation", "mm", 0),
        PRECIP_SKY,
        WIND_INTERVAL,
    ),
}
STATUS_FIELDS = {
    "device_status": (
        number("uptime", "s", 0, integer=True),
        number("voltage", None, 0),
        FieldSpec("firmware_revision", None, text=True),
        number("rssi", "dBm"),
        number("hub_rssi", "dBm"),
        number("sensor_status", "1", 0, integer=True),
        number("debug", "1", integer=True, choices=frozenset({0, 1})),
    ),
    "hub_status": (
        FieldSpec("firmware_revision", None, text=True),
        number("uptime", "s", 0, integer=True),
        number("rssi", "dBm"),
        FieldSpec("reset_flags", None, text=True),
        number("seq", "1", 0, integer=True),
    ),
}


@dataclass(frozen=True)
class FieldValue:
    state: Validity
    reported: Any = field(repr=False)
    canonical: Any = field(repr=False)
    reported_unit: str | None
    canonical_unit: str | None
    reason: str


def _field(value: Any, spec: FieldSpec) -> FieldValue:
    def result(state: Validity, canonical: Any, reason: str) -> FieldValue:
        return FieldValue(
            state,
            None if value is MISSING else value,
            canonical,
            spec.reported_unit,
            spec.canonical_unit,
            reason,
        )

    if value is MISSING:
        return result("absent", None, "absent")
    if value is None:
        return result("null", None, "null")
    if spec.text:
        if (
            isinstance(value, str)
            and not any(ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
            or isinstance(value, (int, Decimal))
            and not isinstance(value, bool)
            and Decimal(value).is_finite()
            and Decimal(value) == Decimal(value).to_integral_value()
            and abs(Decimal(value).adjusted()) <= 127
        ):
            return result("valid", value, "valid")
        return result("invalid", None, "invalid_type")
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return result("invalid", None, "invalid_type")
    try:
        numeric = Decimal(str(value))
        finite = numeric.is_finite()
    except ValueError, OverflowError:
        finite = False
    if not finite:
        return result("invalid", None, "non_finite")
    if not numeric.is_zero() and (
        abs(numeric.adjusted()) > 127 or len(numeric.as_tuple().digits) > 128
    ):
        return result("invalid", None, "numeric_storage_bound")
    if spec.integer and numeric != numeric.to_integral_value():
        return result("invalid", None, "non_integer")
    if (
        (spec.minimum is not None and numeric < spec.minimum)
        or (spec.maximum is not None and numeric > spec.maximum)
        or (spec.choices is not None and numeric not in spec.choices)
    ):
        return result("invalid", None, "out_of_range")
    canonical = int(numeric) if spec.integer else (Decimal(0) if numeric.is_zero() else value)
    return result("valid", canonical, "valid")


def _epoch(value: Any) -> Decimal | None:
    result = _field(value, number("source_time", "s", 0, 253402300799))
    return Decimal(str(result.canonical)) if result.state == "valid" else None


@dataclass(frozen=True)
class Row:
    family: str
    ordinal: int
    source_ts: Decimal | None
    fields: dict[str, FieldValue]
    future: bool = False
    quarantine_reason: str | None = None

    @property
    def values(self) -> dict[str, Any]:
        return {name: value.canonical for name, value in self.fields.items()}

    @property
    def quality(self) -> dict[str, Any]:
        """Compact row status; units, ranges and support are fixed by this parser revision."""
        return {
            "valid": self.quarantine_reason is None and not self.future,
            "reason": self.quarantine_reason or ("future_timestamp" if self.future else "valid"),
            "invalid": sorted(name for name, f in self.fields.items() if f.state == "invalid"),
        }

    def support(self, name: str) -> tuple[Decimal, Decimal] | None:
        """Reported support (start, end] in epoch seconds; None is an instantaneous sample."""
        value = self.fields.get(name)
        if self.source_ts is None or value is None or value.state != "valid":
            return None
        if name == "precip_accumulated":
            seconds = Decimal(RAIN_SUPPORT_SECONDS)
        elif name == "wind_avg" and self.fields["report_interval"].state == "valid":
            seconds = Decimal(str(self.fields["report_interval"].canonical)) * 60
        else:
            return None
        return self.source_ts - seconds, self.source_ts


@dataclass(frozen=True)
class Packet:
    family: str
    state: Literal["parsed", "unsupported", "quarantined"]
    envelope: dict[str, Any] | None = field(repr=False)
    rows: tuple[Row, ...] = field(repr=False)
    reason: str | None = None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateKey("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("non_json_number")


def decode_envelope(raw: bytes) -> Packet:
    if len(raw) > MAX_DATAGRAM_BYTES:
        return Packet("unknown", "quarantined", None, (), "oversized")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        return Packet("unknown", "quarantined", None, (), "malformed_utf8")
    try:
        envelope = json.loads(
            text,
            object_pairs_hook=_unique,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_constant,
        )
    except DuplicateKey:
        return Packet("unknown", "quarantined", None, (), "duplicate_json_key")
    except ValueError, RecursionError:
        return Packet("unknown", "quarantined", None, (), "malformed_json")
    if not isinstance(envelope, dict):
        return Packet("unknown", "quarantined", None, (), "invalid_envelope")
    # Bound traversal after decoding; exact raw custody does not depend on normalization success.
    pending = [(envelope, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            return Packet("unknown", "quarantined", None, (), "nesting_limit")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    family = envelope.get("type")
    if not isinstance(family, str) or family not in FAMILIES:
        return Packet("unknown", "unsupported", envelope, ())
    return Packet(family, "parsed", envelope, ())


def parse_packet(raw: bytes, received_epoch: Decimal, future_seconds: int = 120) -> Packet:
    decoded = decode_envelope(raw)
    if decoded.state != "parsed":
        return decoded
    family, envelope = decoded.family, decoded.envelope
    assert envelope is not None
    if family.startswith("obs_"):
        raw_rows = envelope.get("obs")
        if not isinstance(raw_rows, list) or not raw_rows:
            raw_rows = [MISSING]
    elif family in {"device_status", "hub_status"}:
        raw_rows = [envelope]
    else:
        raw_rows = [envelope.get("ob" if family == "rapid_wind" else "evt", MISSING)]
    rows = []
    for ordinal, raw_row in enumerate(raw_rows):
        is_status = family in STATUS_FIELDS
        if (is_status and not isinstance(raw_row, dict)) or (
            not is_status and not isinstance(raw_row, list)
        ):
            rows.append(Row(family, ordinal, None, {}, quarantine_reason="invalid_row"))
            continue
        timestamp = (
            raw_row.get("timestamp", MISSING) if is_status else (raw_row[0] if raw_row else MISSING)
        )
        source_ts = _epoch(timestamp)
        specs = STATUS_FIELDS[family] if is_status else ARRAY_FIELDS[family]
        fields = {
            spec.name: _field(
                raw_row.get(spec.name, MISSING)
                if is_status
                else (raw_row[index] if index < len(raw_row) else MISSING),
                spec,
            )
            for index, spec in enumerate(specs, 1)
        }
        if family == "hub_status":
            radio = raw_row.get("radio_stats", [])
            radio_specs = (
                number("radio_version", "1", 0, integer=True),
                number("radio_reboot_count", "1", 0, integer=True),
                number("radio_i2c_bus_error_count", "1", 0, integer=True),
                number("radio_status", "1", 0, integer=True),
                number("radio_network_id", None, 0, integer=True),
            )
            for index, spec in enumerate(radio_specs):
                fields[spec.name] = _field(
                    radio[index] if isinstance(radio, list) and index < len(radio) else MISSING,
                    spec,
                )
        rows.append(
            Row(
                family,
                ordinal,
                source_ts,
                fields,
                future=source_ts is not None and source_ts > received_epoch + future_seconds,
                quarantine_reason="invalid_source_time" if source_ts is None else None,
            )
        )
    return Packet(family, "parsed", envelope, tuple(rows))
