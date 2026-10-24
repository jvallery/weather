# SPDX-License-Identifier: MIT
"""Pure startup/lifespan tests: file fixtures and fake pool, no database or socket."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest
from weather.api import build_application, runtime
from weather.config import ConfigurationError, load_settings
from weather.db.public_mapping import FreshnessPolicy


def settings(enabled: bool = True):
    return load_settings(
        {
            "WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net",
            "WEATHER_API_DATA_ENABLED": str(enabled),
            "WEATHER_API_STATION_ALIAS": "Synthetic fixture",
        }
    )


def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text('{"version":1,"metrics":{}}')
    key = tmp_path / "cursor"
    key.write_bytes(b"synthetic-test-key-" + b"a" * 45)
    monkeypatch.setattr(runtime, "POLICY_FILE", policy)
    monkeypatch.setattr(runtime, "CURSOR_FILE", key)


class FakePool:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def test_disabled_runtime_does_not_read_files_or_allocate_pool(monkeypatch: pytest.MonkeyPatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Uncommissioned API touched its dependencies")

    monkeypatch.setattr(runtime, "load_policies", forbidden)
    monkeypatch.setattr(runtime, "create_public_database_engine", forbidden)
    with TestClient(build_application(settings(False))) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/v1/weather/current").status_code == 404


def test_enabled_runtime_requires_alias_without_echoing_values():
    with pytest.raises(ConfigurationError, match="api_station_alias"):
        load_settings(
            {
                "WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net",
                "WEATHER_API_DATA_ENABLED": "true",
            }
        )


@pytest.mark.parametrize(
    "contents",
    [
        '{"version":1,"version":1,"metrics":{}}',
        '{"version":true,"metrics":{}}',
        '{"version":1,"metrics":{"private-fixture":{"secret":"private-value"}}}',
        '{"version":1,"metrics":{"temperature":{"expected_interval_seconds":true,"hold_seconds":60}}}',
    ],
)
def test_bad_policy_is_value_free(tmp_path: Path, contents: str):
    path = tmp_path / "policy"
    path.write_text(contents)
    with pytest.raises(runtime.PublicRuntimeConfigurationError) as error:
        runtime.load_policies(path)
    assert str(error.value) == "Invalid API policy file"


def test_policy_revision_changes_when_source_hold_changes(tmp_path: Path):
    path = tmp_path / "policy"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "metrics": {
                    "temperature": {"expected_interval_seconds": 60, "hold_seconds": 90},
                },
            }
        )
    )
    policies = runtime.load_policies(path)
    assert policies["temperature"] == FreshnessPolicy(60, 90)
    assert runtime.policy_revision(policies) != runtime.policy_revision(
        {
            "temperature": FreshnessPolicy(60, 120),
        }
    )


def test_key_failure_precedes_pool_allocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    files(tmp_path, monkeypatch)
    runtime.CURSOR_FILE.write_bytes(b"invalid fixture")
    monkeypatch.setattr(runtime, "create_public_database_engine", lambda: pytest.fail("pool"))
    with pytest.raises(runtime.PublicRuntimeConfigurationError, match="cursor key"):
        with TestClient(build_application(settings())):
            pass


def test_lifespan_starts_refresh_registers_metrics_and_disposes_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    files(tmp_path, monkeypatch)
    runtime.POLICY_FILE.write_text(
        json.dumps(
            {
                "version": 1,
                "metrics": {
                    "temperature": {"expected_interval_seconds": 60, "hold_seconds": 90},
                },
            }
        )
    )
    pool = FakePool()
    monkeypatch.setattr(runtime, "create_public_database_engine", lambda: pool)
    registry = CollectorRegistry()
    with TestClient(build_application(settings(), registry=registry)) as client:
        assert client.get("/api/v1/weather/current").status_code == 503
        response = client.get("/api/v1/weather/metrics")
        assert response.status_code == 200
        temperature = next(
            row for row in response.json()["metrics"] if row["metric"] == "temperature"
        )
        assert temperature["expected_interval_seconds"] == 60
        metrics = generate_latest(registry).decode()
        assert 'route="current",status="503"' in metrics
        assert "weather_api_source_errors_total" in metrics
        assert "weather_current_conditions_age_seconds" not in metrics
        assert not pool.disposed
    assert pool.disposed


def test_pool_is_disposed_even_if_refresh_shutdown_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    files(tmp_path, monkeypatch)
    pool = FakePool()
    monkeypatch.setattr(runtime, "create_public_database_engine", lambda: pool)
    original = runtime.PublicWeatherService.aclose

    async def failed_close(self, *, timeout_seconds=1):
        await original(self, timeout_seconds=timeout_seconds)
        raise TimeoutError("synthetic cancellation timeout")

    monkeypatch.setattr(runtime.PublicWeatherService, "aclose", failed_close)

    async def run():
        app = build_application(settings(), registry=CollectorRegistry())
        with pytest.raises(TimeoutError, match="synthetic"):
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(run())
    assert pool.disposed
