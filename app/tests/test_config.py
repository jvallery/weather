# SPDX-License-Identifier: MIT
import os
import subprocess
import sys

import pytest

from weather.config import ConfigurationError, load_settings


def test_unknown_and_invalid_settings_do_not_disclose_values() -> None:
    private_value = "do-not-log-this-value"
    with pytest.raises(ConfigurationError) as result:
        load_settings(
            {"WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net", "WEATHER_UNKNOWN": private_value}
        )
    assert "unknown" in str(result.value)
    assert private_value not in str(result.value)


@pytest.mark.parametrize(
    "environment,field",
    [
        ({}, "public_origin"),
        ({"WEATHER_PUBLIC_ORIGIN": "https://placeholder.invalid"}, "public_origin"),
        ({"WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net", "PORT": "bad-value"}, "port"),
        ({"WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net", "PORT": "9000"}, "port"),
    ],
)
def test_settings_reject_missing_placeholder_and_invalid_values(
    environment: dict[str, str], field: str
) -> None:
    with pytest.raises(ConfigurationError, match=field):
        load_settings(environment)


def test_runtime_environment_is_not_an_extra_configuration_key() -> None:
    settings = load_settings(
        {"WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net", "PATH": "/bin"}
    )
    assert settings.port == 8000
    assert settings.metrics_port == 9000


def test_api_startup_reports_only_invalid_field() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from weather.cli import main; main()", "api"],
        env={
            **os.environ,
            "WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net",
            "WEATHER_UNKNOWN": "private-test-value",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert result.stderr == "Invalid configuration fields: unknown\n"
    assert "private-test-value" not in result.stdout + result.stderr
