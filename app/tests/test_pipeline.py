# SPDX-License-Identifier: MIT
"""Renderer unit tests: exact exposure math, linear light, intake, coverage states, results.

FFmpeg-backed tests use tiny synthetic frames (tens of pixels, a few hundred frames at most).
They run in fleet CI, which has the pinned FFmpeg; elsewhere they skip unless WEATHER_FFMPEG
points at a working binary.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from weather.pipeline import ffmpeg, synthetic
from weather.pipeline.exposure import Geometry, coverage, supports
from weather.pipeline.finalize import (
    RESULT,
    gap_ranges,
    poster_frame,
    prepare,
    rendition_specs,
    slot_state,
)
from weather.pipeline.intake import (
    Candidate,
    ChunkIndex,
    Expected,
    IntakeError,
    instant,
    read_frame,
    select,
)
from weather.pipeline.light import to_linear, to_srgb
from weather.pipeline.recipe import Recipe, from_environment
from weather.pipeline.render import SegmentJob, SegmentRenderer
from weather.pipeline.result import RenderResult
from weather.publish.model import ProductFile

W, H = 32, 16
T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
NS = synthetic.NS


def ffmpeg_available() -> bool:
    try:
        return bool(ffmpeg.version())
    except ffmpeg.FFmpegError, OSError, subprocess.SubprocessError:
        return False


HAVE_FFMPEG = ffmpeg_available()
needs_ffmpeg = pytest.mark.skipif(
    not HAVE_FFMPEG and os.environ.get("WEATHER_CI_IN_CLUSTER") != "1",
    reason="FFmpeg runs in fleet CI (set WEATHER_FFMPEG to run locally)",
)


F = Fraction


# Linear light and exposure geometry ---------------------------------------------------------


def test_linear_light_mix_of_black_and_white_is_188_not_128() -> None:
    black, white = to_linear(np.array([0], dtype=np.uint8)), to_linear(np.array([255], np.uint8))
    mixed = to_srgb((black + white) / np.float32(2))
    assert int(mixed[0]) == 188
    assert (0 + 255) // 2 == 127  # the gamma-space mistake this guards against
    # The tables round-trip every 8-bit code exactly.
    codes = np.arange(256, dtype=np.uint8)
    assert np.array_equal(to_srgb(to_linear(codes)), codes)


def test_fractional_shutter_overlap_is_exact() -> None:
    g = Geometry(F(0), F(120), 10, F(1), 180)
    assert g.step == 12 and g.width == 6 and g.shutter(0) == (F(3), F(9))
    # One capture at 3.25 supports [2.75, 3.75): it covers 0.75 s of [3, 9).
    assert coverage(g, 0, supports(g, [F("3.25")])) == F(75, 600)
    # Contiguous 1 Hz captures cover the whole shutter.
    times = [F(t) + F(1, 2) for t in range(120)]
    assert all(coverage(g, k, supports(g, times)) == 1 for k in range(10))
    # 360 degrees doubles the width; the same captures still cover all of it.
    wide = replace(g, shutter_degrees=360)
    assert wide.width == 12 and coverage(wide, 3, supports(wide, times)) == 1


def test_missed_slots_leave_holes_instead_of_stretching_neighbours() -> None:
    g = Geometry(F(0), F(60), 6, F(1), 360)
    # Captures only at 0.5 and 9.5: frame 0's shutter [0, 10) gets 2 s, never 10.
    intervals = supports(g, [F("0.5"), F("9.5")])
    assert intervals == [(F(0), F(1)), (F(9), F(10))]
    assert coverage(g, 0, intervals) == F(1, 5)
    assert coverage(g, 1, intervals) == 0


def test_coverage_exactly_090_renders_and_08999_slates() -> None:
    g = Geometry(F(0), F(20), 2, F(1), 360)
    first = [F(t) + F(1, 2) for t in range(9)]
    second = [F(10 + t) + F(1, 2) for t in range(8)] + [F("18.499")]
    intervals = supports(g, first + second)
    assert coverage(g, 0, intervals) == F(9, 10)
    assert coverage(g, 1, intervals) == F(8999, 10000)
    threshold = Recipe().min_exposure_coverage
    assert coverage(g, 0, intervals) >= threshold > coverage(g, 1, intervals)


def test_half_open_boundaries_and_ownership() -> None:
    g = Geometry(F(100), F(220), 10, F(1), 180)
    with pytest.raises(ValueError):
        supports(g, [F(220)])  # a capture at b belongs to the next segment
    assert supports(g, [F(100)])[0][0] == F(100)  # supports never reach before a
    assert g.owned(0, 3) == (F(100), F(136)) and g.owned(9, 10) == (F(208), F(220))
    assert list(g.overlapping((F(100), F(220)))) == list(range(10))
    # Frame 0's shutter is [103, 109) and frame 1's is [115, 121).
    assert list(g.overlapping((F(110), F(114)))) == []
    assert list(g.overlapping((F(108), F(109)))) == [0]
    assert list(g.overlapping((F(109), F(115)))) == []
    assert list(g.overlapping((F(108), F("115.5")))) == [0, 1]
    frame = Candidate(F(220), F(220), F(220), "unknown", None, "a", Path("x"), "m", "0" * 64, 1)
    assert select([frame], F(100), F(220)).frames == []
    assert select([frame], F(220), F(340)).frames == [frame]


def test_shutter_default_and_selectable_angles_are_recorded() -> None:
    default = Recipe()
    assert default.shutter_degrees == 180 and default.record()["shutter_degrees"] == 180
    wide = from_environment({"WEATHER_PIPELINE_SHUTTER_DEGREES": "360"})
    assert wide.record()["shutter_degrees"] == 360 and wide.id != default.id
    assert Recipe(min_exposure_coverage=F(8, 10)).id != default.id
    assert default.id == Recipe(encoder="x", image="sha256:" + "a" * 64).id
    for bad in ({"WEATHER_PIPELINE_SHUTTER_DEGREES": "200"}, {"WEATHER_IMAGE_DIGEST": "latest"}):
        with pytest.raises(ValueError):
            from_environment(bad)


# Intake --------------------------------------------------------------------------------------


def candidate(
    slot: int, *, time: str | None = None, freshness: str = "unknown", **kw: object
) -> Candidate:
    values: dict[str, object] = dict(
        slot=F(slot),
        time=F(time) if time is not None else F(slot) + F(1, 10),
        upper=F(slot + 1),
        freshness=freshness,
        uncertainty_ms=None,
        attempt_id=f"a{slot}-{freshness}-{time}",
        chunk=Path("c.tar"),
        member="frames/x.jpg",
        sha256="0" * 64,
        size=1,
    )
    values.update(kw)
    return Candidate(**values)  # type: ignore[arg-type]


def test_duplicate_slots_keep_one_best_attempt() -> None:
    verified = candidate(10, freshness="verified", time="10.4")
    plausible = candidate(10, freshness="plausible", time="10.1", uncertainty_ms=1.0)
    unknown = candidate(10, time="10.0")
    window = select([unknown, plausible, verified], F(0), F(100))
    assert window.frames == [verified] and window.duplicates == 2
    tight = candidate(11, freshness="plausible", time="11.3", uncertainty_ms=5.0)
    loose = candidate(11, freshness="plausible", time="11.1", uncertainty_ms=50.0)
    assert select([loose, tight], F(0), F(100)).frames == [tight]
    early, late = candidate(12, time="12.1"), candidate(12, time="12.2")
    assert select([late, early], F(0), F(100)).frames == [early]
    # A rejected attempt never displaces an accepted one; an all-rejected slot keeps its reason.
    bad = candidate(13, reject="geometry")
    window = select([bad, candidate(13), candidate(14, reject="unknown_timing")], F(0), F(100))
    assert [c.slot for c in window.frames] == [F(13)]
    assert window.rejected == {F(14): (F("14.1"), "unknown_timing")}


def _expected(camera: str = "skycam_east") -> Expected:
    return Expected(camera, 1000, 0, W, H)


def _frames(seconds: range, offset_ns: int = 100_000_000, **kw: object) -> list[synthetic.Frame]:
    return [
        synthetic.Frame(
            int(T0.timestamp() + s) * NS,
            synthetic.jpeg(synthetic.sky(W, H, s)),
            offset_ns=offset_ns,
            **kw,  # type: ignore[arg-type]
        )
        for s in seconds
    ]


def test_gaps_come_from_grid_slots_and_a_sealed_frame_beats_a_loss_record(tmp_path: Path) -> None:
    loss = {
        "record_type": "loss_range",
        "failure_class": "persist_failed",
        "first_slot_id": "lost-0",
        "last_slot_id": "lost-9",
        "first_slot_at_utc": "2026-10-23T15:00:00.000000Z",
        "last_slot_at_utc": "2026-10-23T15:00:09.000000Z",
        "attempt_count": 10,
        "individual_details_lost": True,
    }
    # Slots 0-9 are reported lost by one incarnation but sealed by another.
    synthetic.write_chunks(tmp_path, "skycam_east", _frames(range(0, 10)), geometry=(W, H))
    synthetic.write_chunks(
        tmp_path,
        "skycam_east",
        _frames(range(20, 30)),
        geometry=(W, H),
        pod="second",
        extra_records=[loss],
    )
    index = ChunkIndex(tmp_path, _expected())
    start = instant("2026-10-23T15:00:00Z")
    index.refresh(start, start + 60)
    window = index.window(start, start + 60)
    assert {c.slot - start for c in window.frames} == set(range(10)) | set(range(20, 30))
    assert window.reported_losses == 10
    grid = _expected().grid(start, start + 60)
    assert len(grid) == 60 and grid[0] == start
    missing = {slot for slot in grid} - {c.slot for c in window.frames}
    assert len(missing) == 40  # gaps are grid slots without a frame, not the loss record


def test_strict_intake_rejects_unknown_timing_geometry_and_corrupt_tar(tmp_path: Path) -> None:
    synthetic.write_chunks(
        tmp_path,
        "skycam_east",
        _frames(range(0, 2), extra=(("capture_upper_bound", None), ("clock_discontinuity", True))),
        geometry=(W, H),
    )
    synthetic.write_chunks(
        tmp_path, "skycam_east", _frames(range(60, 62)), geometry=(W + 2, H), pod="other"
    )
    synthetic.write_chunks(
        tmp_path, "skycam_east", _frames(range(120, 122)), geometry=(W, H), phase_ms=500, pod="p"
    )
    good = synthetic.write_chunks(
        tmp_path, "skycam_east", _frames(range(180, 182)), geometry=(W, H), pod="good"
    )
    broken = good[0].with_name("broken.tar")
    data = bytearray(good[0].read_bytes())
    data[1024] ^= 0xFF  # inside the first frame member: its sha256 no longer matches
    broken.write_bytes(bytes(data))
    index = ChunkIndex(tmp_path, _expected())
    start = instant("2026-10-23T15:00:00Z")
    index.refresh(start, start + 300)
    window = index.window(start, start + 300)
    reasons = sorted({reason for _, reason in window.rejected.values()})
    assert reasons == ["geometry", "settings", "unknown_timing"]
    assert [c.slot - start for c in window.frames] == [180, 181]
    assert index.invalid == {broken}
    assert tarfile.is_tarfile(good[0])
    # Bytes read later are re-verified against the sealed manifest.
    frame = window.frames[0]
    assert read_frame(frame)[:2] == b"\xff\xd8" and frame.offset is not None
    data = bytearray(frame.chunk.read_bytes())
    data[frame.offset + 10] ^= 0xFF
    frame.chunk.write_bytes(bytes(data))
    with pytest.raises(IntakeError):
        read_frame(frame)


def test_slot_coverage_maps_to_catalog_states() -> None:
    recipe = Recipe()
    assert slot_state(F(1), recipe) == ("published", None)
    assert slot_state(F(99, 100), recipe) == ("published", None)
    assert slot_state(F(98, 100), recipe) == ("partial", "partial_coverage")
    assert slot_state(F(90, 100), recipe) == ("partial", "partial_coverage")
    assert slot_state(F(8999, 10000), recipe) == ("partial", "low_coverage")
    assert slot_state(F(50, 100), recipe) == ("partial", "low_coverage")
    assert slot_state(F(4999, 10000), recipe) == ("unavailable", "insufficient_coverage")


def test_rendition_geometry_is_even_and_never_upscaled() -> None:
    specs = {s.role: (s.width, s.height) for s in rendition_specs(4096, 1856)}
    assert specs == {"native": (4096, 1856), "web": (1920, 870), "mobile": (1280, 580)}
    small = {s.role: (s.width, s.height) for s in rendition_specs(33, 17)}
    assert small == {"native": (32, 16), "web": (32, 16), "mobile": (32, 16)}


def test_gap_ranges_and_poster_choice_avoid_slates() -> None:
    slates = [True, False, False, True, True, False, True]
    assert gap_ranges(slates) == [(0, 1), (3, 5), (6, 7)]
    assert poster_frame(slates) == 2  # nearest observed frame to the middle (3)
    assert poster_frame([True, True]) is None


def _result(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "camera": "skycam_east",
        "cycle_date": "2026-10-23",
        "segment": "daytime",
        "revision": 1,
        "frames": 10,
        "starts_at": "2026-10-23T13:00:00+00:00",
        "ends_at": "2026-10-23T23:00:00+00:00",
        "public_not_before": "2026-10-23T23:10:30+00:00",
        "state": "published",
        "reason": None,
        "recipe": Recipe().record(),
        "input_manifest_sha256": "a" * 64,
        "inputs": 5,
        "slots": {
            "expected": 10,
            "accepted": 10,
            "fraction": 1.0,
            "rejected": {},
            "missing": 0,
            "duplicates": 0,
            "reported_losses": 0,
        },
        "exposure": {"rendered": 8, "gaps": [[2, 4]], "threshold": "9/10"},
        "renditions": [
            {"role": r, "width": 2, "height": 2, "frames": 10, "sha256": "b" * 64, "bytes": 1}
            for r in ("native", "web", "mobile")
        ],
        "posters": [
            {
                "role": "poster",
                "extension": "jpg",
                "width": 2,
                "height": 2,
                "frame": 5,
                "sha256": "c" * 64,
                "bytes": 1,
            }
        ],
        "sidecar_sha256": "d" * 64,
        "expected_revision": 0,
    }
    base.update(overrides)
    return base


def test_render_result_validates_and_refuses_inconsistent_records() -> None:
    RenderResult.model_validate(_result())
    poster = dict(_result()["posters"][0])  # type: ignore[index]
    bad = [
        {"posters": [{**poster, "frame": 3}]},  # a slate poster
        {"renditions": []},
        {"state": "unavailable", "reason": "insufficient_coverage"},  # media on unavailable
        {"public_not_before": "2026-10-23T23:05:00+00:00"},
        {"state": "partial"},  # no reason
        {"exposure": {"rendered": 9, "gaps": [[2, 4]], "threshold": "9/10"}},
        {"unexpected": True},
    ]
    for override in bad:
        with pytest.raises(ValueError):
            RenderResult.model_validate(_result(**override))
    RenderResult.model_validate(
        _result(state="unavailable", reason="insufficient_coverage", renditions=[], posters=[])
    )


# FFmpeg-backed -----------------------------------------------------------------------------


@needs_ffmpeg
def test_decode_rejects_corrupt_entropy_and_truncation() -> None:
    good = synthetic.jpeg(synthetic.sky(W, H, 1))
    assert len(ffmpeg.decode_jpeg(good, W, H) or b"") == W * H * 3
    assert ffmpeg.decode_jpeg(synthetic.corrupt_entropy(good), W, H) is None
    assert ffmpeg.decode_jpeg(good[: len(good) // 2], W, H) is None
    assert ffmpeg.decode_jpeg(good, W + 2, H) is None  # unexpected geometry


def _job(seconds: int, frames: int, kind: str = "daytime") -> SegmentJob:
    return SegmentJob(
        "skycam_east", date(2026, 10, 23), kind, 1, T0, T0 + timedelta(seconds=seconds), frames
    )


def _renderer(staging: Path, job: SegmentJob, **kw: object) -> SegmentRenderer:
    recipe = kw.pop("recipe", Recipe())
    return SegmentRenderer(
        job,
        staging=staging,
        expected=_expected(),
        recipe=recipe,  # type: ignore[arg-type]
        index=ChunkIndex(staging, _expected()),
        **kw,  # type: ignore[arg-type]
    )


FAR_FUTURE = Fraction(10**12)


@needs_ffmpeg
def test_linear_mix_through_the_renderer_and_coverage_slate_at_same_pts(tmp_path: Path) -> None:
    # Segment [T0, T0+20) with 2 frames at 360 degrees: shutters [0,10) and [10,20).
    white, black = synthetic.solid(W, H, 255), synthetic.solid(W, H, 0)
    first = [
        synthetic.Frame(
            int(T0.timestamp() + s) * NS, white if s % 2 else black, offset_ns=500_000_000
        )
        for s in range(9)
    ]
    second = [
        synthetic.Frame(int(T0.timestamp() + s) * NS, black, offset_ns=500_000_000)
        for s in range(10, 18)
    ] + [synthetic.Frame(int(T0.timestamp() + 18) * NS, black, offset_ns=499_000_000)]
    synthetic.write_chunks(tmp_path, "skycam_east", first + second, geometry=(W, H))
    renderer = _renderer(tmp_path, _job(20, 2), recipe=Recipe(shutter_degrees=360))
    renderer.advance(FAR_FUTURE)
    frames = renderer.metas[0]["frames"]
    assert [f["coverage"] for f in frames] == [0.9, 0.8999]
    assert [f["slate"] for f in frames] == [False, True]
    # Five black and four white seconds, averaged in linear light: 4/9 white.
    mixed = int(to_srgb(np.array([4 / 9], dtype=np.float32))[0])
    assert frames[0]["sha256"] == hashlib.sha256(bytes([mixed]) * (W * H * 3)).hexdigest()
    assert mixed > 255 * 4 // 9 + 40  # far brighter than a gamma-space mean
    decoded = ffmpeg.framehash(
        ffmpeg.concat_list(renderer.blocks_dir, renderer.block_paths()), concat=True
    )
    assert len(decoded.frames) == 2  # the slate keeps its own PTS; time never compresses


def _corpus(
    staging: Path, seconds: int, *, corrupt: set[int] = frozenset(), skip: range = range(0)
) -> None:
    frames = []
    for s in range(seconds):
        if s in skip:
            continue
        data = synthetic.jpeg(synthetic.sky(W, H, s))
        if s in corrupt:
            data = synthetic.corrupt_entropy(data)
        frames.append(synthetic.Frame(int(T0.timestamp() + s) * NS, data))
    synthetic.write_chunks(staging, "skycam_east", frames, geometry=(W, H))


def _decoded(renderer: SegmentRenderer) -> tuple[str, ...]:
    return ffmpeg.framehash(
        ffmpeg.concat_list(renderer.blocks_dir, renderer.block_paths()), concat=True
    ).hashes


@needs_ffmpeg
def test_block_size_and_kill_resume_give_identical_decoded_frames(tmp_path: Path) -> None:
    job = _job(240, 120)
    reference_root = tmp_path / "reference"
    _corpus(reference_root, 240, skip=range(100, 130))
    reference = _renderer(reference_root, job)
    assert reference.advance(FAR_FUTURE) == 2
    expected = _decoded(reference)
    assert len(expected) == 120

    def clone(name: str) -> Path:
        root = tmp_path / name
        root.mkdir()
        shutil.copytree(reference_root / "sealed", root / "sealed")
        return root

    small = _renderer(clone("small"), job, block=7)
    small.advance(FAR_FUTURE)
    assert _decoded(small) == expected

    killed_root = clone("killed")

    class Killed(Exception):
        pass

    def kill(last: int) -> None:
        if last >= 49:
            raise Killed

    first = _renderer(killed_root, job, block=49, after_block=kill)
    with pytest.raises(Killed):
        first.advance(FAR_FUTURE)
    # A torn block from the dead process is discarded on restart.
    (first.blocks_dir / ".tmp-00049.mkv").write_bytes(b"partial")
    resumed = _renderer(killed_root, job, block=13)
    assert resumed.rendered == 49
    resumed.advance(FAR_FUTURE)
    assert _decoded(resumed) == expected
    assert [m["frames"] for m in resumed.metas] != []


@needs_ffmpeg
def test_corrupt_frames_are_per_slot_rejects_and_never_abort(tmp_path: Path) -> None:
    _corpus(tmp_path, 240, corrupt={3, 7, 200})
    renderer = _renderer(tmp_path, _job(240, 120))
    renderer.advance(FAR_FUTURE)
    assert renderer.complete
    verdicts = {k: v for meta in renderer.metas for k, v in meta["slots"].items()}
    assert sorted(v for v in verdicts.values() if v != "ok") == ["decode", "decode", "decode"]
    prepared = prepare(renderer, weather=None, expected_revision=0)
    assert prepared.result.slots.rejected == {"decode": 3}
    assert prepared.result.slots.accepted == 237
    # 237 of 240 grid slots is 98.75%: partial, and corrupt frames never reach a poster.
    assert (prepared.result.state, prepared.result.reason) == ("partial", "partial_coverage")
    assert all(
        not first <= poster.frame < last
        for poster in prepared.result.posters
        for first, last in prepared.result.exposure.gaps
    )


@needs_ffmpeg
def test_finalize_validates_renditions_posters_sidecar_and_resumes_identically(
    tmp_path: Path,
) -> None:
    _corpus(tmp_path, 240, skip=range(150, 152))
    renderer = _renderer(tmp_path, _job(240, 120))
    renderer.advance(FAR_FUTURE)
    weather = {"interval": {}, "source": "aggregates/v1", "items": []}
    prepared = prepare(renderer, weather=weather, expected_revision=0)
    result = prepared.result
    assert result.state == "published" and result.frames == 120
    assert sorted(r.role for r in result.renditions) == ["mobile", "native", "web"]
    assert {p.extension for p in result.posters} == {"jpg", "webp"}
    for rendition in result.renditions:
        ffmpeg.validate_rendition(
            renderer.directory / "out" / f"{rendition.role}.mp4",
            ffmpeg.Expect(120, rendition.width, rendition.height),
        )
    sidecar = prepared.sidecar
    assert sidecar["time_map"]["terminal_ticks"] == 120_000
    assert sidecar["time_map"]["source_step_seconds"] == {"numerator": 2, "denominator": 1}
    assert len(sidecar["coverage"]["exposure"]["frames"]) == 120
    assert sidecar["weather"] == weather
    assert sidecar["provenance"]["shutter_degrees"] == 180
    # The public pieces pass #18's sidecar rules and name no input frame, address or secret.
    data = ProductFile.sidecar(sidecar).data or b""
    public = data.decode() + json.dumps(result.model_dump(mode="json"))
    for meta in renderer.metas:
        for _, frame_hash in meta["inputs"]:
            assert frame_hash not in public
    for private in ("127.0.0.1", "synthetic", "password", "latitude", "longitude", "/sealed/"):
        assert private not in public
    # The embargo follows the latest plausible capture, never the segment end alone.
    assert result.public_not_before >= result.ends_at + timedelta(seconds=630)
    # Kill after validation: the same result is reused byte for byte.
    first_bytes = (renderer.directory / RESULT).read_bytes()
    assert prepare(renderer, weather=None, expected_revision=5).result == result
    # Kill during finalize: outputs are rebuilt from the same mezzanine, hash-identical.
    (renderer.directory / RESULT).unlink()
    shutil.rmtree(renderer.directory / "out")
    again = prepare(renderer, weather=weather, expected_revision=0)
    assert (renderer.directory / RESULT).read_bytes() == first_bytes
    assert again.result == result


@needs_ffmpeg
def test_low_coverage_segment_is_unavailable_without_media(tmp_path: Path) -> None:
    _corpus(tmp_path, 240, skip=range(0, 130))
    renderer = _renderer(tmp_path, _job(240, 120))
    renderer.advance(FAR_FUTURE)
    prepared = prepare(renderer, weather=None, expected_revision=0)
    assert (prepared.result.state, prepared.result.reason) == (
        "unavailable",
        "insufficient_coverage",
    )
    assert prepared.files == () and not (renderer.directory / "out" / "native.mp4").exists()


def _encode(tmp_path: Path, frames: int, width: int, height: int, *, movie: bool = True) -> Path:
    raw = tmp_path / f"{width}x{height}.rgb"
    raw.write_bytes(
        b"".join(
            np.roll(synthetic.sky(width, height, 3), k, axis=1).tobytes() for k in range(frames)
        )
    )
    out = tmp_path / f"{width}x{height}-{movie}.mp4"
    command = [
        ffmpeg.binary(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-framerate", "60",
        "-i", str(raw), "-vf",
        "format=yuv420p,setsar=1,setparams=range=tv:color_primaries=bt709:color_trc=bt709"
        ":colorspace=bt709",
        "-c:v", "libx264", "-frames:v", str(frames), "-fps_mode", "cfr", "-r", "60", "-an",
        "-video_track_timescale", "60000",
        *(["-movie_timescale", "60000"] if movie else []),
        "-movflags", "+faststart+write_colr", "-f", "mp4", str(out),
    ]  # fmt: skip
    subprocess.run(command, check=True, timeout=120)
    return out


@needs_ffmpeg
def test_validator_passes_n120_and_native_geometry_and_fails_default_movie_timescale(
    tmp_path: Path,
) -> None:
    good = ffmpeg.validate_rendition(_encode(tmp_path, 120, 64, 30), ffmpeg.Expect(120, 64, 30))
    assert len(good.frames) == 120
    native = _encode(tmp_path, 3, 4096, 1856)
    assert len(ffmpeg.validate_rendition(native, ffmpeg.Expect(3, 4096, 1856)).frames) == 3
    default = _encode(tmp_path, 120, 64, 30, movie=False)
    with pytest.raises(ffmpeg.ValidationError) as failure:
        ffmpeg.validate_rendition(default, ffmpeg.Expect(120, 64, 30))
    assert failure.value.reason == "movie_timescale"
    for expect, reason in (
        (ffmpeg.Expect(119, 64, 30), "sample_timing"),
        (ffmpeg.Expect(120, 66, 30), "geometry"),
    ):
        with pytest.raises(ffmpeg.ValidationError) as failure:
            ffmpeg.validate_rendition(tmp_path / "64x30-True.mp4", expect)
        assert failure.value.reason == reason
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes((tmp_path / "64x30-True.mp4").read_bytes()[:500])
    with pytest.raises(ffmpeg.ValidationError):
        ffmpeg.validate_rendition(truncated, ffmpeg.Expect(120, 64, 30))


def test_schedule_job_without_the_site_secret_fails_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from weather.db.jobs import Job
    from weather.pipeline import metrics, schedule
    from weather.scheduler.registry import JobContext
    from weather.timemodel import ScheduleError

    monkeypatch.setenv("WEATHER_SITE_FILE", str(tmp_path / "absent" / "site.json"))
    job = Job(uuid4(), schedule.SCHEDULE_KIND, {"start": "2026-10-24"}, T0, 1)
    before = metrics.SCHEDULE_FAILURES.labels("site")._value.get()
    with pytest.raises(ScheduleError):
        asyncio.run(schedule.schedule_job(JobContext(None, job)))  # type: ignore[arg-type]
    assert metrics.SITE_CONFIGURED._value.get() == 0
    assert metrics.SCHEDULE_FAILURES.labels("site")._value.get() == before + 1
    assert schedule.hour_key(T0 + timedelta(minutes=59)) == "2026-10-23T15Z"


def test_pipeline_deployments_ship_disabled_with_only_their_inputs() -> None:
    import yaml

    root = Path(__file__).resolve().parents[2] / "deploy/k8s"
    assert "pipeline" not in yaml.safe_load((root / "kustomization.yaml").read_text())["resources"]
    deployments = list(yaml.safe_load_all((root / "pipeline/deployments.yaml").read_text()))
    policy = yaml.safe_load((root / "pipeline/network-policy.yaml").read_text())
    assert {d["metadata"]["name"] for d in deployments} == {
        "pipeline-skycam-east",
        "pipeline-skycam-west",
        "pipeline-frontyard-1",
    }
    for deployment in deployments:
        spec = deployment["spec"]["template"]["spec"]
        assert deployment["spec"]["replicas"] == 0
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert policy["spec"]["podSelector"]["matchLabels"].items() <= labels.items()
        assert spec["automountServiceAccountToken"] is False
        container = spec["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["command"][:3] == ["weather", "pipeline", "run"]
        secrets = {
            source["secret"]["name"]
            for volume in spec["volumes"]
            for source in volume.get("projected", {}).get("sources", [])
        }
        assert secrets == {"weather-pg-worker", "weather-pg-ca", "weather-media-s3-writer"}
        claims = {
            v["persistentVolumeClaim"]["claimName"]
            for v in spec["volumes"]
            if "persistentVolumeClaim" in v
        }
        assert claims == {"weather-staging"}
    egress_ports = {p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]}
    assert egress_ports == {5432, 53, 443, 8443}
    bench = yaml.safe_load((root / "pipeline/bench-job.yaml").read_text())
    assert bench["metadata"]["labels"]["estate.vallery.net/disposable"] == "true"
    assert "volumes" in bench["spec"]["template"]["spec"]
    assert not any(
        "secret" in v or "projected" in v for v in bench["spec"]["template"]["spec"]["volumes"]
    )
