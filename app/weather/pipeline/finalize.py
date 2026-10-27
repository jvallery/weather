# SPDX-License-Identifier: MIT
"""Segment boundary: concatenate, encode three renditions, validate, posters, sidecar, publish.

`prepare` is deterministic and resumable: it writes `result.json` (RenderResult, sidecar and
file list) with fsync + rename only after every output validated, and a later run reuses it.
`publish` hands the prepared product to #18 with the expected revision recorded at prepare
time, so a process killed between validate and publish, or between publish and the segment
update, commits exactly one publication: the retry is recognized as the same content.
"""

import hashlib
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from weather.publish.model import Product, ProductFile
from weather.publish.publisher import Publisher
from weather.timemodel import Segment

from . import gapfill, metrics
from .ffmpeg import (
    Expect,
    MezzanineReader,
    RenditionSpec,
    ValidationError,
    concat_list,
    encode_renditions,
    extract_frame,
    validate_rendition,
)
from .intake import utc
from .recipe import Recipe
from .render import SegmentRenderer, canonical, durable_write, file_sha256, fsync_dir, micros
from .result import (
    EMBARGO,
    Build,
    ExposureCoverage,
    FilledRun,
    Poster,
    RenderResult,
    Rendition,
    SlotCoverage,
)

LOG = logging.getLogger("weather.pipeline")
RESULT = "result.json"
# (width, JPEG role, WebP role); `poster` is the role the public catalog view exposes.
POSTERS = (
    (1920, "poster", "poster_webp"),
    (960, "poster_960", "poster_webp960"),
    (480, "poster_480", "poster_webp480"),
)
CRF = {"native": 20, "web": 21, "mobile": 23}
WIDTHS = {"native": None, "web": 1920, "mobile": 1280}
PUBLISH_DEADLINE = timedelta(hours=1)


@dataclass(frozen=True)
class Prepared:
    result: RenderResult
    sidecar: dict[str, Any]
    files: tuple[dict[str, Any], ...]

    def document(self) -> dict[str, Any]:
        return {
            "result": self.result.model_dump(mode="json"),
            "sidecar": self.sidecar,
            "files": list(self.files),
        }

    @classmethod
    def load(cls, path: Path) -> Prepared:
        value = json.loads(path.read_text())
        return cls(
            RenderResult.model_validate(value["result"]),
            value["sidecar"],
            tuple(value["files"]),
        )


def even(value: int) -> int:
    return max(2, value - value % 2)


def rendition_specs(width: int, height: int) -> list[RenditionSpec]:
    """Native, web and mobile: even dimensions, aspect kept, never upscaled."""
    specs = []
    for role in ("native", "web", "mobile"):
        target = WIDTHS[role]
        w = even(width if target is None else min(target, width))
        h = even(round(Fraction(height * w, width))) if target is not None else even(height)
        specs.append(RenditionSpec(role, w, h, CRF[role]))
    return specs


def slot_state(fraction: Fraction, recipe: Recipe) -> tuple[str, str | None]:
    if fraction >= recipe.slot_normal:
        return "published", None
    if fraction >= recipe.slot_partial:
        return "partial", "partial_coverage"
    if fraction >= recipe.slot_available:
        return "partial", "low_coverage"
    return "unavailable", "insufficient_coverage"


def gap_ranges(slates: list[bool]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for index, slate in enumerate(slates):
        if not slate:
            continue
        if ranges and ranges[-1][1] == index:
            ranges[-1] = (ranges[-1][0], index + 1)
        else:
            ranges.append((index, index + 1))
    return ranges


def poster_frame(slates: list[bool]) -> int | None:
    """The observed frame nearest the middle (earlier on a tie)."""
    middle = len(slates) // 2
    candidates = [k for k, slate in enumerate(slates) if not slate]
    return min(candidates, key=lambda k: (abs(k - middle), k)) if candidates else None


def _write_image(image: Image.Image, path: Path, extension: str) -> None:
    buffer = io.BytesIO()
    if extension == "jpg":
        image.save(buffer, format="JPEG", quality=88, optimize=False, progressive=False)
    else:
        image.save(buffer, format="WEBP", quality=82, method=4)
    durable_write(path, buffer.getvalue())


def prepare(
    renderer: SegmentRenderer,
    *,
    weather: dict[str, Any] | None,
    expected_revision: int,
) -> Prepared:
    """Everything up to the catalog: deterministic, CPU-bound, resumable."""
    path = renderer.directory / RESULT
    if path.exists():
        return Prepared.load(path)
    if not renderer.complete:
        raise RuntimeError("Segment mezzanine is incomplete")
    job, recipe, expected = renderer.job, renderer.recipe, renderer.expected
    geometry = renderer.geometry
    metas = renderer.metas
    # Slot coverage: canonical grid slots of [a, b), never collector loss records.
    grid = {micros(slot) for slot in expected.grid(geometry.start, geometry.end)}
    verdicts: dict[str, str] = {}
    for meta in metas:
        verdicts.update(meta["slots"])
    accepted = sum(1 for slot in grid if verdicts.get(slot) == "ok")
    rejected: dict[str, int] = {}
    for slot in grid:
        reason = verdicts.get(slot)
        if reason is not None and reason != "ok":
            rejected[reason] = rejected.get(reason, 0) + 1
    fraction = Fraction(accepted, len(grid)) if grid else Fraction(0)
    state, reason = slot_state(fraction, recipe)
    frames = [frame for meta in metas for frame in meta["frames"]]
    slates = [bool(frame["slate"]) for frame in frames]
    gaps = gap_ranges(slates)
    rendered = slates.count(False)
    if state != "unavailable" and rendered == 0:
        state, reason = "unavailable", "no_exposure"
    uppers = [int(meta["max_upper"]) for meta in metas if meta["max_upper"] is not None]
    pnb = max(job.ends_at, *(utc(Fraction(u, 1_000_000)) for u in uppers)) + EMBARGO
    inputs = sorted({tuple(item) for meta in metas for item in meta["inputs"]})
    # Mezzanine build provenance, merged over contiguous blocks from the same build.
    builds: list[dict[str, Any]] = []
    for meta in metas:
        build = {"encoder": meta.get("encoder", "unknown"), "image": meta.get("image", "unpinned")}
        if builds and {k: builds[-1][k] for k in build} == build:
            builds[-1]["last"] = meta["last"]
        else:
            builds.append({**build, "first": meta["first"], "last": meta["last"]})
    manifest = hashlib.sha256(canonical(inputs)).hexdigest()
    slots = SlotCoverage(
        expected=len(grid),
        accepted=accepted,
        fraction=float(fraction),
        rejected=dict(sorted(rejected.items())),
        missing=len(grid) - accepted - sum(rejected.values()),
        duplicates=sum(int(meta["duplicates"]) for meta in metas),
        reported_losses=sum(int(meta["reported_losses"]) for meta in metas),
    )
    renditions: list[Rendition] = []
    posters: list[Poster] = []
    fill_runs: list[gapfill.Run] = []
    files: list[dict[str, Any]] = []
    out = renderer.directory / "out"
    out.mkdir(exist_ok=True)
    if state != "unavailable":
        blocks = renderer.block_paths()
        for meta, block in zip(metas, blocks, strict=True):
            if file_sha256(block) != meta["mkv_sha256"]:
                raise RuntimeError("Mezzanine block changed on the staging share")
        concat = concat_list(renderer.blocks_dir, blocks)
        specs = rendition_specs(expected.width, expected.height)
        temporary = [(spec, out / f".tmp-{spec.role}.mp4") for spec in specs]
        if gaps:
            # #144: renditions never show a slate; the mezzanine and `gaps` keep them.
            fill_runs = gapfill.plan(slates)
            stats = gapfill.Stats()
            step = job.frame_map.source_step

            def span(first: int, last: int) -> tuple[datetime, datetime]:
                return (
                    job.starts_at + timedelta(seconds=float(first * step)),
                    job.starts_at + timedelta(seconds=float(last * step)),
                )

            reader = MezzanineReader(concat, expected.width, expected.height)
            try:
                encode_renditions(
                    concat,
                    job.frames,
                    temporary,
                    raw=(expected.width, expected.height),
                    feed=gapfill.fill(
                        reader, slates, (expected.height, expected.width), span, stats
                    ),
                )
            finally:
                reader.close()
            for mode, count in (("interpolated", stats.interpolated), ("held", stats.held)):
                metrics.FRAMES.labels(job.camera, mode).inc(count)
        else:
            encode_renditions(concat, job.frames, temporary)
        timing = None
        for spec, tmp in temporary:
            decoded = validate_rendition(tmp, Expect(job.frames, spec.width, spec.height))
            # Every rendition maps frame k to the same PTS, hence the same source time.
            this = tuple(
                (pts * decoded.timebase, d * decoded.timebase) for pts, d, _ in decoded.frames
            )
            if timing is not None and this != timing:
                raise ValidationError("rendition_timing")
            timing = this
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            final = out / f"{spec.role}.mp4"
            os.replace(tmp, final)
            renditions.append(
                Rendition(
                    role=spec.role,  # type: ignore[arg-type]
                    width=spec.width,
                    height=spec.height,
                    frames=job.frames,
                    sha256=file_sha256(final),
                    bytes=final.stat().st_size,
                )
            )
            files.append(
                {
                    "role": spec.role,
                    "extension": "mp4",
                    "path": f"out/{spec.role}.mp4",
                    "width": spec.width,
                    "height": spec.height,
                }
            )
        fsync_dir(out)
        index = poster_frame(slates)
        assert index is not None
        native = specs[0]
        rgb = np.frombuffer(
            extract_frame(out / "native.mp4", index, native.width, native.height), dtype=np.uint8
        ).reshape(native.height, native.width, 3)
        image = Image.fromarray(rgb, "RGB")
        for target, jpeg_role, webp_role in POSTERS:
            width = min(target, native.width)
            height = max(1, round(native.height * width / native.width))
            scaled = (
                image
                if width == native.width
                else image.resize((width, height), Image.Resampling.LANCZOS)
            )
            for extension, role in (("jpg", jpeg_role), ("webp", webp_role)):
                name = f"{role}.{extension}"
                _write_image(scaled, out / name, extension)
                posters.append(
                    Poster(
                        role=role,
                        extension=extension,  # type: ignore[arg-type]
                        width=width,
                        height=height,
                        frame=index,
                        sha256=file_sha256(out / name),
                        bytes=(out / name).stat().st_size,
                    )
                )
                files.append(
                    {
                        "role": role,
                        "extension": extension,
                        "path": f"out/{name}",
                        "width": width,
                        "height": height,
                    }
                )
    segment = Segment(job.kind, job.cycle_date, job.starts_at, job.ends_at, job.frames)  # type: ignore[arg-type]
    step = geometry.step
    sidecar = {
        "schema_version": 1,
        "camera": job.camera,
        "cycle_date": job.cycle_date.isoformat(),
        "segment": job.kind,
        "label": segment.label,
        "state": state,
        "reason": reason,
        "source_interval": {"start": job.starts_at.isoformat(), "end": job.ends_at.isoformat()},
        "public_not_before": pnb.isoformat(),
        "time_map": {
            "frames": job.frames,
            "fps": 60,
            "timescale": 60000,
            "sample_ticks": 1000,
            "terminal_ticks": job.frames * 1000,
            "source_step_seconds": {"numerator": step.numerator, "denominator": step.denominator},
            "source_time": "start + (k + 1/2) * source_step_seconds",
            "pts_seconds": "k / 60",
        },
        "coverage": {
            "slots": slots.model_dump(mode="json"),
            "exposure": {
                "threshold": str(recipe.min_exposure_coverage),
                "rendered": rendered,
                "gaps": [list(item) for item in gaps],
                "fill": gapfill.FILL if fill_runs else None,
                "filled": [
                    {"first": run.first, "last": run.last, "mode": run.mode} for run in fill_runs
                ],
                "frames": [round(float(frame["coverage"]), 4) for frame in frames],
            },
        },
        "renditions": [
            {"role": r.role, "width": r.width, "height": r.height, "frames": r.frames}
            for r in renditions
        ],
        "provenance": {
            **recipe.record(),
            "input_manifest_sha256": manifest,
            "inputs": len(inputs),
            "mezzanine_builds": builds,
        },
        "weather": weather,
        # The video publishes first; its keogram's final status follows as a sidecar revision.
        "keogram": {"state": "pending", "reason": None},
    }
    sidecar_bytes = ProductFile.sidecar(sidecar).data
    assert sidecar_bytes is not None
    result = RenderResult(
        camera=job.camera,
        cycle_date=job.cycle_date,
        segment=job.kind,  # type: ignore[arg-type]
        revision=job.revision,
        frames=job.frames,
        starts_at=job.starts_at,
        ends_at=job.ends_at,
        public_not_before=pnb,
        state=state,  # type: ignore[arg-type]
        reason=reason,
        recipe=recipe.record(),
        input_manifest_sha256=manifest,
        inputs=len(inputs),
        mezzanine_builds=[Build.model_validate(item) for item in builds],
        slots=slots,
        exposure=ExposureCoverage(
            rendered=rendered,
            gaps=gaps,
            threshold=str(recipe.min_exposure_coverage),
            fill=gapfill.FILL if fill_runs else None,
            filled=[
                FilledRun(first=run.first, last=run.last, mode=run.mode)  # type: ignore[arg-type]
                for run in fill_runs
            ],
        ),
        renditions=renditions,
        posters=posters,
        sidecar_sha256=hashlib.sha256(sidecar_bytes).hexdigest(),
        expected_revision=expected_revision,
    )
    prepared = Prepared(result, sidecar, tuple(files))
    durable_write(path, canonical(prepared.document()))
    return prepared


def rebase(renderer: SegmentRenderer, prepared: Prepared, expected_revision: int) -> Prepared:
    """The same outputs against a newer expected revision, written durably first."""
    result = prepared.result.model_copy(update={"expected_revision": expected_revision})
    rebased = Prepared(
        RenderResult.model_validate(result.model_dump()), prepared.sidecar, prepared.files
    )
    durable_write(renderer.directory / RESULT, canonical(rebased.document()))
    return rebased


def product(prepared: Prepared, directory: Path) -> Product:
    result = prepared.result
    files = tuple(
        ProductFile(
            role=item["role"],
            extension=item["extension"],
            path=directory / item["path"],
            width=item["width"],
            height=item["height"],
        )
        for item in prepared.files
    )
    return Product.video(
        camera_id=result.camera,
        cycle_date=result.cycle_date,
        segment=result.segment,
        starts_at=result.starts_at,
        ends_at=result.ends_at,
        frames=result.frames,
        public_not_before=result.public_not_before,
        state=result.state,
        reason=result.reason,
        files=(*files, ProductFile.sidecar(prepared.sidecar)),
    )


async def publish(publisher: Publisher, prepared: Prepared, directory: Path) -> Any:
    item = product(prepared, directory)
    if hashlib.sha256(item.files[-1].data or b"").hexdigest() != prepared.result.sidecar_sha256:
        raise RuntimeError("Prepared sidecar changed")
    return await publisher.publish(
        item,
        expected_revision=prepared.result.expected_revision,
        deadline=datetime.now(UTC) + PUBLISH_DEADLINE,
    )
