# SPDX-License-Identifier: MIT
"""`weather pipeline bench`: synthetic throughput, for the in-cluster Job only.

Per camera, in parallel processes: (1) streaming: seal `--seconds` of 1 Hz synthetic sky
frames and time the renderer over them (target >= 3x real time); (2) finalize: build a
`--finalize-frames` intra mezzanine and time concat + three encodes + validation + posters,
extrapolated to 4,200 frames (target <= 2 h with three cameras at once). Prints one JSON line
per camera and exits 1 when a target is missed. Never run it on a workstation: at native
geometry it is minutes of saturated CPU per camera.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg, synthetic
from .finalize import prepare
from .intake import ChunkIndex, Expected
from .recipe import CAMERAS, Recipe
from .render import SegmentJob, SegmentRenderer, canonical, durable_write, file_sha256, micros

TARGET_FACTOR = 3
TARGET_FINALIZE = 7200
REFERENCE_FRAMES = 4200
START = datetime(2026, 6, 21, 12, tzinfo=UTC)


def _stream(
    work: Path, camera: str, seconds: int, width: int, height: int, recipe: Recipe
) -> float:
    pool = [synthetic.jpeg(synthetic.sky(width, height, seed)) for seed in range(12)]
    frames = [
        synthetic.Frame(int(START.timestamp() + i) * synthetic.NS, pool[i % len(pool)])
        for i in range(seconds)
    ]
    synthetic.write_chunks(work, camera, frames, geometry=(width, height))
    # A typical daytime step: one output frame per 12 s of source.
    job = SegmentJob(
        camera,
        date(2026, 6, 21),
        "daytime",
        1,
        START,
        START + timedelta(seconds=seconds),
        max(1, seconds // 12),
    )
    expected = Expected(camera, 1000, 0, width, height)
    renderer = SegmentRenderer(
        job, staging=work, expected=expected, recipe=recipe, index=ChunkIndex(work, expected)
    )
    started = time.monotonic()
    renderer.advance(Fraction(10**12))
    return seconds / (time.monotonic() - started)


def _finalize(
    work: Path, camera: str, frames: int, width: int, height: int, recipe: Recipe
) -> tuple[float, str]:
    job = SegmentJob(
        camera,
        date(2026, 6, 22),
        "daytime",
        1,
        START + timedelta(days=1),
        START + timedelta(days=1, seconds=frames * 12),
        frames,
    )
    expected = Expected(camera, 1000, 0, width, height)
    renderer = SegmentRenderer(
        job, staging=work, expected=expected, recipe=recipe, index=ChunkIndex(work, expected)
    )
    pool = [synthetic.sky(width, height, seed) for seed in range(8)]
    grid = expected.grid(renderer.geometry.start, renderer.geometry.end)
    for first in range(0, frames, 60):
        last = min(first + 60, frames)
        owned = renderer.geometry.owned(first, last)
        path = renderer.blocks_dir / f"{first:05d}.mkv"
        encoder = ffmpeg.BlockEncoder(path, width, height)
        metas = []
        for k in range(first, last):
            pixels = np.roll(pool[k % len(pool)], k * 7, axis=1).tobytes()
            encoder.write(pixels)
            metas.append({"coverage": 1.0, "slate": False, "sha256": "0" * 64})
        encoder.close()
        meta = {
            "schema_version": 1,
            "recipe": recipe.id,
            "first": first,
            "last": last,
            "mkv_sha256": file_sha256(path),
            "frames": metas,
            "slots": {micros(s): "ok" for s in grid if owned[0] <= s < owned[1]},
            "inputs": [],
            "max_upper": micros(owned[1]),
            "duplicates": 0,
            "reported_losses": 0,
        }
        durable_write(renderer.blocks_dir / f"{first:05d}.json", canonical(meta))
    renderer.metas = renderer._load()
    started = time.monotonic()
    prepared = prepare(renderer, weather=None, expected_revision=0)
    return time.monotonic() - started, prepared.result.state


def run_camera(args: tuple[str, str, int, int, int, int, int]) -> dict[str, Any]:
    root, camera, seconds, finalize_frames, width, height, shutter = args
    recipe = replace(Recipe(shutter_degrees=shutter), encoder=ffmpeg.version())
    work = Path(root) / camera
    work.mkdir(parents=True, exist_ok=True)
    factor = _stream(work / "stream", camera, seconds, width, height, recipe)
    elapsed, state = _finalize(work / "finalize", camera, finalize_frames, width, height, recipe)
    return {
        "camera": camera,
        "stream_realtime_factor": round(factor, 2),
        "finalize_frames": finalize_frames,
        "finalize_seconds": round(elapsed, 1),
        "finalize_seconds_at_4200": round(elapsed * REFERENCE_FRAMES / finalize_frames, 1),
        "state": state,
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="weather pipeline bench")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--finalize-frames", type=int, default=1200)
    parser.add_argument("--cameras", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--width", type=int, default=4096)
    parser.add_argument("--height", type=int, default=1856)
    parser.add_argument("--shutter", type=int, default=180)
    args = parser.parse_args(argv)
    if not 12 <= args.seconds <= 7200 or not 1 <= args.finalize_frames <= REFERENCE_FRAMES:
        parser.exit(2, "Out-of-range benchmark size\n")
    jobs = [
        (
            str(args.work),
            camera,
            args.seconds,
            args.finalize_frames,
            args.width,
            args.height,
            args.shutter,
        )
        for camera in CAMERAS[: args.cameras]
    ]
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(run_camera, jobs))
    failed = False
    for result in results:
        result["pass"] = (
            result["stream_realtime_factor"] >= TARGET_FACTOR
            and result["finalize_seconds_at_4200"] <= TARGET_FINALIZE
            and result["state"] == "published"
        )
        failed |= not result["pass"]
        print(json.dumps(result, sort_keys=True))
    sys.exit(1 if failed else 0)
