# SPDX-License-Identifier: MIT
"""Keogram goldens (#37): bin ownership and tie-breaks, gaps, linear light, chunk-boundary
byte identity, assembly from stored bins alone, failure isolation and region validation.

The renderer-driven checks decode tiny synthetic JPEGs with FFmpeg, so like the renderer's
own tests they run in fleet CI (or locally with WEATHER_FFMPEG); the rest are pure."""

import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from weather.pipeline import ffmpeg, synthetic
from weather.pipeline.intake import Candidate, ChunkIndex, Expected
from weather.pipeline.keogram import (
    BINS,
    DEFAULT_REGIONS,
    GAP_RGB,
    BinMap,
    KeogramStage,
    Region,
    RegionConfig,
    Sample,
    assemble,
    column,
    gap_column,
    load_regions,
    micros,
    sidecar,
)
from weather.pipeline.keogram_publish import embargo
from weather.pipeline.light import to_srgb
from weather.pipeline.recipe import Recipe
from weather.pipeline.render import SegmentJob, SegmentRenderer
from weather.publish.model import ProductFile

W, H = 32, 16
T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
NS = synthetic.NS
F = Fraction
REGION = Region(version="test-1", x=(12, 20), y=(0, H), orientation="vertical")


def ffmpeg_available() -> bool:
    try:
        return bool(ffmpeg.version())
    except ffmpeg.FFmpegError, OSError, subprocess.SubprocessError:
        return False


needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available() and os.environ.get("WEATHER_CI_IN_CLUSTER") != "1",
    reason="FFmpeg runs in fleet CI (set WEATHER_FFMPEG to run locally)",
)


def job(seconds: int = BINS, kind: str = "daytime", start: datetime = T0) -> SegmentJob:
    return SegmentJob(
        "skycam_east", date(2026, 10, 23), kind, 1, start, start + timedelta(seconds=seconds), 60
    )


def candidate(time: Fraction, capture_id: str = "a", upper: Fraction | None = None) -> Candidate:
    return Candidate(
        slot=time,
        time=time,
        upper=upper if upper is not None else time + F(1, 10),
        freshness="verified",
        uncertainty_ms=50.0,
        attempt_id=capture_id,
        chunk=Path("c.tar"),
        member="frames/x.jpg",
        sha256="0" * 64,
        size=1,
    )


def at(segment: SegmentJob, offset: Fraction | int) -> Fraction:
    return F(micros(segment.starts_at), 1_000_000) + offset


def solid(value: int) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


class Store:
    """The bin store's merge rule, in memory: the nearer, then earlier, then lower ID wins."""

    def __init__(self) -> None:
        self.bins: dict[int, Sample] = {}
        self.failed: str | None = None
        self.calls = 0

    def __call__(self, job: SegmentJob, region: Region, samples: list[Sample], failed: Any) -> None:
        self.calls += 1
        self.failed = self.failed or failed
        for sample in samples:
            kept = self.bins.get(sample.bin)
            if kept is None or sample.key < kept.key:
                self.bins[sample.bin] = sample


# Ownership and selection ----------------------------------------------------------------------


def test_a_capture_exactly_at_dusk_belongs_to_the_overnight_segment() -> None:
    day = job(3600, "daytime")
    night = job(3600, "overnight", start=day.ends_at)
    dusk = micros(day.ends_at)
    assert BinMap.of(day).index(dusk) is None
    assert BinMap.of(night).index(dusk) == 0
    assert BinMap.of(day).index(dusk - 1) == BINS - 1
    store = Store()
    stage = KeogramStage(REGION, store)
    capture = candidate(F(dusk, 1_000_000))
    for segment in (day, night):
        stage.frame(segment, capture, solid(200))
        stage.commit(segment, 0, 60)
    assert list(store.bins) == [0]  # only the overnight segment's first bin


def test_bins_never_borrow_a_neighbouring_bins_capture() -> None:
    segment = job()  # 1 s bins
    store = Store()
    stage = KeogramStage(REGION, store)
    # Bin 10's only capture sits at its very start; bins 9 and 11 stay empty.
    stage.frame(segment, candidate(at(segment, 10)), solid(200))
    stage.commit(segment, 0, 60)
    assert sorted(store.bins) == [10]
    assembled = assemble(list(store.bins.values()), REGION.height)
    assert assembled.gaps == [(0, 10), (11, BINS)]


def test_nearest_midpoint_then_earlier_then_capture_id() -> None:
    segment = job()  # bin 5 is [5, 6) s, midpoint 5.5 s
    store = Store()
    stage = KeogramStage(REGION, store)
    late, early = candidate(at(segment, F(57, 10)), "z"), candidate(at(segment, F(53, 10)), "y")
    nearest = candidate(at(segment, F(56, 10)), "x")
    for item in (late, early):  # equidistant: the earlier capture wins
        stage.frame(segment, item, solid(10 if item is early else 250))
    stage.commit(segment, 0, 60)
    assert store.bins[5].capture_id == "y"
    stage.frame(segment, nearest, solid(99))
    stage.commit(segment, 60, 120)
    assert store.bins[5].capture_id == "x"
    # The same instant and distance: the lower capture ID, whatever the arrival order.
    for order in (("b", "a"), ("a", "b")):
        other = Store()
        stage = KeogramStage(REGION, other)
        for capture_id in order:
            stage.frame(segment, candidate(at(segment, 7), capture_id), solid(1))
        stage.commit(segment, 0, 60)
        assert other.bins[7].capture_id == "a"


def test_distance_is_exact_for_uneven_bins() -> None:
    bins = BinMap(0, 1441)  # bins of 1441/1440 us
    assert bins.index(1440) == 1439 and bins.index(1) == 0
    # |t - midpoint| * 2880: bin 0's midpoint is 1441/2880 us.
    assert bins.distance(0, 0) == 1441 and bins.distance(0, 1) == 1439


# Columns, gaps and assembly -------------------------------------------------------------------


def test_linear_light_column_is_not_a_gamma_space_mean() -> None:
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[:, 16:20] = 255  # half the region is white
    strip = np.frombuffer(column(frame, REGION), dtype=np.uint8)
    expected = int(to_srgb(np.array([0.5], dtype=np.float32))[0])
    assert expected == 188
    assert set(strip.tolist()) == {expected}  # a gamma-space mean would be 128


def test_spatial_axis_is_at_most_512_and_never_upscaled() -> None:
    narrow = Region(version="t", x=(0, 4), y=(0, 300), orientation="vertical")
    tall = Region(version="t", x=(0, 4), y=(0, 1856), orientation="vertical")
    wide = Region(version="t", x=(0, 1000), y=(10, 12), orientation="horizontal")
    frame = np.full((1856, 1000, 3), 77, dtype=np.uint8)
    assert len(column(frame, narrow)) == 300 * 3
    assert len(column(frame, tall)) == 512 * 3
    assert len(column(frame, wide)) == 512 * 3
    assert set(column(frame, tall)) == {77}


def test_a_one_hour_outage_is_a_run_of_visible_gap_columns() -> None:
    segment = job(4 * 3600)  # 10 s bins
    store = Store()
    stage = KeogramStage(REGION, store)
    outage = range(3600, 7200)
    for second in range(0, 4 * 3600, 2):
        if second not in outage:
            stage.frame(segment, candidate(at(segment, second), f"c{second}"), solid(180))
    stage.commit(segment, 0, 60)
    assembled = assemble(list(store.bins.values()), REGION.height)
    assert assembled.gaps == [(360, 720)]
    assert assembled.observed == BINS - 360 and assembled.state == ("partial", "gaps")
    from io import BytesIO

    from PIL import Image

    image = np.asarray(Image.open(BytesIO(assembled.png)))
    assert image.shape == (H, BINS, 3)
    for index in (359, 720):  # neighbours keep their own pixels: nothing is stretched
        assert set(image[:, index].flatten().tolist()) == {180}
    for index in range(360, 720):
        assert (image[:, index] == gap_column(index, H)).all()
    assert {tuple(p) for p in image[:, 400]} == set(GAP_RGB)
    thumb = np.asarray(Image.open(BytesIO(assembled.thumbnail)))
    assert thumb.shape == (H // 4, BINS // 4, 3)


def test_sidecar_lists_bin_map_gaps_lineage_and_region_without_private_fields() -> None:
    segment = job(4 * 3600)  # 10 s bins
    start = micros(segment.starts_at)

    def sample(index: int, upper: int | None = None) -> Sample:
        captured = start + index * 10_000_000
        return Sample(index, 0, captured, "c", upper or captured + 100_000, None, bytes(H * 3))

    samples = [sample(index) for index in range(0, BINS, 2)]
    assembled = assemble(samples, H)
    # Captures that end before the boundary: the segment's own end + 630 s guards.
    pnb = embargo(segment, assembled)
    assert pnb == segment.ends_at + timedelta(seconds=630)
    # The newest bin's capture upper bound + 600 s + 30 s skew, when that is later.
    late = [*samples[:-1], sample(1438, micros(segment.ends_at) + 5_000_000)]
    assert embargo(segment, assemble(late, H)) == segment.ends_at + timedelta(seconds=635)
    document = sidecar(segment, REGION, samples, assembled, pnb)
    ProductFile.sidecar(document)  # passes the public-document checks
    assert document["bins"]["count"] == BINS
    assert F(**document["bins"]["step_seconds"]) == F(10)
    assert document["coverage"]["gaps"][:2] == [[1, 2], [3, 4]]
    assert document["captures"][1] is None and document["captures"][0] is not None
    assert document["provenance"]["region"]["version"] == "test-1"
    assert "capture_id" not in json.dumps(document)


# Failure isolation ----------------------------------------------------------------------------


def test_an_extraction_failure_marks_the_keogram_failed_and_never_raises() -> None:
    store = Store()
    outside = Region(version="t", x=(0, 64), y=(0, H), orientation="vertical")
    stage = KeogramStage(outside, store)
    segment = job()
    stage.frame(segment, candidate(at(segment, 3)), solid(5))  # region wider than the frame
    stage.frame(segment, candidate(at(segment, 4)), solid(5))
    stage.commit(segment, 0, 60)
    assert store.failed == "extraction" and not store.bins


# Region configuration -------------------------------------------------------------------------


def test_region_schema_rejects_empty_and_out_of_frame_regions() -> None:
    config = load_regions()
    assert config == load_regions(DEFAULT_REGIONS)
    for camera in ("skycam_east", "skycam_west", "frontyard_1"):
        region = config.region(camera, 4096, 1856)
        assert region.mask == "none" and region.height == 512
    with pytest.raises(ValueError):
        config.region("skycam_east", 1920, 1080)
    document = json.loads(DEFAULT_REGIONS.read_text())
    bad: list[dict[str, Any]] = [
        {"x": [10, 10]},  # empty
        {"y": [5, 2]},  # reversed
        {"x": [4000, 4097]},  # out of frame
        {"y": [0, 1857]},
        {"x": [-1, 3]},
        {"mask": "blur"},
        {"orientation": "diagonal"},
    ]
    for change in bad:
        broken = json.loads(json.dumps(document))
        broken["cameras"]["frontyard_1"].update(change)
        with pytest.raises(ValidationError):
            RegionConfig.model_validate(broken)
    missing = json.loads(json.dumps(document))
    del missing["cameras"]["frontyard_1"]
    with pytest.raises(ValidationError):
        RegionConfig.model_validate(missing)


# Through the renderer -------------------------------------------------------------------------


def _render(staging: Path, segment: SegmentJob, store: Store, *, chunk_seconds: int) -> None:
    frames = [
        synthetic.Frame(int(T0.timestamp() + s) * NS, synthetic.jpeg(synthetic.sky(W, H, s)))
        for s in range(0, 240)
        if not 100 <= s < 130
    ]
    synthetic.write_chunks(
        staging, "skycam_east", frames, geometry=(W, H), chunk_seconds=chunk_seconds
    )
    expected = Expected("skycam_east", 1000, 0, W, H)
    renderer = SegmentRenderer(
        segment,
        staging=staging,
        expected=expected,
        recipe=Recipe(),
        index=ChunkIndex(staging, expected),
        block=25,
        chunk_seconds=chunk_seconds,
        stages=[KeogramStage(REGION, store)],
    )
    renderer.advance(F(10**12))
    assert renderer.complete


@needs_ffmpeg
def test_chunk_boundaries_never_change_the_keogram_and_assembly_needs_no_raw_chunks(
    tmp_path: Path,
) -> None:
    segment = SegmentJob(
        "skycam_east", date(2026, 10, 23), "daytime", 1, T0, T0 + timedelta(seconds=240), 120
    )
    results = []
    for chunk_seconds in (60, 30, 20):
        staging = tmp_path / str(chunk_seconds)
        store = Store()
        _render(staging, segment, store, chunk_seconds=chunk_seconds)
        assert store.calls == 5 and store.failed is None
        # The raw chunks are gone; the stored bins alone assemble the keogram.
        shutil.rmtree(staging / "sealed")
        assembled = assemble(list(store.bins.values()), REGION.height)
        results.append((assembled.png, assembled.thumbnail, assembled.lineage, assembled.gaps))
    assert results[0] == results[1] == results[2]
    # 240 s at 1 Hz in 1,440 bins: one capture per 6 bins, and the 30 s outage is wider.
    gaps = results[0][3]
    assert sum(b - a for a, b in gaps) == BINS - 210
    assert max(b - a for a, b in gaps) >= 30 * 6 - 6


def test_a_bin_split_across_blocks_keeps_its_best_capture_in_either_order() -> None:
    segment = job()  # bin 5 is [5, 6) s
    better, worse = candidate(at(segment, F(54, 10)), "b"), candidate(at(segment, F(59, 10)), "w")
    for order in ((worse, better), (better, worse)):
        store = Store()
        stage = KeogramStage(REGION, store)
        for block, item in enumerate(order):  # one capture per block commit
            stage.frame(segment, item, solid(10 + block))
            stage.commit(segment, 60 * block, 60 * (block + 1))
        assert store.bins[5].capture_id == "b"


def test_only_transient_store_errors_defer_the_block() -> None:
    segment = job()

    def refused(*_: Any) -> None:
        raise ValueError("invalid input for type")

    def lost(*_: Any) -> None:
        raise ConnectionResetError("connection lost")

    stage = KeogramStage(REGION, refused)
    stage.frame(segment, candidate(at(segment, 3)), solid(5))
    stage.commit(segment, 0, 60)  # never raises: the keogram fails, the video carries on
    assert stage.failed[segment.key] == "store"
    stage = KeogramStage(REGION, lost)
    with pytest.raises(ConnectionResetError):
        stage.commit(segment, 0, 60)
    assert segment.key not in stage.failed


def test_upper_bounds_round_up_and_capture_times_down() -> None:
    segment = job()
    store = Store()
    stage = KeogramStage(REGION, store)
    time = at(segment, 3) + F(1, 3_000_000)
    stage.frame(segment, candidate(time, upper=time + F(1, 3_000_000)), solid(5))
    stage.commit(segment, 0, 60)
    sample = store.bins[3]
    assert sample.captured == micros(segment.starts_at) + 3_000_000
    assert sample.upper == micros(segment.starts_at) + 3_000_001


def test_a_bad_region_file_disables_keograms_without_stopping_the_camera(tmp_path: Path) -> None:
    from weather.pipeline.worker import PipelineConfig

    good = PipelineConfig.from_environment("frontyard_1", {})
    assert good.region is not None and not good.region_error
    broken = tmp_path / "regions.json"
    broken.write_text("{not json")
    for env in (
        {"WEATHER_KEOGRAM_REGIONS": str(broken)},
        {"WEATHER_KEOGRAM_REGIONS": str(tmp_path / "missing.json")},
        {"WEATHER_PIPELINE_GEOMETRY": "1920x1080"},  # regions chosen for another geometry
    ):
        config = PipelineConfig.from_environment("frontyard_1", env)
        assert config.region is None and config.region_error, env


def test_connectivity_errors_are_told_apart_from_data_errors() -> None:
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
    from weather.pipeline.keogram_publish import connectivity

    data = DBAPIError("INSERT", {}, Exception("value too long for type character varying(64)"))
    assert not connectivity(data)  # asyncpg data errors arrive as a plain DBAPIError
    assert not connectivity(ValueError("bad strip"))
    assert connectivity(OperationalError("SELECT", {}, Exception("server closed")))
    assert connectivity(TimeoutError()) and connectivity(ConnectionRefusedError())
    dropped = InterfaceError("SELECT", {}, Exception("connection is closed"),
                             connection_invalidated=True)
    assert connectivity(dropped)
    assert not connectivity(InterfaceError("SELECT", {}, Exception("cursor already closed")))
