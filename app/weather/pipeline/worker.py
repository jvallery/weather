# SPDX-License-Identifier: MIT
"""`weather pipeline run --camera <id>`: one camera's streaming render and publish loop.

Every ten seconds: load the camera's started, unfinished segments; render every block whose
inputs have had time to seal; once a segment's mezzanine is complete, prepare (encode,
validate, posters, sidecar) and publish through #18, then record the result on the segment.
Then the hourly pass (#38) selects and archives each closed hour's original. Each segment and
each hour fails alone and retries on a later loop. CPU work runs in a worker thread so the
metrics endpoint stays responsive.
"""

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.api.source import SourceUnavailable
from weather.db.connection import create_database_engine, wait_for_database
from weather.preview.producer import PreviewProducer
from weather.publish import catalog
from weather.publish.model import MEDIA_BUCKET, ORIGINALS_BUCKET
from weather.publish.objects import ObjectStore
from weather.publish.publisher import Publisher
from weather.scheduler.cleanup import Cleanup, CleanupSettings, Housekeeping, SqlCatalog
from weather.scheduler.pressure import Pressure
from weather.timemodel._instant import epoch_seconds

from . import ffmpeg, metrics, schedule
from .finalize import RESULT, Prepared, prepare, publish, rebase
from .hourly import HourlyArchive
from .intake import ChunkIndex, Expected, utc
from .keogram import Region, load_regions
from .keogram_publish import Keograms
from .recipe import (
    CAMERAS,
    DEFAULT_CADENCE_MS,
    DEFAULT_PHASE_MS,
    MAX_BLOCK,
    Recipe,
    from_environment,
)
from .render import ChunkStage, SegmentRenderer
from .summary import AggregateWeather, WeatherSummaryProvider

LOG = logging.getLogger("weather.pipeline")
LOOP_SECONDS = 10
# Rendering per segment per loop stops after this long; the next loop starts at once.
RENDER_BUDGET_SECONDS = 30
MAX_BACKOFF = 1800
WEATHER_PATIENCE = timedelta(hours=1)


@dataclass(frozen=True)
class PipelineConfig:
    camera: str
    staging: Path
    expected: Expected
    recipe: Recipe
    block: int = MAX_BLOCK
    chunk_seconds: int = 60
    metrics_port: int = 9000
    skew_seconds: int = 30
    # The camera's keogram region (#37); None renders no keogram.
    region: Region | None = None
    # The region file was refused: no keogram is extracted, and each one fails `region_config`.
    region_error: bool = False

    @classmethod
    def from_environment(cls, camera: str, environ: dict[str, str] | None = None) -> PipelineConfig:
        env = dict(os.environ if environ is None else environ)
        if camera not in CAMERAS:
            raise ValueError("Unknown camera")
        try:
            width, height = (
                int(v) for v in env.get("WEATHER_PIPELINE_GEOMETRY", "4096x1856").split("x")
            )
            expected = Expected(
                camera=camera,
                # The commissioned capture grid (#4: 0.5 Hz); must equal capture.json.
                cadence_ms=int(env.get("WEATHER_PIPELINE_CADENCE_MS", str(DEFAULT_CADENCE_MS))),
                phase_ms=int(env.get("WEATHER_PIPELINE_PHASE_MS", str(DEFAULT_PHASE_MS[camera]))),
                width=width,
                height=height,
            )
            config = cls(
                camera=camera,
                staging=Path(env.get("WEATHER_STAGING", "/staging")),
                expected=expected,
                recipe=from_environment(env),
                block=int(env.get("WEATHER_PIPELINE_BLOCK_FRAMES", str(MAX_BLOCK))),
                chunk_seconds=int(env.get("WEATHER_CAPTURE_CHUNK_SECONDS", "60")),
                metrics_port=int(env.get("METRICS_PORT", "9000")),
                skew_seconds=int(env.get("WEATHER_PREVIEW_SKEW_SECONDS", "30")),
            )
        except ValueError, OSError:
            raise ValueError("Invalid pipeline configuration") from None
        if (
            not config.staging.is_absolute()
            or not 1 <= config.block <= MAX_BLOCK
            or not 1 <= config.chunk_seconds <= 300
            or not 30 <= config.skew_seconds <= 600
            or not 1000 <= expected.cadence_ms <= 3_600_000
            or not 0 <= expected.phase_ms < expected.cadence_ms
            or not (0 < width <= 16384 and 0 < height <= 16384)
        ):
            raise ValueError("Invalid pipeline configuration")
        try:
            region = load_regions(
                Path(env["WEATHER_KEOGRAM_REGIONS"]) if "WEATHER_KEOGRAM_REGIONS" in env else None
            ).region(camera, width, height)
        except (ValueError, OSError) as error:
            # A bad region never stops the camera's videos, stills or previews (#37).
            metrics.KEOGRAM_REGION_ERRORS.labels(camera).inc()
            LOG.error("Keogram region configuration refused (%s, %s)", camera, type(error).__name__)
            return replace(config, region_error=True)
        return replace(config, region=region)


class ReleaseAhead(RuntimeError):
    """The segment's release point is ahead of its durable blocks: an incident."""


class Worker:
    def __init__(
        self,
        config: PipelineConfig,
        engine: AsyncEngine,
        publisher: Publisher,
        weather: WeatherSummaryProvider,
        *,
        stages: Sequence[ChunkStage] = (),
        archive_hours: bool = False,
        housekeeping: Housekeeping | None = None,
    ) -> None:
        self.config, self.engine, self.publisher, self.weather = config, engine, publisher, weather
        self.housekeeping = housekeeping
        self.catching_up = False
        self.stages = tuple(stages)
        # Created on the event loop: the keogram stage writes bins through it.
        self.keograms: Keograms | None = None
        self.index = ChunkIndex(config.staging, config.expected)
        self.hourly = (
            HourlyArchive(
                config.camera,
                engine,
                publisher,
                self.index,
                config.expected,
                config.chunk_seconds,
            )
            if archive_hours
            else None
        )
        self.renderers: dict[UUID, SegmentRenderer] = {}
        self.durable: set[UUID] = set()
        # Per-segment retry backoff: (consecutive failures, wall time of the next attempt).
        self.backoff: dict[UUID, tuple[int, datetime]] = {}

    def render_allowed(self) -> bool:
        return self.housekeeping is None or self.housekeeping.render_allowed()

    def renderer(self, row: schedule.SegmentRow) -> SegmentRenderer:
        found = self.renderers.get(row.id)
        if found is None:
            # A segment with a release point finishes under the recipe its blocks used (#144):
            # a recipe change applies to segments that have not started.
            recipe = self.config.recipe
            stored = row.mezzanine_recipe
            if stored is not None and stored != recipe.id:
                with contextlib.suppress(ValueError):
                    # An id that does not parse keeps the configured recipe: _guard refuses it.
                    recipe = Recipe.from_id(stored, recipe)
            created = SegmentRenderer(
                row.job,
                staging=self.config.staging,
                expected=self.config.expected,
                recipe=recipe,
                index=self.index,
                block=self.config.block,
                chunk_seconds=self.config.chunk_seconds,
                stages=self.stages
                + ((self.keograms.stage,) if self.keograms and self.keograms.stage else ()),
            )
            self._guard(row, created)
            self.renderers[row.id] = found = created
        return found

    def _guard(self, row: schedule.SegmentRow, renderer: SegmentRenderer) -> None:
        """Refuse to resume below the durable release point (#19): cleanup may already have
        deleted that input, so re-rendering it would replace frames with gaps. The renderer
        is not cached, so the next loop re-checks (e.g. once the share lists again)."""
        if row.mezzanine_through is None:
            return
        released = renderer.released_through()
        if row.mezzanine_recipe not in (None, renderer.recipe.id) or (
            released is None or utc(released) < row.mezzanine_through
        ):
            metrics.RELEASE_INCIDENTS.labels(self.config.camera).inc()
            LOG.error(
                "Durable blocks are behind the release point; not rendering (%s)",
                self.config.camera,
            )
            raise ReleaseAhead("On-disk mezzanine is behind segment.mezzanine_through")

    async def step(self, now: datetime) -> None:
        camera = self.config.camera
        keograms = self.config.region is not None or self.config.region_error
        if keograms and self.keograms is None:
            self.keograms = Keograms(
                self.engine, self.publisher, self.config.region, asyncio.get_running_loop()
            )
        rows = await schedule.active(self.engine, camera, now)
        live = {row.id for row in rows}
        for stale in set(self.renderers) - live:
            del self.renderers[stale]
        self.durable &= live
        self.backoff = {k: v for k, v in self.backoff.items() if k in live}
        current = epoch_seconds(now)
        for row in rows:
            if row.id in self.backoff and now < self.backoff[row.id][1]:
                continue
            stage = "render"
            try:
                renderer = self.renderer(row)
                if not renderer.complete and not self.render_allowed():
                    # Render stop (>= 97 %, #19): add no mezzanine blocks to the share.
                    # Complete mezzanines still finalize and publish, which frees them.
                    metrics.PRESSURE_PAUSED.labels(camera).inc()
                    continue
                rendered_through = (
                    renderer.geometry.owned(0, renderer.rendered)[1] if renderer.rendered else None
                )
                for chunk in self.index.refresh(
                    renderer.geometry.start, min(renderer.geometry.end, current)
                ):
                    if rendered_through is not None and not chunk.readded:
                        late = sum(
                            1
                            for c in chunk.candidates
                            if renderer.geometry.start <= c.time < rendered_through
                        )
                        if late:
                            metrics.LATE.labels(camera).inc(late)
                # One block at a time within a time budget, so housekeeping and the pressure
                # checks keep running while a backlog catches up (#19); the loop then
                # returns at once instead of sleeping.
                budget = time.monotonic() + RENDER_BUDGET_SECONDS
                while await asyncio.to_thread(renderer.advance, current, limit=1):
                    released = renderer.released_through()
                    if released is not None:
                        await schedule.mark_through(
                            self.engine, row.id, utc(released), renderer.recipe.id
                        )
                    if renderer.complete or not self.render_allowed():
                        break
                    if time.monotonic() >= budget:
                        self.catching_up = True
                        break
                if not renderer.complete:
                    continue
                if row.id not in self.durable:
                    await schedule.mark_durable(self.engine, row.id)
                    self.durable.add(row.id)
                stage = "prepare"
                await schedule.mark_processing(self.engine, row.id)
                weather = None
                revision = 0
                if not (renderer.directory / RESULT).exists():
                    revision = (await schedule.current_video(self.engine, row.job)).revision
                    stage = "weather"
                    try:
                        weather = await self.weather.summary(row.job.starts_at, row.job.ends_at)
                    except SourceUnavailable:
                        # Retry a transient failure; never freeze it into result.json while
                        # there is time. Past the patience window the sidecar records none.
                        if now < row.job.ends_at + WEATHER_PATIENCE:
                            raise
                    stage = "prepare"
                prepared = await asyncio.to_thread(
                    prepare, renderer, weather=weather, expected_revision=revision
                )
                stage = "publish"
                outcome = await self._publish(row, renderer, prepared)
                stage = "catalog"
                result = prepared.result.model_dump(mode="json")
                await schedule.finish(self.engine, row.id, result, outcome=outcome)
                metrics.SEGMENTS.labels(camera, outcome[0] if outcome else result["state"]).inc()
                metrics.COVERAGE.labels(camera).set(prepared.result.slots.fraction)
                metrics.LATENCY.labels(camera).observe((now - row.job.ends_at).total_seconds())
                del self.renderers[row.id]
                if self.keograms is not None and self.keograms.stage is not None:
                    self.keograms.stage.forget(row.job)
                self.durable.discard(row.id)
                self.backoff.pop(row.id, None)
            except Exception as error:
                metrics.FAILURES.labels(camera, stage).inc()
                # Never log paths, payloads or driver errors; the type and stage suffice.
                LOG.error("Pipeline %s failed (%s, %s)", stage, camera, type(error).__name__)
                # Back off so a deterministic failure never re-reads gigabytes every loop or
                # starves the camera's live segment: 10 s doubling to 30 min.
                failures = self.backoff.get(row.id, (0, now))[0] + 1
                delay = min(MAX_BACKOFF, LOOP_SECONDS * 2 ** min(failures - 1, 10))
                self.backoff[row.id] = (failures, now + timedelta(seconds=delay))
        if self.hourly is not None:
            try:
                await self.hourly.step(now)
            except Exception as error:
                metrics.STILL_FAILURES.labels(camera, "catalog").inc()
                LOG.error("Hourly pass failed (%s, %s)", camera, type(error).__name__)
        if self.keograms is not None:
            # After the videos: a keogram never delays or withdraws its video.
            try:
                await self.keograms.sweep(camera, now)
            except Exception as error:
                metrics.FAILURES.labels(camera, "keogram").inc()
                LOG.error("Pipeline keogram failed (%s, %s)", camera, type(error).__name__)
        # A segment in backoff (possibly stuck for days) must not hold every chunk since its
        # start in memory: only segments that render now set the prune floor.
        rendering = [r for r in rows if not (r.id in self.backoff and now < self.backoff[r.id][1])]
        if rendering:
            self.index.prune(min(epoch_seconds(r.job.starts_at) for r in rendering) - 3600)
        elif rows:
            # Every segment is backing off: keep only the last hour in memory.
            self.index.prune(current - 3600)

    async def _publish(
        self, row: schedule.SegmentRow, renderer: SegmentRenderer, prepared: Prepared
    ) -> tuple[str, str | None] | None:
        """Publish once; returns a state override for outcomes the result does not carry."""
        camera = self.config.camera
        try:
            await publish(self.publisher, prepared, renderer.directory)
            return None
        except catalog.Withdrawn:
            # A withdrawn video stays withdrawn: record it, never republish it.
            LOG.warning("Segment product was withdrawn; not republished (%s)", camera)
            return "unavailable", "withdrawn"
        except (catalog.RevisionConflict, catalog.StaleFence) as error:
            # Re-read what is committed: a deadline placeholder racing this commit (its newer
            # intent fences ours, or its commit moved the revision) converges right here.
            current = await schedule.current_video(self.engine, row.job)
            if not schedule.is_placeholder(current):
                if (
                    isinstance(error, catalog.StaleFence)
                    and current.revision == prepared.result.expected_revision
                ):
                    raise  # a newer attempt is still in flight: retry after it resolves
                # Someone else committed other content: terminal for this prepared result.
                LOG.warning("Segment video changed under the pipeline (%s)", camera)
                return "unavailable", "revision_conflict"
        # The deadline committed its database-only placeholder first: supersede it, recording
        # the new expected revision durably so a retry stays exactly-once.
        prepared = rebase(renderer, prepared, current.revision)
        try:
            await publish(self.publisher, prepared, renderer.directory)
        except catalog.Withdrawn:
            return "unavailable", "withdrawn"
        return None

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.catching_up = False
            if self.housekeeping is not None:
                # Staging cleanup (#19) runs ahead of rendering; it never raises.
                await self.housekeeping.tick(time.monotonic())
            try:
                await self.step(datetime.now(UTC))
            except Exception as error:
                metrics.FAILURES.labels(self.config.camera, "loop").inc()
                LOG.error("Pipeline loop failed (%s)", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), 0 if self.catching_up else LOOP_SECONDS)
            except TimeoutError:
                pass


async def main_async(config: PipelineConfig) -> None:
    recipe = replace(config.recipe, encoder=await asyncio.to_thread(ffmpeg.version))
    config = replace(config, recipe=recipe)
    engine = create_database_engine()
    store = ObjectStore.from_environment(MEDIA_BUCKET)
    # The pipeline holds the only originals key (#38); no other identity can read the bucket.
    originals = ObjectStore.from_environment(ORIGINALS_BUCKET)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await wait_for_database(engine)
        cleanup = Cleanup(
            config.camera,
            config.staging,
            SqlCatalog(engine),
            CleanupSettings.from_environment(),
            lag=timedelta(seconds=float(config.expected.max_lag)),
            expected=(
                config.expected.cadence_ms,
                config.expected.phase_ms,
                config.expected.width,
                config.expected.height,
            ),
        )
        worker = Worker(
            config,
            engine,
            Publisher(engine, {MEDIA_BUCKET: store, ORIGINALS_BUCKET: originals}),
            AggregateWeather(engine),
            archive_hours=True,
            # statvfs on a path that is the share itself: each pod mounts only its camera's
            # subPaths, so /staging is not on NFS.
            housekeeping=Housekeeping(
                cleanup, Pressure(config.camera, config.staging / "sealed" / config.camera)
            ),
        )
        # Previews (#39) run beside the renderer, so a long finalize never stalls them.
        previews = PreviewProducer(
            config.staging, config.expected, engine, skew=timedelta(seconds=config.skew_seconds)
        )
        await asyncio.gather(worker.run(stop), previews.run(stop))
    finally:
        await store.aclose()
        await originals.aclose()
        await engine.dispose()


def run(camera: str) -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        config = PipelineConfig.from_environment(camera)
        start_http_server(config.metrics_port)
        metrics.zero_pipeline(config.camera)
        asyncio.run(main_async(config))
    except Exception as error:
        LOG.error("Pipeline startup failed (%s)", type(error).__name__)
        raise SystemExit(2) from None
