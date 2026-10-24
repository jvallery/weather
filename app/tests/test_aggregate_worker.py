# SPDX-License-Identifier: MIT
"""Small pure scheduling, bounded computation and immutable job-binding checks."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from test_support_assembly import sample
from weather.api.aggregate_config import support_binding
from weather.api.metric_registry import PublicInputError
from weather.api.support_assembly import SupportPolicy
from weather.db import jobs
from weather.db.public_evidence import EvidenceWindow
from weather.scheduler import aggregate_worker as module
from weather.scheduler.aggregate_worker import (
    AggregateRevisionWorker,
    Change,
    Target,
    affected_targets,
    plan_change,
)
from weather.scheduler.registry import Registry

START = datetime(2026, 1, 1, tzinfo=UTC)
NOW = START + timedelta(hours=3)
POLICIES = {"temperature": SupportPolicy("forward_hold", "synthetic_hold_v1", hold_seconds=60)}


def worker(**kwargs: Any) -> AggregateRevisionWorker:
    policies = kwargs.pop("policies", POLICIES)
    document = kwargs.pop("policy_document", support_binding(policies))
    return AggregateRevisionWorker(None, policies, document, clock=lambda: NOW, **kwargs)


def event(source: datetime = START, boundary: int = 1) -> Change:
    return Change(uuid4(), "obs_st", "Synthetic station", source, boundary)


def test_change_parser_is_exact_private_free_and_accepts_postgres_utc_timestamp() -> None:
    payload = {
        "id": str(uuid4()),
        "family": "obs_st",
        "station": "Synthetic station",
        "sourceTime": START.isoformat(),
        "watermark": 1,
    }
    assert Change.parse(payload).source_time == START
    for patch in (
        {"quality": {}},
        {"watermark": True},
        {"watermark": -1},
        {"id": None},
        {"family": "hub_status"},
        {"station": "bad\nname"},
        {"sourceTime": "2026-01-01T00:00:00"},
    ):
        with pytest.raises(ValueError):
            Change.parse({**payload, **patch})


def test_close_schedule_is_fixed_per_window_policy_and_late_keys_bind_watermark() -> None:
    first = plan_change(
        event(), POLICIES, "policy_v1", now=NOW, event_deadline=NOW + timedelta(days=1)
    )
    second = plan_change(
        event(boundary=2),
        POLICIES,
        "policy_v1",
        now=NOW + timedelta(seconds=1),
        event_deadline=NOW + timedelta(days=1),
    )
    close1 = [p for p in first if p.key.startswith("aggregate-close:")]
    close2 = [p for p in second if p.key.startswith("aggregate-close:")]
    assert close1 == close2
    for plan in close1:
        target = Target.parse(plan.payload)
        assert plan.not_before == target.end + timedelta(seconds=120)
        assert plan.deadline == target.end + timedelta(hours=48)
    late1 = [p for p in first if p.key.startswith("aggregate-late:")]
    late2 = [p for p in second if p.key.startswith("aggregate-late:")]
    assert late1 and {p.key for p in late1}.isdisjoint(p.key for p in late2)
    assert all(p.deadline == NOW + timedelta(days=1) and p.not_before == NOW for p in late1)
    assert all(Target.parse(p.payload).end <= NOW for p in late1)


def test_active_days_and_hours_only_get_future_close_jobs_not_immediate_revisions() -> None:
    plans = plan_change(
        event(NOW + timedelta(minutes=1)),
        POLICIES,
        "policy_v1",
        now=NOW,
        event_deadline=NOW + timedelta(days=1),
    )
    assert plans and all(p.key.startswith("aggregate-close:") for p in plans)
    assert all(Target.parse(p.payload).end > NOW for p in plans)
    assert (
        affected_targets(Change(uuid4(), "evt_strike", "Synthetic station", START, 1), POLICIES)
        == ()
    )


def test_expired_original_close_deadline_does_not_get_renewed_on_retry() -> None:
    change = event(START - timedelta(days=5))
    deadline = NOW + timedelta(hours=1)
    plans = plan_change(change, POLICIES, "policy_v1", now=NOW, event_deadline=deadline)
    assert plans and all(p.key.startswith("aggregate-late:") for p in plans)
    retry = plan_change(
        change, POLICIES, "policy_v1", now=NOW + timedelta(minutes=30), event_deadline=deadline
    )
    assert plans == retry
    assert plan_change(change, POLICIES, "policy_v1", now=deadline, event_deadline=deadline) == ()


@pytest.mark.parametrize(
    ("stamp", "hours"), [("2026-03-08T12:00:00Z", 23), ("2026-11-01T12:00:00Z", 25)]
)
def test_denver_weather_days_preserve_dst_length(stamp: str, hours: int) -> None:
    target = Target.at("weather_day", datetime.fromisoformat(stamp))
    assert target.end - target.start == timedelta(hours=hours)
    repeated1 = Target.at("hour", datetime(2026, 11, 1, 8, tzinfo=UTC))
    repeated2 = Target.at("hour", datetime(2026, 11, 1, 9, tzinfo=UTC))
    assert repeated1 != repeated2
    assert Target.at("weather_day", repeated1.start) == Target.at("weather_day", repeated2.start)


def test_late_support_envelope_uses_report_backwards_hold_forwards_and_is_bounded() -> None:
    policies = {
        **POLICIES,
        "wind_avg": SupportPolicy(
            "reported_interval",
            "synthetic_report_v1",
            reported_policies=("v171-wind-report-interval-1",),
            maximum_reported_seconds=120,
        ),
    }
    targets = affected_targets(event(START), policies)
    assert {t.start for t in targets if t.kind == "hour"} == {START - timedelta(hours=1), START}
    longest = {
        "temperature": SupportPolicy("forward_hold", "hold_v1", hold_seconds=86400),
        "humidity": SupportPolicy(
            "reported_interval",
            "report_v1",
            reported_policies=("synthetic_report_v1",),
            maximum_reported_seconds=86400,
        ),
    }
    assert len(affected_targets(event(START + timedelta(minutes=30)), longest)) <= 56


def test_registry_routes_changed_conflict_and_recompute_without_guessed_authority() -> None:
    value = worker()
    registry = Registry()
    value.register(registry)
    assert registry.topic_kind("telemetry.changed") == "aggregate.changed"
    assert registry.topic_kind("telemetry.conflict") == "aggregate.conflict"
    recipes = value._recipes(Target.at("hour", START))
    assert recipes == (("temperature", "mean"), ("temperature", "min"), ("temperature", "max"))
    with pytest.raises(ValueError):
        worker(policy_document={**support_binding(POLICIES), "credential": "not allowed"})


class Context:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.job = jobs.Job(
            uuid4(),
            "aggregate.recompute",
            payload,
            None,
            NOW + timedelta(days=1),
            1,
            "synthetic-worker",
            1,
        )
        self.result: Any = None
        self.committed = False

    async def commit(self, write: Any = None, *, result: Any = None) -> None:
        if write:
            await write(None)
        self.result, self.committed = result, True


def work_payload(value: AggregateRevisionWorker, *, end: datetime | None = None) -> dict[str, Any]:
    target = Target.at("hour", START)
    return {
        "version": 1,
        "station": "Synthetic station",
        "kind": target.kind,
        "start": target.start.isoformat().replace("+00:00", "Z"),
        "end": (end or target.end).isoformat().replace("+00:00", "Z"),
        "policy": value.policy_revision,
    }


def test_conflict_is_acknowledged_without_inspecting_raw_payload_or_creating_work() -> None:
    async def check() -> None:
        context = Context({"private_conflict_fields": ["synthetic never public"]})
        await worker().conflict(context)
        assert context.result == {"reason": "covered_by_telemetry_changed"}
        assert context.committed

    asyncio.run(check())


def test_recompute_freezes_one_boundary_and_evaluation_then_persists_only_in_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        value = worker()
        context = Context(work_payload(value))
        calls, persisted = [], []

        async def rows(self: Any, sql: Any, params: Any) -> Any:
            calls.append("watermark")
            return [{"watermark": 1}]

        async def evidence(*args: Any, **kwargs: Any) -> EvidenceWindow:
            assert kwargs["snapshot_watermark"] == "1"
            assert kwargs["evaluated_at"] == NOW and not kwargs["enforce_raw_range"]
            calls.append("evidence")
            return EvidenceWindow("1", (sample(10, 0, "sample_one"),))

        async def persist(connection: Any, prepared: Any) -> str:
            assert calls == ["watermark", "evidence"]
            assert prepared.parameters["evaluated"] == NOW
            assert prepared.parameters["policy_document"]
            persisted.append(prepared)
            return "created"

        monkeypatch.setattr(module.PublicSqlSource, "_rows", rows)
        monkeypatch.setattr(module, "read_evidence_window", evidence)
        monkeypatch.setattr(module.aggregate_store, "persist", persist)
        result = await value.recompute(context)
        assert context.committed and len(persisted) == result["created"] == 3
        assert result["watermark"] == "1"

    asyncio.run(check())


def test_active_or_noncanonical_window_is_rejected_before_any_database_read() -> None:
    async def check() -> None:
        value = worker()
        payload = work_payload(value)
        payload.update(
            start=NOW.isoformat().replace("+00:00", "Z"),
            end=(NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
        with pytest.raises(ValueError):
            await value.recompute(Context(payload))
        payload["end"] = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        with pytest.raises(ValueError):
            await value.recompute(Context(payload))

    asyncio.run(check())


def test_worker_uses_shared_overlap_cost_preflight_before_synchronous_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        value = worker(
            maximum_work=300,
            policies={
                "temperature": SupportPolicy("forward_hold", "overlap_v1", hold_seconds=86400)
            },
        )
        entered = []
        original = module._preflight_compute

        async def rows(*args: Any, **kwargs: Any) -> Any:
            return [{"watermark": 1}]

        async def evidence(*args: Any, **kwargs: Any) -> EvidenceWindow:
            return EvidenceWindow(
                "1",
                tuple(
                    sample(10, i, f"sample_{i}", device=f"synthetic_device_{i}") for i in range(20)
                ),
            )

        async def preflight(*args: Any, **kwargs: Any) -> None:
            entered.append(True)
            await original(*args, **kwargs)

        def never(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Engine must not run after work-budget rejection")

        monkeypatch.setattr(module.PublicSqlSource, "_rows", rows)
        monkeypatch.setattr(module, "read_evidence_window", evidence)
        monkeypatch.setattr(module, "_preflight_compute", preflight)
        monkeypatch.setattr(module, "compute_window", never)
        with pytest.raises(PublicInputError):
            await value.recompute(Context(work_payload(value)))
        assert entered

    asyncio.run(check())


def grouped_reader(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], list[Any]]:
    import weakref

    reads, windows = [], []

    async def rows(*args: Any, **kwargs: Any) -> Any:
        reads.append("watermark")
        return [{"watermark": 1}]

    async def evidence(reader: Any, interval: Any, roles: Any, **kwargs: Any) -> EvidenceWindow:
        assert len(roles) == 1 and kwargs["maximum_points"] == 2
        assert kwargs["snapshot_watermark"] == "1" and kwargs["evaluated_at"] == NOW
        assert not kwargs["enforce_raw_range"]
        # An earlier group's coroutine must not retain its evidence/index while
        # a subsequent complete-window group is being read.
        assert all(window() is None for window in windows)
        metric = roles[0]
        reads.append(metric)
        value = EvidenceWindow(
            "1", tuple(sample(10, i * 30, f"{metric}_{i}", metric=metric) for i in range(2))
        )
        windows.append(weakref.ref(value))
        return value

    monkeypatch.setattr(module.PublicSqlSource, "_rows", rows)
    monkeypatch.setattr(module, "read_evidence_window", evidence)
    return reads, windows


def test_grouped_reads_exceed_union_cap_but_individual_groups_and_recipe_budgets_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        policies = {
            **POLICIES,
            "humidity": SupportPolicy("forward_hold", "humidity_v1", hold_seconds=60),
        }
        value = worker(policies=policies, maximum_inputs=2, maximum_work=25)
        reads, windows = grouped_reader(monkeypatch)
        persisted = []
        clock_calls = []

        def clock() -> datetime:
            clock_calls.append(True)
            return NOW

        async def persist(connection: Any, item: Any) -> str:
            persisted.append(item)
            return "created"

        value.clock = clock
        monkeypatch.setattr(module.aggregate_store, "persist", persist)
        context = Context(work_payload(value))
        result = await value.recompute(context)
        assert reads == ["watermark", "humidity", "temperature"]
        assert len(clock_calls) == 1 and all(window() is None for window in windows)
        assert 2 * len(windows) > value.maximum_inputs
        assert len(persisted) == result["created"] == 6
        assert context.committed

    asyncio.run(check())


def test_total_prepared_json_overflow_after_first_group_never_opens_catalog_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import MappingProxyType

    from weather.db.aggregate_store import StoredComputation

    async def check() -> None:
        item = StoredComputation(
            "synthetic", "synthetic", MappingProxyType({"document": "public" * 10})
        )
        policies = {
            **POLICIES,
            "humidity": SupportPolicy("forward_hold", "humidity_v1", hold_seconds=60),
        }
        value = worker(
            policies=policies,
            maximum_inputs=2,
            maximum_work=25,
            maximum_prepared_bytes=2 + 3 * (module._prepared_size(item) + 1),
        )
        reads, windows = grouped_reader(monkeypatch)
        prepared = []

        def prepare(*args: Any, **kwargs: Any) -> StoredComputation:
            prepared.append(True)
            return item

        async def never(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("No partial aggregate writes may occur on prepared-byte overflow")

        monkeypatch.setattr(module.aggregate_store, "prepare", prepare)
        monkeypatch.setattr(module.aggregate_store, "persist", never)
        context = Context(work_payload(value))
        with pytest.raises(PublicInputError, match="byte budget"):
            await value.recompute(context)
        assert reads == ["watermark", "humidity", "temperature"]
        assert len(prepared) == 4 and not context.committed
        assert all(window() is None for window in windows)

    asyncio.run(check())
