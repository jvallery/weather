# SPDX-License-Identifier: MIT
"""Pure complete-window contract tests; fake rows only, no database connection."""

import asyncio
from datetime import timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from test_public_sql_source import AT, row
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import Interval
from weather.db.public_evidence import quality_revision, read_evidence_window
from weather.db.public_mapping import history_point
from weather.db.public_source import PublicSqlSource


def test_complete_window_binds_calibrated_bounds_and_keeps_invalid_hold_boundary() -> None:
    async def check() -> None:
        source = PublicSqlSource(
            object(),
            station_alias="public-station",
            maximum_raw_seconds=86400,
            clock=lambda: AT + timedelta(hours=2),
        )
        captured = {}
        before = row(
            observed_at=AT - timedelta(seconds=10),
            publication_seq=1,
            device_alias="public-device",
            source_time_eligible=True,
            value_text=None,
            value_kind="null",
            field_quality={
                "state": "invalid",
                "valid": False,
                "eligible": False,
                "reason": "payload_conflict",
            },
        )
        inside = row(
            publication_seq=2,
            device_alias="public-device",
            source_time_eligible=True,
            value_text="0.12345678901234567890123456789",
        )

        async def rows(statement: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
            captured.update(parameters)
            return [{"watermark": 3, **before}, {"watermark": 3, **inside}]

        source._rows = rows
        window = Interval(start=AT, end=AT + timedelta(hours=1))
        result = await read_evidence_window(
            source,
            window,
            ["temperature"],
            preceding_hold_seconds=90,
            following_report_seconds=60,
            snapshot_watermark="3",
        )
        assert captured["lower"] == AT - timedelta(seconds=90)
        assert captured["upper"] == window.end + timedelta(seconds=60)
        assert captured["watermark"] == 3 and result.watermark == "3"
        assert len(result.rows) == 2 and result.rows[0].source_time_eligible
        assert result.rows[0].point.value is None
        assert result.rows[0].exact_value is None
        assert result.rows[1].point.value == float("0.12345678901234567890123456789")
        assert result.rows[1].exact_value == Decimal("0.12345678901234567890123456789")
        with pytest.raises(PublicInputError) as failure:
            await read_evidence_window(
                source,
                window,
                ["temperature"],
                preceding_hold_seconds=90,
                following_report_seconds=60,
                maximum_points=1,
            )
        assert failure.value.code == "range_too_large"

    asyncio.run(check())


def test_unknown_or_unqualified_report_policy_does_not_create_support() -> None:
    for policy in ("unknown", "v171-rain-firmware-unqualified-1", "private arbitrary policy"):
        value = row(
            field_quality={
                "state": "valid",
                "valid": True,
                "eligible": True,
                "reason": "valid",
                "support": {
                    "kind": "reported_interval",
                    "qualified": True,
                    "reported_seconds": 60,
                    "policy": policy,
                },
            }
        )
        assert history_point(value, AT).support_interval is None


def test_bound_evaluation_instant_controls_sql_and_mapping_without_aging() -> None:
    async def check() -> None:
        clock = AT + timedelta(seconds=30)
        source = PublicSqlSource(
            object(),
            station_alias="public-station",
            maximum_raw_seconds=86400,
            clock=lambda: clock,
        )
        captured = []

        async def rows(statement: Any, parameters: dict[str, Any]) -> list[dict[str, Any]]:
            nonlocal clock
            captured.append(parameters["now"])
            clock += timedelta(seconds=30)
            return [
                {
                    "watermark": 1,
                    **row(
                        observed_at=AT + timedelta(seconds=1),
                        publication_seq=1,
                        device_alias="public-device",
                        source_time_eligible=False,
                        value_text="21",
                    ),
                }
            ]

        source._rows = rows
        window = Interval(start=AT, end=AT + timedelta(seconds=60))
        for _ in range(2):
            result = await read_evidence_window(
                source,
                window,
                ["temperature"],
                preceding_hold_seconds=0,
                following_report_seconds=0,
                snapshot_watermark="1",
                evaluated_at=AT,
            )
            assert result.rows[0].point.value is None
            assert result.rows[0].exact_value is None and not result.rows[0].source_time_eligible
        assert captured == [AT, AT]
        for invalid in (
            AT.replace(tzinfo=None),
            AT.astimezone(timezone(timedelta(hours=1))),
            clock + timedelta(microseconds=1),
        ):
            with pytest.raises(ValueError, match="^Invalid bounded evidence evaluation instant$"):
                await read_evidence_window(
                    source,
                    window,
                    ["temperature"],
                    preceding_hold_seconds=0,
                    following_report_seconds=0,
                    evaluated_at=invalid,
                )
        assert captured == [AT, AT]  # Invalid inputs never issue a SQL read.

    asyncio.run(check())


def test_quality_revision_binds_all_safe_state_without_private_metadata() -> None:
    original = row(
        value_text=None,
        value_kind="null",
        field_quality={
            "state": "invalid",
            "valid": False,
            "eligible": False,
            "reason": "payload_conflict",
            "range_policy": "v171-basic-domains-1",
        },
    )
    first = quality_revision(original)
    private_only = {**original, "quality": {**original["quality"], "private_extra": "private data"}}
    assert quality_revision(private_only) == first
    changed = {
        **original,
        "field_quality": {**original["field_quality"], "range_policy": "unknown"},
    }
    assert quality_revision(changed) != first
