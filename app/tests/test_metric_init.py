# SPDX-License-Identifier: MIT
"""Label sets the slice-2 alerts read with increase() exist at 0 from process start (#49)."""

from typing import get_args

from prometheus_client import REGISTRY
from weather.pipeline import metrics
from weather.pipeline.recipe import CAMERAS
from weather.stills.policy import State


def value(name: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def test_scheduler_zeroes_every_camera_deadline_series() -> None:
    metrics.zero_scheduler()
    for camera in CAMERAS:
        assert value("weather_segments_deadline_missed_total", camera=camera) is not None


def test_pipeline_zeroes_its_camera_latency_and_still_states() -> None:
    camera = "skycam_west"
    metrics.zero_pipeline(camera)
    assert value("weather_pipeline_publish_latency_seconds_count", camera=camera) is not None
    assert value("weather_pipeline_publish_latency_seconds_bucket", camera=camera, le="7200.0") \
        is not None
    for state in ("on_target", "off_target", "missing"):
        assert value("weather_stills_slots_total", camera=camera, state=state) is not None
    # The still states match the selection policy's vocabulary.
    assert set(metrics.STILL_STATES) == set(get_args(State))
