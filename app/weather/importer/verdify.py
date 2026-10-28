# SPDX-License-Identifier: MIT
"""Import Tempest history from the verdify-platform database (#167).

The greenhouse controller's TimescaleDB (`verdify-db` in `verdify-prod`, table `public.climate`)
holds this station's outdoor readings from 2025-10-23, in US units, on two kinds of rows:

* Tempest rows (`wind_speed_avg_mph` present): the station's own report. Averaged wind is in
  `wind_speed_avg_mph`/`wind_direction_avg_deg` (`wind_speed_mph`/`wind_direction_deg` are rapid
  samples); `precip_in` is the report minute's amount when `precip_intensity_in_h` is present.
* Home Assistant poll rows (no `wind_speed_avg_mph`): about one a minute, repeating the latest
  temperature, humidity, pressure, light and UV. Their wind and rain columns are not the report's
  values (`precip_in` holds a rate), so only those five scalar readings are taken from them.

`transform` turns one source row into the live `obs_st` value keys in canonical metric units,
dropping (never guessing) a missing or out-of-range field. `python -m weather.importer.verdify
transform` reads the export CSV on stdin and writes `source_ts,values,cap_seconds` CSV for the
loader (docs/operations/telemetry.md, "Imported history"). It needs no database access.
"""

import csv
import json
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from typing import IO

SOURCE = "verdify-platform"
# The source reported every one to five minutes: a reading is held at most ten minutes (#167).
CAP_SECONDS = 600
MPH = 0.44704
INCH_MM = 25.4
MILE_KM = 1.609344

# Read-only export, one bounded window per call; :from and :to are UTC instants.
EXPORT_SQL = """COPY (SELECT ts,outdoor_temp_f,outdoor_rh_pct,pressure_hpa,outdoor_lux,
  solar_irradiance_w_m2,uv_index,wind_speed_avg_mph,wind_direction_avg_deg,wind_gust_mph,
  wind_lull_mph,precip_in,precip_intensity_in_h,lightning_count,lightning_avg_dist_mi
FROM public.climate
WHERE outdoor_temp_f IS NOT NULL AND ts >= '{start}' AND ts < '{end}'
ORDER BY ts) TO STDOUT WITH (FORMAT csv, HEADER true)"""

# Canonical bounds; a value outside them is dropped as a sensor or transport fault.
BOUNDS: dict[str, tuple[float, float]] = {
    "air_temperature": (-50.0, 55.0),
    "relative_humidity": (0.0, 100.0),
    "station_pressure": (600.0, 1100.0),
    "illuminance": (0.0, 200_000.0),
    "solar_radiation": (0.0, 1_800.0),
    "uv": (0.0, 20.0),
    "wind_avg": (0.0, 75.0),
    "wind_gust": (0.0, 90.0),
    "wind_lull": (0.0, 75.0),
    "wind_direction": (0.0, 360.0),
    "precip_accumulated": (0.0, 50.0),
    "lightning_strike_count": (0.0, 10_000.0),
    "lightning_strike_avg_distance": (0.0, 100.0),
}


def _number(row: Mapping[str, str], column: str) -> float | None:
    text = (row.get(column) or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def transform(row: Mapping[str, str]) -> dict[str, float]:
    """One source row as `obs_st` values (canonical units); absent or invalid fields are omitted."""
    values: dict[str, float] = {}

    def put(key: str, value: float | None, digits: int) -> None:
        if value is None:
            return
        low, high = BOUNDS[key]
        if low <= value <= high:
            values[key] = round(value, digits)

    fahrenheit = _number(row, "outdoor_temp_f")
    put("air_temperature", None if fahrenheit is None else (fahrenheit - 32) * 5 / 9, 2)
    put("relative_humidity", _number(row, "outdoor_rh_pct"), 2)
    put("station_pressure", _number(row, "pressure_hpa"), 2)
    put("illuminance", _number(row, "outdoor_lux"), 0)
    put("solar_radiation", _number(row, "solar_irradiance_w_m2"), 0)
    put("uv", _number(row, "uv_index"), 2)
    average = _number(row, "wind_speed_avg_mph")
    if average is not None:  # A Tempest row: its own wind, and rain and lightning when reported.
        put("wind_avg", average * MPH, 2)
        put("wind_direction", _number(row, "wind_direction_avg_deg"), 0)
        for key, column in (("wind_gust", "wind_gust_mph"), ("wind_lull", "wind_lull_mph")):
            speed = _number(row, column)
            put(key, None if speed is None else speed * MPH, 2)
        if _number(row, "precip_intensity_in_h") is not None:
            inches = _number(row, "precip_in")
            put("precip_accumulated", None if inches is None else inches * INCH_MM, 6)
            put("lightning_strike_count", _number(row, "lightning_count"), 0)
            miles = _number(row, "lightning_avg_dist_mi")
            put("lightning_strike_avg_distance", None if miles is None else miles * MILE_KM, 1)
    return values


def instant(text: str) -> str:
    """The source's timestamptz text (`2026-10-26 18:00:07.1+00`) as a UTC ISO instant."""
    value = datetime.fromisoformat(text.strip().replace(" ", "T"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source time without an offset")
    return value.isoformat()


def rows(source: Iterable[Mapping[str, str]]) -> Iterator[tuple[str, str, int]]:
    """Loader rows: one per source row that carries at least one valid reading."""
    for row in source:
        values = transform(row)
        if values:
            yield instant(row["ts"]), json.dumps(values, sort_keys=True), CAP_SECONDS


def main(stdin: IO[str], stdout: IO[str]) -> int:
    writer = csv.writer(stdout, lineterminator="\n")
    count = 0
    for record in rows(csv.DictReader(stdin)):
        writer.writerow(record)
        count += 1
    print(f"verdify import transform: {count} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the import runbook
    if sys.argv[1:] != ["transform"]:
        print("usage: python -m weather.importer.verdify transform < export.csv", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.stdin, sys.stdout))
