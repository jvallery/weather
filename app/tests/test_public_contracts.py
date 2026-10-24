# SPDX-License-Identifier: MIT
"""Focused public serialization/query/privacy checks; no database, station or model calls."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from weather.api.metric_registry import convert, discovery, metric_definition, validate_selection
from weather.api.public_models import (
    Aggregate,
    AggregateQuery,
    Current,
    Interval,
    Metric,
    ObservationQuery,
    age_current,
    enforce_raw_window,
)
from weather.api.public_schema import contract_app, public_openapi

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures/public_contracts"
START, END = "2026-11-01T06:00:00Z", "2026-11-02T07:00:00Z"


def metric() -> dict[str, object]:
    return json.loads((FIXTURES / "current.json").read_text())["metrics"][0]  # type: ignore[no-any-return]


def test_archived_metric_and_computation_fixtures() -> None:
    document = json.loads(
        (ROOT / "docs/archive/planning-2026-10/contracts/schema-fixtures.json").read_text()
    )
    models = {"Metric": Metric, "Aggregate": Aggregate}
    for case in document["cases"]:
        if case["schema"] not in models:
            continue
        if case["valid"]:
            models[case["schema"]].model_validate(case["value"])
        else:
            with pytest.raises((ValidationError, ValueError)):
                models[case["schema"]].model_validate(case["value"])


def test_web_fixture_current_zero_missing_stale_and_raw_history() -> None:
    from weather.api.public_models import History

    current = Current.model_validate_json((FIXTURES / "current.json").read_text())
    history = History.model_validate_json((FIXTURES / "history.json").read_text())
    assert current.metrics[0].value == 0
    assert current.metrics[-1].value is None and current.metrics[-1].reason
    assert history.points[0].value == 0 and history.points[1].value is None
    assert (
        History.model_validate({**history.model_dump(), "resolution_seconds": 0}).resolution_seconds
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", True),
        ("value", float("nan")),
        ("value", float("inf")),
        ("unit", "degF"),
        ("observed_at", "2026-02-30T00:00:00Z"),
        ("observed_at", "2026-11-01T01:00:00-07:00"),
        ("observed_at", "2026-11-01T08:15:00.1234567Z"),
        ("quality", "invalid"),
        ("station_serial", "SYNTHETIC-PRIVATE"),
    ],
)
def test_invalid_numbers_units_times_and_private_fields(field: str, value: object) -> None:
    with pytest.raises((ValidationError, ValueError)):
        Metric.model_validate({**metric(), field: value})


def test_nullable_cadence_and_source_expiry_never_freshen_cache() -> None:
    initial = Current.model_validate_json((FIXTURES / "current.json").read_text())
    initial = Current.model_validate(
        {
            **initial.model_dump(),
            "metrics": [
                {**item.model_dump(), "expected_interval_seconds": None} for item in initial.metrics
            ],
        }
    )
    expired = age_current(initial, datetime(2026, 11, 1, 8, 17, tzinfo=UTC), cached=True)
    assert expired.data_state == "cached"
    assert expired.metrics[0].freshness == "stale"
    assert expired.metrics[0].observed_at == initial.metrics[0].observed_at
    assert expired.metrics[0].received_at == initial.metrics[0].received_at
    assert expired.metrics[0].value == 0
    assert expired.metrics[-1].value is None
    assert expired.metrics[0].expected_interval_seconds is None
    with pytest.raises(ValidationError):
        Current.model_validate({**initial.model_dump(), "generated_at": expired.generated_at})


def test_utc_microseconds_half_open_and_dst_day_duration() -> None:
    assert Interval(start=START, end=END).seconds == 25 * 3600  # type: ignore[arg-type]
    assert (
        Interval(
            start="2026-01-01T00:00:00.000001Z",  # type: ignore[arg-type]
            end="2026-01-01T00:00:00.000002Z",
        ).seconds
        == 0.000001
    )  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Interval(start=END, end=START)  # type: ignore[arg-type]


def test_registry_conversions_and_private_diagnostics_absent() -> None:
    payload = discovery().model_dump_json()
    assert all(item.expected_interval_seconds is None for item in discovery().metrics)
    assert {"temperature", "humidity", "wind_gust", "station_pressure", "irradiance"} <= {
        item.metric for item in discovery().metrics
    }
    assert not any(
        word in payload
        for word in (
            "station_serial",
            "sensor_status",
            "rssi",
            "battery",
            "raw",
            "source_binding",
        )
    )
    assert convert("temperature", 0, "degF") == 32
    assert convert("temperature", 100, "degF") == 212
    assert convert("wind_gust", 1, "mph") == Fraction(3125, 1397)
    assert convert("precipitation", 25.4, "in") == 1
    assert convert("lightning_distance", 1.609344, "mi") == 1
    assert metric_definition("station_pressure").canonical_unit == "hPa"
    with pytest.raises(ValueError):
        validate_selection(["temperature"], "observed_accumulation")
    with pytest.raises(ValueError):
        validate_selection(["sensor_status"])


def test_query_limits_explicit_statistic_and_configured_raw_budget() -> None:
    query = ObservationQuery(start=START, end=END, metrics=["temperature"])  # type: ignore[arg-type]
    assert query.limit == 1000
    enforce_raw_window(query, maximum_seconds=25 * 3600)
    with pytest.raises(ValueError, match="Raw source window") as error:
        enforce_raw_window(query, maximum_seconds=24 * 3600)
    assert error.value.suggested_resolution_seconds == 18  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        AggregateQuery(
            start=START,
            end=END,
            metrics=["temperature"],  # type: ignore[arg-type]
            statistic="mean",
            resolution_seconds=0,
        )
    with pytest.raises(ValidationError):
        ObservationQuery(start=START, end=END, metrics=["temperature"], limit=5001)  # type: ignore[arg-type]


def test_http_bad_metric_statistic_bounds_and_private_input_are_sanitized_400() -> None:
    with TestClient(contract_app()) as client:
        discovery_response = client.get("/api/v1/weather/metrics")
        assert discovery_response.status_code == 200
        assert discovery_response.json()["metrics"][0]["conversions"]
        for params in (
            {"start": START, "end": END, "metrics": "not_public"},
            {"start": START, "end": END, "metrics": "temperature", "limit": "5001"},
            {
                "start": START,
                "end": END,
                "metrics": "temperature",
                "station_serial": "SYNTHETIC-PRIVATE",
            },
        ):
            response = client.get("/api/v1/weather/observations", params=params)
            assert response.status_code == 400
            assert response.headers["content-type"].startswith("application/problem+json")
            assert "SYNTHETIC-PRIVATE" not in response.text and "detail" not in response.json()
        response = client.get(
            "/api/v1/weather/aggregates",
            params={
                "start": START,
                "end": END,
                "metrics": "temperature",
                "resolution_seconds": "60",
                "statistic": "observed_accumulation",
            },
        )
        assert response.status_code == 400
        assert client.get("/api/v1/weather/current").status_code == 503


def test_openapi_is_local_and_public_only() -> None:
    schema = public_openapi()
    assert schema["openapi"] == "3.1.1"
    assert len(schema["paths"]) == 4
    assert "Wind" in schema["components"]["schemas"]
    assert "Lightning" in schema["components"]["schemas"]
    assert "422" not in json.dumps(schema)
    assert "station_serial" not in json.dumps(schema)
    assert "conversions" in schema["components"]["schemas"]["MetricDefinition"]["required"]
    assert "schema_version" in schema["components"]["schemas"]["Current"]["required"]


def test_ci_rejects_real_pydantic_change_without_regenerated_artifacts(tmp_path: Path) -> None:
    """Run the actual CI mode against an isolated source overlay, leaving checkout untouched."""
    overlay = tmp_path / "overlay/weather"
    overlay.mkdir(parents=True)
    shutil.copyfile(ROOT / "app/weather/__init__.py", overlay / "__init__.py")
    shutil.copytree(ROOT / "app/weather/api", overlay / "api")
    path = overlay / "api/public_models.py"
    source = path.read_text()
    assert "class Metric(PublicModel):" in source
    path.write_text(
        source.replace(
            "class Metric(PublicModel):",
            "class Metric(PublicModel):\n    regression_added_field: str",
            1,
        )
    )
    result = subprocess.run(
        ["bash", "scripts/ci.sh", "contracts"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(overlay.parent)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "API contract drift" in result.stderr + result.stdout
    assert "weather.openapi.json" in result.stderr + result.stdout
    assert "web/app/generated/weather-api.ts" in result.stderr + result.stdout


def test_structured_wind_preserves_directional_missing_and_full_denominator() -> None:
    from weather.api.public_models import Wind

    gap = {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:10:00Z"}
    missing = {
        "fraction": 0,
        "support_seconds": 0,
        "expected_seconds": 600,
        "algorithm_revision": "synthetic_support_v1",
        "gaps": [gap],
    }
    payload = {
        "mean_speed": None,
        "maximum_gust": None,
        "direction_degrees": None,
        "direction_state": "unavailable",
        "resultant": None,
        "speed_coverage": missing,
        "direction_coverage": missing,
        "rose": [
            {
                "category": "missing",
                "angle_start": None,
                "angle_end": None,
                "speed_min": None,
                "speed_max": None,
                "duration_seconds": 600,
                "denominator_seconds": 600,
            }
        ],
        "algorithm_revision": "synthetic_wind_v1",
        "evidence_revision": "synthetic",
        "evidence_ids": [],
        "aggregate_id": "synthetic_wind",
    }
    wind = Wind.model_validate(payload)
    assert wind.rose[0].duration_seconds == 600 and wind.mean_speed is None
    with pytest.raises(ValidationError):
        Wind.model_validate({**payload, "direction_degrees": 0})
    with pytest.raises(ValidationError):
        Wind.model_validate({**payload, "rose": [{**payload["rose"][0], "duration_seconds": 599}]})


def test_lightning_remains_integer_and_missing_is_not_zero() -> None:
    from weather.api.public_models import Lightning

    payload = {
        "value": None,
        "observed_subtotal": 0,
        "boundary_uncertain_count": 1,
        "coverage": {
            "fraction": 0,
            "support_seconds": 0,
            "expected_seconds": 600,
            "algorithm_revision": "synthetic_support",
            "gaps": [],
        },
        "reason": "No qualified count support.",
        "authority": "obs_st",
        "algorithm_revision": "synthetic_count",
        "evidence_revision": "synthetic",
        "evidence_ids": [],
        "aggregate_id": "synthetic_count",
    }
    assert Lightning.model_validate(payload).value is None
    with pytest.raises(ValidationError):
        Lightning.model_validate({**payload, "value": 0})
    with pytest.raises(ValidationError):
        Lightning.model_validate({**payload, "boundary_uncertain_count": 0.5})
