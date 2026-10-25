# SPDX-License-Identifier: MIT
"""Single-concurrency scheduler with bounded leases and source-owned handlers."""

import asyncio
import contextlib
import logging
import os
import signal
import time
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, start_http_server
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.ai import store as analysis
from weather.db import jobs
from weather.db.connection import create_database_engine, wait_for_database
from weather.scheduler import handlers
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext, Registry

LOG = logging.getLogger("weather.scheduler")
CLAIMED = Counter("weather_jobs_claimed_total", "Claimed durable jobs")
FAILED = Counter("weather_jobs_failed_total", "Failed job attempts", ["reason"])
LOST = Counter("weather_job_leases_lost_total", "Lost or expired job leases")
OUTBOX_FAILED = Counter(
    "weather_outbox_dispatch_failures_total", "Outbox dispatch failures"
)
TICK_FAILED = Counter("weather_scheduler_tick_failures_total", "Periodic tick failures")
TICK_SECONDS = 60
ALIVE = Gauge(
    "weather_scheduler_database_ready",
    "Scheduler's most recent database operation worked",
)


async def _heartbeat(
    engine: AsyncEngine, job: jobs.Job, owner_task: asyncio.Task[None]
) -> None:
    try:
        while True:
            await asyncio.sleep(15)
            async with engine.begin() as connection:
                if not await jobs.heartbeat(connection, job):
                    owner_task.cancel()
                    return
    except Exception:
        owner_task.cancel()


async def _run_job(engine: AsyncEngine, registry: Registry, job: jobs.Job) -> None:
    current = asyncio.current_task()
    assert current is not None
    context = JobContext(engine, job)
    beat = asyncio.create_task(_heartbeat(engine, job, current))
    try:
        handler = registry.handler(job.kind)
        remaining = (job.deadline - datetime.now(UTC)).total_seconds()
        async with asyncio.timeout(max(0, remaining)):
            result = await handler(context)
            if not context.committed:
                await context.commit(result=result)
    except jobs.LeaseLost:
        LOST.inc()
        LOG.warning("Job lease lost; catalog transaction rolled back")
    except asyncio.CancelledError:
        # Heartbeat loss or shutdown cannot authorize a completion.
        LOST.inc()
        raise
    except Exception as error:
        FAILED.labels("handler").inc()
        # Never log exceptions/SQL URLs, payloads, source IDs or private data.
        LOG.error("Job handler failed (%s)", type(error).__name__)
        async with engine.begin() as connection:
            await jobs.fail(
                connection,
                job,
                reason="handler",
                retry=job.attempt < 5,
                delay_seconds=min(300, 5 * 2 ** min(job.attempt, 6)),
            )
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


async def tick(connection: AsyncConnection, now: datetime) -> None:
    """Idempotent periodic work: deterministic job keys, then bounded retention."""
    await handlers.periodic(connection, now)
    await jobs.prune(connection)
    # Private AI replies expire after 24 h even while intel is disabled (#23).
    await analysis.purge_raw(connection)


async def serve(engine: AsyncEngine, registry: Registry, stop: asyncio.Event) -> None:
    kinds = registry.kinds()
    next_tick = next_outbox = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_tick:
                try:
                    async with engine.begin() as connection:
                        await tick(connection, datetime.now(UTC))
                    next_tick = time.monotonic() + TICK_SECONDS
                except Exception as error:
                    TICK_FAILED.inc()
                    LOG.error("Scheduler tick failed (%s)", type(error).__name__)
                    next_tick = time.monotonic() + 30
            if time.monotonic() >= next_outbox:
                try:
                    async with engine.begin() as connection:
                        await dispatch(connection, registry)
                    next_outbox = time.monotonic() + 1
                except Exception as error:
                    OUTBOX_FAILED.inc()
                    LOG.error("Outbox dispatch failed (%s)", type(error).__name__)
                    next_outbox = time.monotonic() + 30
            async with engine.begin() as connection:
                await jobs.expire(connection)
                job = await jobs.claim(connection, kinds=kinds)
            ALIVE.set(1)
            if job is not None:
                CLAIMED.inc()
                await _run_job(engine, registry, job)
                continue
        except asyncio.CancelledError:
            if stop.is_set():
                return
            # A lost per-job lease cancels that attempt, not the scheduler process.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as error:
            ALIVE.set(0)
            LOG.error("Scheduler database operation failed (%s)", type(error).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)


def installed_registry() -> Registry:
    registry = Registry()
    handlers.register(registry)
    return registry


async def run() -> None:
    engine = create_database_engine()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await wait_for_database(engine)
        await serve(engine, installed_registry(), stop)
    finally:
        await engine.dispose()


def main() -> None:
    from weather.pipeline import metrics as pipeline_metrics

    logging.basicConfig(level=logging.INFO)
    try:
        port = int(os.environ.get("METRICS_PORT", "9000"))
        if not 1 <= port <= 65535:
            raise ValueError("metrics port")
        start_http_server(port)
        pipeline_metrics.zero_scheduler()
        asyncio.run(run())
    except Exception as error:
        LOG.error("Scheduler startup failed (%s)", type(error).__name__)
        raise SystemExit(2) from None
