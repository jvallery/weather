# SPDX-License-Identifier: MIT
"""Validate runtime settings without including input values in startup errors."""

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    public_origin: HttpUrl
    port: int = Field(default=8000, ge=1, le=65535)
    metrics_port: int = Field(default=9000, ge=1, le=65535)
    # Default Tempest cadences; a value is fresh until observed_at + hold (#14).
    api_obs_st_interval_seconds: float = Field(default=60, gt=0, le=3600)
    api_obs_st_hold_seconds: float = Field(default=150, gt=0, le=86400)
    api_rapid_wind_interval_seconds: float = Field(default=3, gt=0, le=3600)
    api_rapid_wind_hold_seconds: float = Field(default=15, gt=0, le=86400)

    @field_validator("public_origin")
    @classmethod
    def public_https_origin(cls, value: HttpUrl) -> HttpUrl:
        host = value.host or ""
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
            or value.path not in (None, "/")
            or host in {"localhost", "example.com", "example.net", "example.org"}
            or any(word in host for word in ("placeholder", "changeme", "replace-me"))
        ):
            raise ValueError("requires a configured public HTTPS origin")
        return value


class ConfigurationError(ValueError):
    """A value-free diagnostic safe for process startup logs."""


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environment is None else environment
    values: dict[str, str] = {
        key.removeprefix("WEATHER_").lower(): value
        for key, value in env.items()
        if key.startswith("WEATHER_") and not key.startswith("WEATHER_CI_")
    }
    for key in ("PORT", "METRICS_PORT"):
        if key in env:
            values[key.lower()] = env[key]
    try:
        settings = Settings.model_validate(values)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError("Invalid configuration fields: " + ", ".join(fields)) from None
    if settings.port == settings.metrics_port:
        raise ConfigurationError("Invalid configuration fields: port, metrics_port")
    return settings
