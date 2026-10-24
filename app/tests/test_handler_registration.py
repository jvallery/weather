# SPDX-License-Identifier: MIT
"""Actual source-owned startup routes, without connection or filesystem dependencies."""

from pathlib import Path

from weather.api.aggregate_config import load_aggregate_configuration
from weather.scheduler import handlers, runner


def test_startup_registers_atomic_qualification_events_and_revision_handler(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "public-policy.json"
    path.write_text('{"version":1,"metrics":{}}')
    config = load_aggregate_configuration(path)
    monkeypatch.setattr(handlers, "load_aggregate_configuration", lambda _path: config)
    registry = runner.installed_registry(object())
    assert registry.topic_kind("telemetry.changed") == "aggregate.changed"
    assert registry.topic_kind("telemetry.conflict") == "aggregate.conflict"
    assert callable(registry.handler("aggregate.recompute"))
