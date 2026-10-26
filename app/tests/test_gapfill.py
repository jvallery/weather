# SPDX-License-Identifier: MIT
"""#144: published renditions never show a flat dark slate."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from weather.pipeline import gapfill
from weather.pipeline.gapfill import Run
from weather.pipeline.recipe import SLATE_RGB

H, W = 180, 320
START = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)


def frame(value: int) -> bytes:
    return np.full((H, W, 3), value, dtype=np.uint8).tobytes()


SLATE = np.full((H, W, 3), SLATE_RGB, dtype=np.uint8).tobytes()


def span(first: int, last: int) -> tuple[datetime, datetime]:
    return START + timedelta(seconds=10 * first), START + timedelta(seconds=10 * last)


def run(slates: list[bool], values: list[int]) -> tuple[list[np.ndarray], gapfill.Stats]:
    source = [SLATE if slate else frame(v) for slate, v in zip(slates, values, strict=True)]
    stats = gapfill.Stats()
    out = list(gapfill.fill(source, slates, (H, W), span, stats))
    return [np.frombuffer(o, dtype=np.uint8).reshape(H, W, 3) for o in out], stats


def test_plan_classifies_short_inner_runs_and_long_or_edge_runs() -> None:
    slates = [True, False, True, False] + [True] * 13 + [False, True, True]
    assert gapfill.plan(slates) == [
        Run(0, 1, "held"),
        Run(2, 3, "interpolated"),
        Run(4, 17, "held"),
        Run(18, 20, "held"),
    ]
    assert gapfill.plan([False] * 5) == []
    with pytest.raises(ValueError):
        gapfill.plan([True, True])


def test_a_single_missing_frame_is_the_midpoint_of_its_neighbours() -> None:
    frames, stats = run([False, True, False], [100, 0, 200])
    assert [int(f[0, 0, 0]) for f in frames] == [100, 150, 200]
    assert (stats.interpolated, stats.held) == (1, 0)


def test_a_short_run_cross_fades_linearly_and_deterministically() -> None:
    slates = [False] + [True] * 3 + [False]
    frames, _ = run(slates, [0, 0, 0, 0, 255])
    assert [int(f[0, 0, 0]) for f in frames] == [0, 64, 128, 191, 255]
    again, _ = run(slates, [0, 0, 0, 0, 255])
    assert all(np.array_equal(a, b) for a, b in zip(frames, again, strict=True))


def test_no_output_frame_is_a_slate_with_scattered_and_long_gaps() -> None:
    rng = np.random.default_rng(144)
    slates = [bool(x) for x in rng.random(600) < 0.085]
    slates[0] = False
    slates[200:380] = [True] * 180  # a 30-minute outage at 10 s per frame
    values = [40 + (k % 150) for k in range(600)]
    frames, stats = run(slates, values)
    assert len(frames) == 600
    slate = np.frombuffer(SLATE, dtype=np.uint8).reshape(H, W, 3)
    assert not any(np.array_equal(f, slate) for f in frames)
    assert stats.interpolated + stats.held == sum(slates)
    assert stats.held >= 180


def test_a_long_gap_holds_the_last_frame_with_a_local_time_label() -> None:
    slates = [False] + [True] * 20 + [False]
    frames, stats = run(slates, [90] + [0] * 20 + [180])
    assert stats.held == 20
    held = frames[1]
    assert all(np.array_equal(held, f) for f in frames[1:21])
    # Most of the held frame is the last rendered frame; the label band changes a corner.
    assert np.count_nonzero(held != 90) > 0
    assert np.mean(held == 90) > 0.5
    assert int(frames[21][0, 0, 0]) == 180
    # 14:00:10Z to 14:03:30Z is 08:00-08:03 in America/Denver (MDT).
    assert gapfill.gap_label(*span(1, 21)) == "Capture gap 08:00–08:03"


def test_leading_and_trailing_runs_hold_the_nearest_rendered_frame() -> None:
    frames, stats = run([True, True, False, True], [0, 0, 120, 0])
    assert int(frames[2][0, 0, 0]) == 120
    assert np.mean(frames[0] == 120) > 0.5 and np.array_equal(frames[0], frames[1])
    assert np.mean(frames[3] == 120) > 0.5
    assert (stats.interpolated, stats.held) == (0, 3)


def test_a_frame_count_mismatch_is_an_error() -> None:
    with pytest.raises(ValueError):
        list(gapfill.fill([frame(1)], [False, False], (H, W), span))
    with pytest.raises(ValueError):
        list(gapfill.fill([frame(1), frame(2)], [False], (H, W), span))
