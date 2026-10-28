# SPDX-License-Identifier: MIT
"""#167: verdify-platform climate rows become obs_st values in canonical units, never guessed."""

import csv
import io
import json
from pathlib import Path

import pytest
from weather.importer import verdify

SAMPLE = Path(__file__).parent / "fixtures" / "verdify_climate_sample.csv"


def sample() -> list[dict[str, str]]:
    with SAMPLE.open() as handle:
        return list(csv.DictReader(handle))


def test_a_tempest_row_converts_to_the_live_values() -> None:
    """The 18:00 report, as the live receiver stored it (8.11 °C, 0.41/0.61/0.06 m/s, 243°)."""
    values = verdify.transform(sample()[0])
    assert values == {
        "air_temperature": 8.11,
        "relative_humidity": 76.8,
        "station_pressure": 848.77,
        "illuminance": 47193,
        "solar_radiation": 393,
        "uv": 2.63,
        "wind_avg": 0.41,
        "wind_direction": 243,
        "wind_gust": 0.61,
        "wind_lull": 0.06,
        "precip_accumulated": 0.0,
        "lightning_strike_count": 0,
        "lightning_strike_avg_distance": 0.0,
    }


def test_a_poll_row_keeps_only_its_repeated_scalars() -> None:
    """A Home Assistant poll row: its wind and rain columns are not the report's values."""
    values = verdify.transform(sample()[1])
    assert set(values) == {
        "air_temperature",
        "relative_humidity",
        "station_pressure",
        "illuminance",
        "solar_radiation",
        "uv",
    }


def test_rain_and_lightning_convert_from_inches_and_miles() -> None:
    values = verdify.transform(sample()[2])
    # 0.000328661 in in the report minute is the live receiver's 0.008348 mm.
    assert values["precip_accumulated"] == pytest.approx(0.008348, abs=1e-6)
    assert values["lightning_strike_count"] == 3
    assert values["lightning_strike_avg_distance"] == pytest.approx(12.1)
    assert values["air_temperature"] == pytest.approx(7.0)
    assert values["wind_avg"] == pytest.approx(0.94)


def test_a_report_without_rain_fields_has_no_rain_and_a_missing_value_is_never_zero() -> None:
    winter = verdify.transform(sample()[3])
    assert "precip_accumulated" not in winter and "lightning_strike_count" not in winter
    assert winter["air_temperature"] == pytest.approx(-2.0)
    assert winter["wind_gust"] == pytest.approx(2.73)
    assert "air_temperature" not in verdify.transform(sample()[4])


def test_out_of_range_values_are_dropped_not_clamped() -> None:
    values = verdify.transform(sample()[5])
    for key in ("air_temperature", "relative_humidity", "station_pressure", "illuminance"):
        assert key not in values
    assert "wind_avg" not in values and "wind_direction" not in values
    assert values["wind_gust"] == 0.0


def test_loader_rows_are_utc_instants_with_the_import_cap() -> None:
    out = io.StringIO()
    with SAMPLE.open() as handle:
        assert verdify.main(handle, out) == 0
    loaded = list(csv.reader(io.StringIO(out.getvalue())))
    reports = [row for row in loaded if row[0] == "obs_st"]
    # Every sample row with a valid reading, including the light-only row.
    assert len(reports) == 7
    family, at, values, cap = reports[0]
    assert at == "2026-10-26T18:00:07.099546+00:00"
    assert json.loads(values)["air_temperature"] == 8.11
    assert cap == str(verdify.CAP_SECONDS)
    with pytest.raises(ValueError):
        verdify.instant("2026-10-26 18:00:07")


def test_rapid_wind_comes_only_from_tempest_rows() -> None:
    rows = sample()
    # The 18:00 Tempest row's 3 s sample (0.02 mph from 310 degrees), held at most 6 s.
    assert verdify.rapid(rows[0]) == {"wind_speed": 0.01, "wind_direction": 310}
    # A poll row's wind columns hold the lagged average, not a sample.
    assert verdify.rapid(rows[1]) == {}
    out = io.StringIO()
    with SAMPLE.open() as handle:
        verdify.main(handle, out)
    samples = [row for row in csv.reader(io.StringIO(out.getvalue())) if row[0] == "rapid_wind"]
    assert [row[1][:19] for row in samples] == [
        "2026-10-26T18:00:07",
        "2026-10-26T08:00:05",
        "2025-11-03T06:10:00",
    ]
    assert all(row[3] == str(verdify.RAPID_CAP_SECONDS) for row in samples)


def test_a_light_only_row_keeps_its_light_readings() -> None:
    values = verdify.transform(sample()[6])
    assert values == {"illuminance": 57965, "solar_radiation": 483, "uv": 1.44}


def test_the_fixture_carries_no_station_identity() -> None:
    text = SAMPLE.read_text().lower()
    for word in ("lat", "lon", "serial", "st-", "hub", "station_id"):
        assert word not in text
