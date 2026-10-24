# SPDX-License-Identifier: MIT
"""Source-owned startup registration; job payloads cannot select modules or policy files."""

from sqlalchemy.ext.asyncio import AsyncEngine

from weather.api.aggregate_config import AGGREGATE_POLICY_FILE, load_aggregate_configuration
from weather.scheduler.aggregate_worker import AggregateRevisionWorker
from weather.scheduler.registry import Registry


def register(registry: Registry, engine: AsyncEngine) -> None:
    config = load_aggregate_configuration(AGGREGATE_POLICY_FILE)
    AggregateRevisionWorker(
        engine,
        config.policies,
        config.document,
        pressure_endpoint_seconds=config.pressure_endpoint_seconds,
        pressure_minimum_fraction=config.pressure_minimum_fraction,
    ).register(registry)
