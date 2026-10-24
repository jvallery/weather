# SPDX-License-Identifier: MIT
import pytest
from weather.scheduler.registry import JobContext, Registry, UnregisteredJob


async def noop(context: JobContext) -> None:
    return None


def test_unknown_handlers_and_topics_fail_closed() -> None:
    registry = Registry()
    with pytest.raises(UnregisteredJob):
        registry.handler("missing")
    with pytest.raises(UnregisteredJob):
        registry.topic_kind("missing")


def test_duplicate_route_rejection_preserves_the_original_registry() -> None:
    registry = Registry()
    registry.register("aggregate", noop, topics=("metric.changed",))
    with pytest.raises(ValueError):
        registry.register("other", noop, topics=("metric.changed",))
    assert registry.topic_kind("metric.changed") == "aggregate"
    assert registry.handler("aggregate") is noop
    with pytest.raises(UnregisteredJob):
        registry.handler("other")
