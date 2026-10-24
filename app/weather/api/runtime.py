# SPDX-License-Identifier: MIT
"""Explicit API activation; files are read only at configured process startup."""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from prometheus_client import CollectorRegistry

from weather.config import Settings
from weather.db.public_connection import create_public_database_engine
from weather.db.public_mapping import FreshnessPolicy
from weather.db.public_source import PublicSqlSource

from .aggregate_config import AGGREGATE_POLICY_FILE, load_aggregate_configuration
from .aggregate_source import AggregateSource
from .cursors import CursorCodec, Snapshot
from .metric_registry import REGISTRY, REGISTRY_REVISION, discovery
from .public_metrics import PublicMetrics
from .public_models import AggregateQuery, Aggregates
from .public_service import PublicWeatherService, SourcePage

POLICY_FILE = Path("/etc/weather/api-policy.json")
CURSOR_FILE = Path("/var/run/weather-api-cursor/CURSOR_HMAC_KEY")


class PublicRuntimeConfigurationError(ValueError):
    pass


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate policy field")
        result[key] = value
    return result


def load_policies(path: Path) -> dict[str, FreshnessPolicy]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("policy size")
        document = json.loads(raw, object_pairs_hook=_unique)
        if (
            type(document) is not dict
            or set(document) != {"version", "metrics"}
            or type(document["version"]) is not int
            or document["version"] != 1
            or type(document["metrics"]) is not dict
        ):
            raise ValueError("policy schema")
        policies = {}
        for metric, data in document["metrics"].items():
            if (
                metric not in REGISTRY
                or type(data) is not dict
                or set(data)
                != {
                    "expected_interval_seconds",
                    "hold_seconds",
                }
            ):
                raise ValueError("policy metric")
            policies[metric] = FreshnessPolicy(**data)
        return policies
    except Exception:
        raise PublicRuntimeConfigurationError("Invalid API policy file") from None


def load_cursor_key(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            value = stream.read(129)
        if re.fullmatch(rb"[A-Za-z0-9_-]{64}", value) is None:
            raise ValueError("key size")
        return value
    except OSError, ValueError:
        raise PublicRuntimeConfigurationError("Invalid API cursor key file") from None


def policy_revision(
    policies: dict[str, FreshnessPolicy],
    aggregate_revision: str | None = None,
    *,
    station_alias: str,
) -> str:
    data = [
        REGISTRY_REVISION,
        station_alias,
        {name: asdict(value) for name, value in policies.items()},
        aggregate_revision,
    ]
    return (
        "api_policy_v2_"
        + hashlib.sha256(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )


def public_lifespan(
    settings: Settings,
    registry: CollectorRegistry,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Key/policy failure happens before allocating a pool; no secret defaults exist.
        policies, key = load_policies(POLICY_FILE), load_cursor_key(CURSOR_FILE)
        calibration = load_aggregate_configuration(AGGREGATE_POLICY_FILE)
        engine = create_public_database_engine()
        service = None
        aggregate = None
        try:
            station_alias = settings.api_station_alias or ""

            async def read_aggregates(
                query: AggregateQuery, snapshot: Snapshot | None
            ) -> SourcePage[Aggregates]:
                if aggregate is None:
                    raise RuntimeError("Aggregate runtime is not configured")
                return await aggregate(query, snapshot)

            source = PublicSqlSource(
                engine,
                station_alias=station_alias,
                maximum_raw_seconds=settings.api_raw_window_seconds,
                policies=policies,
                aggregate_source=read_aggregates,
            )
            aggregate = AggregateSource(
                source,
                calibration.policies,
                clock=lambda: datetime.now(UTC),
                pressure_endpoint_seconds=calibration.pressure_endpoint_seconds,
                pressure_minimum_fraction=calibration.pressure_minimum_fraction,
            )
            service = PublicWeatherService(
                source,
                CursorCodec(
                    key,
                    policy_revision=policy_revision(
                        policies, calibration.revision, station_alias=station_alias
                    ),
                ),
                maximum_raw_seconds=settings.api_raw_window_seconds,
                metrics=PublicMetrics(registry),
                metric_registry=discovery(
                    {
                        metric: policy.expected_interval_seconds
                        for metric, policy in policies.items()
                    }
                ),
            )
            app.state.weather_public_service = service
            await service.start()
            yield
        finally:
            app.state.weather_public_service = None
            try:
                if service is not None:
                    await service.aclose(timeout_seconds=1)
            finally:
                try:
                    if aggregate is not None:
                        await aggregate.aclose(timeout_seconds=1)
                finally:
                    # Even cancellation failure cannot skip pool disposal.
                    async with asyncio.timeout(3):
                        await engine.dispose()

    return lifespan
