# SPDX-License-Identifier: MIT
"""The `intel` role: analysis and embedding jobs, one at a time, plus its periodic upkeep.

It claims only `analysis.describe`, `analysis.synthesize` and `search.embed`; the scheduler
stays the only outbox dispatcher. Every Cortex call (chat or embedding) takes the cross-process
advisory slot, so any number of intel replicas keep one call in flight. The tick purges raw
responses past 24 h, queues nightly retries, fails heads pending for 7 days and checks recent
revisions for changed measurements.
"""

import asyncio
import contextlib
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from prometheus_client import Gauge, start_http_server
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.db import jobs
from weather.db.connection import create_database_engine, wait_for_database
from weather.media.garage import media_store
from weather.pipeline.summary import AggregateWeather
from weather.scheduler.registry import Registry
from weather.scheduler.runner import _run_job
from weather.search import documents
from weather.search.cortex import DEFAULT_PROFILE, PROFILES, Embedder

from . import store
from .analyze import Analyzer, register
from .client import CORTEX_BASE_URL, Cortex, check_base_url, load_key
from .media import GarageMedia
from .slot import CortexSlot

LOG = logging.getLogger("weather.ai")
TICK_SECONDS = 60
EVIDENCE_SECONDS = 900
QUEUE = Gauge("weather_ai_queue_depth", "Queued or running analysis jobs")
OLDEST = Gauge("weather_ai_oldest_pending_seconds", "Age of the oldest pending analysis")
PURGED = Gauge("weather_ai_raw_purged_last", "Raw responses purged by the last tick")
ALIVE = Gauge("weather_intel_database_ready", "Intel's most recent database operation worked")


async def tick(engine: AsyncEngine, now: datetime) -> None:
    async with engine.begin() as connection:
        PURGED.set(await store.purge_raw(connection))
        await store.expire(connection, now)
        await store.nightly(connection, now)
        depth, oldest = await store.queue_depth(connection)
    QUEUE.set(depth)
    OLDEST.set(oldest)


async def serve(
    engine: AsyncEngine, registry: Registry, analyzer: Analyzer, stop: asyncio.Event
) -> None:
    kinds = registry.kinds()
    next_tick = next_evidence = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_tick:
                await tick(engine, datetime.now(UTC))
                next_tick = time.monotonic() + TICK_SECONDS
            if time.monotonic() >= next_evidence:
                await analyzer.check_evidence(datetime.now(UTC))
                next_evidence = time.monotonic() + EVIDENCE_SECONDS
            async with engine.begin() as connection:
                job = await jobs.claim(connection, kinds=kinds)
            ALIVE.set(1)
            if job is not None:
                await _run_job(engine, registry, job)
                continue
        except asyncio.CancelledError:
            if stop.is_set():
                return
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as error:
            ALIVE.set(0)
            LOG.error("Intel loop failed (%s)", type(error).__name__)
            next_tick = min(next_tick, time.monotonic() + 30)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)


async def run() -> None:
    base = check_base_url(os.environ.get("WEATHER_CORTEX_BASE_URL"))
    profile = os.environ.get("WEATHER_SEARCH_PROFILE", DEFAULT_PROFILE)
    if profile not in PROFILES:
        raise ValueError("Unknown embedding profile")
    key = load_key()
    store_ = media_store()
    if key is None or store_ is None:
        # Ships disabled until weather-cortex and weather-media-s3-reader exist (#23, #50).
        raise RuntimeError("Cortex key or media reader key missing")
    # One loop connection, the inference slot's session and a short raw-response write.
    engine = create_database_engine(
        pool_size=4,
        server_settings={"application_name": "weather-intel", "statement_timeout": "60000"},
    )
    cortex = Cortex(key, base_url=base)
    embedder = Embedder(base, key, PROFILES[profile])
    slot = CortexSlot(engine)
    scratch = Path(os.environ.get("TMPDIR", "/tmp"))
    analyzer = Analyzer(
        engine,
        cortex,
        slot,
        GarageMedia(store_, scratch),
        AggregateWeather(engine),
        profile_id=profile,
    )
    registry = Registry()
    register(registry, analyzer)
    documents.register(registry, embedder, slot=slot)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await wait_for_database(engine)
        await serve(engine, registry, analyzer, stop)
    finally:
        await cortex.aclose()
        await embedder.aclose()
        await store_.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        port = int(os.environ.get("METRICS_PORT", "9000"))
        if not 1 <= port <= 65535:
            raise ValueError("metrics port")
        start_http_server(port)
        asyncio.run(run())
    except Exception as error:
        LOG.error("Intel startup failed (%s)", type(error).__name__)
        raise SystemExit(2) from None


__all__ = ["CORTEX_BASE_URL", "main", "serve", "tick"]
