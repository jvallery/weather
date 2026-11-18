# SPDX-License-Identifier: MIT
"""#146: a complete segment is finalized in a task, off the loop, with one writer per segment."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from weather.pipeline import schedule
from weather.pipeline.intake import Expected
from weather.pipeline.recipe import Recipe
from weather.pipeline.render import SegmentJob
from weather.pipeline.worker import PipelineConfig, Worker

CAMERA = "skycam_east"
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)


def _row() -> schedule.SegmentRow:
    job = SegmentJob(CAMERA, date(2026, 10, 23), "daytime", 1, NOW, NOW, 120)
    return schedule.SegmentRow(id=uuid.uuid4(), state="processing", job=job)


def _worker(tmp_path: Path) -> Worker:
    config = PipelineConfig(
        camera=CAMERA, staging=tmp_path, expected=Expected(CAMERA, 2000, 0, 64, 48), recipe=Recipe()
    )
    return Worker(config, None, None, None, background_finalize=True)  # type: ignore[arg-type]


def _complete() -> Any:
    geometry = SimpleNamespace(start=Fraction(0), end=Fraction(10), owned=lambda *_: (0, 0))
    return SimpleNamespace(
        complete=True, rendered=None, geometry=geometry, advance=lambda *_, **__: 0
    )


def test_the_loop_returns_while_a_complete_segment_finalizes_and_never_starts_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _row()

    async def active(*_: object) -> list[schedule.SegmentRow]:
        return [row]

    monkeypatch.setattr(schedule, "active", active)
    worker = _worker(tmp_path)
    renderers: list[uuid.UUID] = []

    def renderer(segment: schedule.SegmentRow) -> Any:
        renderers.append(segment.id)
        return _complete()

    monkeypatch.setattr(worker, "renderer", renderer)
    release = asyncio.Event()
    calls: list[uuid.UUID] = []

    async def finalize(segment: schedule.SegmentRow, *_: object) -> None:
        calls.append(segment.id)
        await release.wait()

    monkeypatch.setattr(worker, "_finalize", finalize)

    async def scenario() -> None:
        await worker.step(NOW)  # returns with the finalize still running
        await asyncio.sleep(0)  # let the task start
        assert list(worker.finalizing) == [row.id] and calls == [row.id]
        task = worker.finalizing[row.id]
        await worker.step(NOW)  # the same segment again: skipped, not rendered or finalized
        await worker.step(NOW)
        assert worker.finalizing == {row.id: task} and calls == [row.id]
        assert renderers == [row.id]
        release.set()
        await task
        assert worker.finalizing == {}

    asyncio.run(scenario())


def test_one_finalize_runs_at_a_time_per_camera(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    running, peak, done = 0, 0, []

    async def finalize(segment: schedule.SegmentRow, *_: object) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        done.append(segment.id)

    worker._finalize = finalize  # type: ignore[method-assign]
    rows = [_row(), _row(), _row()]

    async def scenario() -> None:
        for row in rows:
            worker._start_finalize(row, _complete())
            worker._start_finalize(row, _complete())  # a repeat while in flight is ignored
        assert len(worker.finalizing) == 3
        await asyncio.gather(*worker.finalizing.values())

    asyncio.run(scenario())
    assert peak == 1 and sorted(done) == sorted(r.id for r in rows)


def test_a_failed_finalize_backs_off_with_its_stage_and_frees_the_segment(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    row = _row()

    async def finalize(
        segment: schedule.SegmentRow, renderer: Any, now: datetime, progress: list[str]
    ) -> None:
        progress[0] = "publish"
        raise RuntimeError("killed")

    worker._finalize = finalize  # type: ignore[method-assign]

    async def scenario() -> None:
        worker._start_finalize(row, _complete())
        await asyncio.gather(*worker.finalizing.values())

    asyncio.run(scenario())
    assert worker.finalizing == {}
    failures, retry_at = worker.backoff[row.id]
    assert failures == 1 and retry_at > NOW
