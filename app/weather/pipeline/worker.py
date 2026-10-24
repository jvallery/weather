# SPDX-License-Identifier: MIT
"""`weather pipeline run --camera <id>`: one camera's streaming render and publish loop.

Every ten seconds: load the camera's started, unfinished segments; render every block whose
inputs have had time to seal; once a segment's mezzanine is complete, prepare (encode,
validate, posters, sidecar) and publish through #18, then record the result on the segment.
Each segment fails alone and retries on the next loop. CPU work runs in a worker thread so
the metrics endpoint stays responsive.
"""

import asyncio
import logging
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.db.connection import create_database_engine, wait_for_database
from weather.publish import catalog
from weather.publish.model import MEDIA_BUCKET, Product
from weather.publish.objects import ObjectStore
from weather.publish.publisher import Publisher
from weather.timemodel._instant import epoch_seconds

from . import ffmpeg, metrics, schedule
from .finalize import RESULT, prepare, publish
from .intake import ChunkIndex, Expected, utc
from .recipe import CAMERAS, DEFAULT_PHASE_MS, MAX_BLOCK, Recipe, from_environment
from .render import ChunkStage, SegmentJob, SegmentRenderer
from .summary import AggregateWeather, WeatherSummaryProvider

LOG = logging.getLogger("weather.pipeline")
LOOP_SECONDS = 10


@dataclass(frozen=True)
class PipelineConfig:
    camera: str
    staging: Path
    expected: Expected
    recipe: Recipe
    block: int = MAX_BLOCK
    chunk_seconds: int = 60
    metrics_port: int = 9000

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
                cadence_ms=int(env.get("WEATHER_PIPELINE_CADENCE_MS", "1000")),
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
            )
        except ValueError:
            raise ValueError("Invalid pipeline configuration") from None
        if (
            not config.staging.is_absolute()
            or not 1 <= config.block <= MAX_BLOCK
            or not 1 <= config.chunk_seconds <= 300
            or not 1000 <= expected.cadence_ms <= 3_600_000
            or not 0 <= expected.phase_ms < expected.cadence_ms
            or not (0 < width <= 16384 and 0 < height <= 16384)
        ):
            raise ValueError("Invalid pipeline configuration")
        return config


async def current_revision(engine: AsyncEngine, job: SegmentJob) -> int:
    identity = Product.video(
        camera_id=job.camera,
        cycle_date=job.cycle_date,
        segment=job.kind,
        starts_at=job.starts_at,
        ends_at=job.ends_at,
        frames=job.frames,
        public_not_before=job.ends_at,
        state="unavailable",
        reason="identity",
        files=(),
    )
    async with engine.connect() as connection:
        return (await catalog.current(connection, identity)).revision


class Worker:
    def __init__(
        self,
        config: PipelineConfig,
        engine: AsyncEngine,
        publisher: Publisher,
        weather: WeatherSummaryProvider,
        *,
        stages: Sequence[ChunkStage] = (),
    ) -> None:
        self.config, self.engine, self.publisher, self.weather = config, engine, publisher, weather
        self.stages = tuple(stages)
        self.index = ChunkIndex(config.staging, config.expected)
        self.renderers: dict[UUID, SegmentRenderer] = {}
        self.durable: set[UUID] = set()

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
        current = epoch_seconds(now)
        for row in rows:
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
                    revision = await current_revision(self.engine, row.job)
                    weather = await self.weather.summary(row.job.starts_at, row.job.ends_at)
                prepared = await asyncio.to_thread(
                    prepare, renderer, weather=weather, expected_revision=revision
                )
                stage = "publish"
                outcome: tuple[str, str | None] | None = None
                try:
                    await publish(self.publisher, prepared, renderer.directory)
                except catalog.Withdrawn:
                    # A withdrawn video stays withdrawn: record it, never republish it.
                    LOG.warning("Segment product was withdrawn; not republished (%s)", camera)
                    outcome = ("unavailable", "withdrawn")
                stage = "catalog"
                result = prepared.result.model_dump(mode="json")
                await schedule.finish(self.engine, row.id, result, outcome=outcome)
                metrics.SEGMENTS.labels(camera, outcome[0] if outcome else result["state"]).inc()
                metrics.COVERAGE.labels(camera).set(prepared.result.slots.fraction)
                metrics.LATENCY.labels(camera).observe((now - row.job.ends_at).total_seconds())
                del self.renderers[row.id]
            except Exception as error:
                metrics.FAILURES.labels(camera, stage).inc()
                # Never log paths, payloads or driver errors; the type and stage suffice.
                LOG.error("Pipeline %s failed (%s, %s)", stage, camera, type(error).__name__)
        if rows:
            self.index.prune(min(epoch_seconds(row.job.starts_at) for row in rows) - 3600)

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
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await wait_for_database(engine)
        worker = Worker(
            config, engine, Publisher(engine, {MEDIA_BUCKET: store}), AggregateWeather(engine)
        )
        await worker.run(stop)
    finally:
        await store.aclose()
        await engine.dispose()


def run(camera: str) -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        config = PipelineConfig.from_environment(camera)
        start_http_server(config.metrics_port)
        asyncio.run(main_async(config))
    except Exception as error:
        LOG.error("Pipeline startup failed (%s)", type(error).__name__)
        raise SystemExit(2) from None
