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
import logging
import os
import signal
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
from weather.timemodel._instant import epoch_seconds

from . import ffmpeg, metrics, schedule
from .finalize import RESULT, Prepared, prepare, publish, rebase
from .hourly import HourlyArchive
from .intake import ChunkIndex, Expected, utc
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
        except ValueError:
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
        return config


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
    ) -> None:
        self.config, self.engine, self.publisher, self.weather = config, engine, publisher, weather
        self.stages = tuple(stages)
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

    def renderer(self, row: schedule.SegmentRow) -> SegmentRenderer:
        found = self.renderers.get(row.id)
        if found is None:
            found = SegmentRenderer(
                row.job,
                staging=self.config.staging,
                expected=self.config.expected,
                recipe=self.config.recipe,
                index=self.index,
                block=self.config.block,
                chunk_seconds=self.config.chunk_seconds,
                stages=self.stages,
            )
            self.renderers[row.id] = found
        return found

    async def step(self, now: datetime) -> None:
        camera = self.config.camera
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
                rendered_through = (
                    renderer.geometry.owned(0, renderer.rendered)[1] if renderer.rendered else None
                )
                for chunk in self.index.refresh(
                    renderer.geometry.start, min(renderer.geometry.end, current)
                ):
                    if rendered_through is not None:
                        late = sum(
                            1
                            for c in chunk.candidates
                            if renderer.geometry.start <= c.time < rendered_through
                        )
                        if late:
                            metrics.LATE.labels(camera).inc(late)
                if await asyncio.to_thread(renderer.advance, current):
                    released = renderer.released_through()
                    if released is not None:
                        await schedule.mark_through(self.engine, row.id, utc(released))
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
        if rows:
            self.index.prune(min(epoch_seconds(row.job.starts_at) for row in rows) - 3600)

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
            try:
                await self.step(datetime.now(UTC))
            except Exception as error:
                metrics.FAILURES.labels(self.config.camera, "loop").inc()
                LOG.error("Pipeline loop failed (%s)", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), LOOP_SECONDS)
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
        worker = Worker(
            config,
            engine,
            Publisher(engine, {MEDIA_BUCKET: store, ORIGINALS_BUCKET: originals}),
            AggregateWeather(engine),
            archive_hours=True,
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
