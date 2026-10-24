# SPDX-License-Identifier: MIT
"""Tiny fake complete readers; no database, server socket, background CPU or network."""

import asyncio
import base64
import json
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

import pytest
from test_support_assembly import START, at, hold, inputs, sample
from weather.api import aggregate_source as module
from weather.api.aggregate_source import AggregateSource, decode_position, encode_position
from weather.api.cursors import Snapshot
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import AggregateQuery
from weather.db.public_evidence import EvidenceWindow, SupportEvidence
from weather.db.public_source import PublicSqlSource


class FakeRead:
    def __init__(self, rows: tuple[SupportEvidence, ...] = ()) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []
        self.bound = "9"

    async def __call__(
        self, reader: object, window: Any, metrics: Any, **kwargs: Any
    ) -> EvidenceWindow:
        self.calls.append({"window": window, "metrics": tuple(metrics), **kwargs})
        assert kwargs["enforce_raw_range"] is False
        evaluated_at = kwargs["evaluated_at"]
        rows = []
        for row in self.rows:
            if row.point.metric not in metrics:
                continue
            if row.point.observed_at > evaluated_at or row.point.received_at > evaluated_at:
                row = replace(
                    row,
                    source_time_eligible=False,
                    exact_value=None,
                    point=row.point.model_copy(
                        update={
                            "value": None,
                            "quality": "invalid",
                            "reason": "Synthetic future",
                            "support_interval": None,
                        }
                    ),
                )
            rows.append(row)
        lower = window.start - timedelta(seconds=kwargs["preceding_hold_seconds"])
        upper = window.end + timedelta(seconds=kwargs["following_report_seconds"])
        selected = [row for row in rows if window.start <= row.point.observed_at <= upper]
        preceding: dict[tuple[str, str, str], list[SupportEvidence]] = defaultdict(list)
        for row in rows:
            if lower <= row.point.observed_at < window.start and row.source_time_eligible:
                preceding[(row.point.metric, row.device_alias, row.family)].append(row)
        for members in preceding.values():
            stamp = max(row.point.observed_at for row in members)
            selected.extend(row for row in members if row.point.observed_at == stamp)
        return inputs(*selected, watermark=kwargs["snapshot_watermark"] or self.bound)


def query(
    *, limit: int = 1000, end: int = 125, metrics: list[str] | None = None, resolution: int = 60
) -> AggregateQuery:
    return AggregateQuery(
        start=at(0),
        end=at(end),
        metrics=metrics or ["temperature"],
        statistic="mean",
        resolution_seconds=resolution,
        limit=limit,
    )


def source(*, now: list[Any] | None = None, **kwargs: Any) -> AggregateSource:
    return AggregateSource(
        cast(PublicSqlSource, object()),
        {"temperature": hold(180), "humidity": hold(180)},
        pressure_endpoint_seconds=None,
        pressure_minimum_fraction=None,
        clock=lambda: now[0] if now else at(7200),
        **kwargs,
    )


def test_sorted_metrics_halfopen_bins_final_clip_and_page_identities_are_limit_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        fake = FakeRead(
            tuple(
                sample(value, stamp, f"{metric}_{stamp}", metric=metric)
                for metric in ("temperature", "humidity")
                for stamp, value in ((0, 0), (60, 10), (120, 20), (125, 99))
            )
        )
        monkeypatch.setattr(module, "read_evidence_window", fake)
        q = query(limit=3, metrics=["temperature", "humidity"])
        first = await source()(q, None)
        assert fake.calls[0]["snapshot_watermark"] is None
        assert fake.calls[0]["window"].start == at(0)
        assert fake.calls[0]["window"].end == at(120)
        assert first.next_position is not None
        second = await source()(
            q, Snapshot(watermark=first.watermark, position=first.next_position)
        )
        full = await source()(q.model_copy(update={"limit": 100}), None)
        combined = (*first.data.items, *second.data.items)
        assert combined == full.data.items
        assert first.data.revision == second.data.revision == full.data.revision
        assert [item.metric for item in combined] == ["humidity", "temperature"] * 3
        assert [item.value for item in combined] == [0, 0, 10, 10, 20, 20]
        assert [item.interval.seconds for item in combined] == [60, 60, 60, 60, 5, 5]
        assert second.next_position is None
        assert fake.calls[1]["snapshot_watermark"] == first.watermark

    asyncio.run(check())


def test_first_empty_read_still_selects_atomic_watermark_and_large_ranges_do_not_allocate_bins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        fake = FakeRead()
        monkeypatch.setattr(module, "read_evidence_window", fake)
        small = await source()(query(limit=1, end=365 * 86400, resolution=1), None)
        assert small.watermark == "9" and small.next_position is not None
        assert len(small.data.items) == 1 and small.data.items[0].value is None
        assert fake.calls[0]["window"].seconds == 1
        coarse = await source()(query(limit=1, end=365 * 86400, resolution=2_147_483_647), None)
        assert coarse.next_position is None
        assert coarse.data.items[0].interval.seconds == 365 * 86400
        assert fake.calls[1]["maximum_points"] == 50000

    asyncio.run(check())


def test_invalid_tied_bookend_and_source_time_false_boundary_are_kept_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        rows = (
            sample(2, -20, "old"),
            sample(None, -10, "invalid"),
            sample(3, -10, "tie"),
            sample(None, -5, "clock", source_eligible=False),
            sample(4, 10, "good"),
        )
        monkeypatch.setattr(module, "read_evidence_window", FakeRead(rows))
        result = await source()(query(end=60), None)
        assert result.data.items[0].value == 4
        assert result.data.items[0].coverage.support_seconds == 50
        assert result.data.items[0].coverage.gaps[0].start == START
        assert result.data.items[0].coverage.gaps[0].end == at(10)

    asyncio.run(check())


def test_signed_evaluation_instant_prevents_future_samples_aging_into_later_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        fake = FakeRead((sample(8, 70, "future"),))
        monkeypatch.setattr(module, "read_evidence_window", fake)
        now = [at(30)]
        q = query(limit=1, end=120)
        first = await source(now=now)(q, None)
        now[0] = at(7200)
        later = await source(now=now)(
            q, Snapshot(watermark=first.watermark, position=first.next_position or "invalid")
        )
        assert later.data.items[0].value is None
        assert fake.calls[0]["evaluated_at"] == fake.calls[1]["evaluated_at"] == at(30)
        assert first.data.revision == later.data.revision
        fresh_query = await source(now=now)(q.model_copy(update={"limit": 2}), None)
        assert fresh_query.data.items[1].value == 8

    asyncio.run(check())


def test_position_is_versioned_canonical_bounded_and_rejects_future_or_invalid_ordinal() -> None:
    valid = encode_position(1, at(0).replace(microsecond=123456))
    assert decode_position(valid, 10, at(1)) == (1, at(0).replace(microsecond=123456))

    def token(value: object) -> str:
        return "a1_" + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    bad = [
        "../private",
        valid + "=",
        "a1_" + "a" * 513,
        token([2, 1, "2026-01-01T00:00:00.000000Z"]),
        token([1, True, "2026-01-01T00:00:00.000000Z"]),
        token([1, 0, "2026-01-01T00:00:00.000000Z"]),
        token([1, 10, "2026-01-01T00:00:00.000000Z"]),
        encode_position(1, at(2)),
    ]
    for value in bad:
        with pytest.raises(PublicInputError) as error:
            decode_position(value, 10, at(1))
        assert error.value.code == "invalid_cursor"


def test_process_singleflight_rejects_overload_and_cancellation_releases_without_background_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        async def waiting(*args: Any, **kwargs: Any) -> EvidenceWindow:
            entered.set()
            await finish.wait()
            return inputs()

        monkeypatch.setattr(module, "read_evidence_window", waiting)
        first_source, other_source = source(), source()
        active = asyncio.create_task(first_source(query(), None))
        await entered.wait()
        with pytest.raises(PublicInputError) as overload:
            await other_source(query(), None)
        assert overload.value.code == "unavailable"
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        monkeypatch.setattr(module, "read_evidence_window", FakeRead())
        assert (await other_source(query(), None)).watermark == "9"

    asyncio.run(check())


def test_closed_source_cancels_its_flight_and_never_starts_another_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        entered = asyncio.Event()

        async def waiting(*args: Any, **kwargs: Any) -> EvidenceWindow:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Not resumed")

        monkeypatch.setattr(module, "read_evidence_window", waiting)
        callback = source()
        active = asyncio.create_task(callback(query(), None))
        await entered.wait()
        await callback.aclose()
        assert active.cancelled()
        with pytest.raises(PublicInputError) as error:
            await callback(query(), None)
        assert error.value.code == "unavailable"
        monkeypatch.setattr(module, "read_evidence_window", FakeRead())
        assert (await source()(query(), None)).watermark == "9"

    asyncio.run(check())


def test_input_work_and_global_evidence_reference_overflow_fail_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        rows = (sample(4, 0, "a"), sample(4, 0, "b", device="other"))
        monkeypatch.setattr(module, "read_evidence_window", FakeRead(rows))
        for settings in ({"maximum_inputs": 1}, {"maximum_work": 1}):
            with pytest.raises(PublicInputError) as error:
                await source(**settings)(query(), None)
            assert error.value.code == "range_too_large"
        monkeypatch.setattr(module, "read_evidence_window", FakeRead((rows[0],)))
        monkeypatch.setattr(module, "MAX_EVIDENCE_REFERENCES", 2)
        with pytest.raises(PublicInputError) as error:
            await source()(query(end=180, limit=3), None)
        assert error.value.code == "range_too_large"

    asyncio.run(check())


def test_mapper_or_driver_errors_are_value_free_and_do_not_leak_into_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        async def failing(*args: Any, **kwargs: Any) -> EvidenceWindow:
            raise RuntimeError("SYNTHETIC-PRIVATE SQL parameters")

        monkeypatch.setattr(module, "read_evidence_window", failing)
        with pytest.raises(PublicInputError) as error:
            await source()(query(), None)
        assert error.value.code == "unavailable" and "PRIVATE" not in str(error.value)

    asyncio.run(check())


def test_actual_index_quantum_yields_and_cancel_does_not_launch_computation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        rows = tuple(sample(2, 0, f"row_{index}", device=f"device_{index}") for index in range(129))
        monkeypatch.setattr(module, "read_evidence_window", FakeRead(rows))
        computed = []
        original = module.compute_window

        def recording(*args: Any, **kwargs: Any) -> Any:
            computed.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "compute_window", recording)
        active = asyncio.create_task(source()(query(), None))
        await asyncio.sleep(0)
        assert not active.done() and not computed
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert not computed
        monkeypatch.setattr(module, "read_evidence_window", FakeRead())
        assert (await source()(query(limit=1), None)).watermark == "9"

    asyncio.run(check())


def test_more_than_one_thousand_actual_evidence_ids_are_retained_without_bundling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        rows = tuple(
            sample(2, 0, f"source_{index}", device=f"device_{index}") for index in range(1001)
        )
        monkeypatch.setattr(module, "read_evidence_window", FakeRead(rows))
        result = await source()(query(end=60, limit=1), None)
        assert len(result.data.items[0].evidence_ids) == 1001
        assert set(result.data.items[0].evidence_ids) == {row.point.evidence_id for row in rows}
        assert result.data.items[0].value == 2

    asyncio.run(check())


def test_unrelated_watermark_changes_snapshot_receipt_without_churning_closed_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        fake = FakeRead((sample(4, 0, "same_source"),))
        monkeypatch.setattr(module, "read_evidence_window", fake)
        first = await source()(query(end=60, limit=1), None)
        fake.bound = "10"
        second = await source()(query(end=60, limit=1), None)
        assert first.watermark != second.watermark
        assert first.data.items == second.data.items
        # The envelope revision names the export snapshot; it is not the stored
        # computation revision. Item evidence/algorithm identities stay unchanged.
        assert first.data.revision != second.data.revision

    asyncio.run(check())


def test_staggered_overlapping_hold_cost_is_rejected_before_synchronous_engine_math(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        rows = tuple(
            sample(2, index, f"row_{index}", device=f"device_{index}") for index in range(80)
        )
        monkeypatch.setattr(module, "read_evidence_window", FakeRead(rows))
        computed = []
        original = module.compute_window

        def recording(*args: Any, **kwargs: Any) -> Any:
            computed.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, "compute_window", recording)
        with pytest.raises(PublicInputError) as error:
            await source(maximum_work=5000)(query(end=180, resolution=180), None)
        assert error.value.code == "range_too_large"
        assert not computed

    asyncio.run(check())
