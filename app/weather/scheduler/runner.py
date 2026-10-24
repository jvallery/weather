# SPDX-License-Identifier: MIT
"""Single-concurrency scheduler with bounded leases and source-owned handlers."""

import asyncio
import contextlib
import importlib
import logging
import os
import signal
import time
from datetime import UTC, datetime
from uuid import uuid4

from prometheus_client import Counter, Gauge, start_http_server
from sqlalchemy.ext.asyncio import AsyncEngine

from weather.db import jobs
from weather.db.connection import create_database_engine
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext, Registry, UnregisteredJob

LOG = logging.getLogger("weather.scheduler")
CLAIMED = Counter("weather_jobs_claimed_total", "Claimed durable jobs")
FAILED = Counter("weather_jobs_failed_total", "Failed job attempts", ["reason"])
LOST = Counter("weather_job_leases_lost_total", "Lost or expired job leases")
OUTBOX_FAILED = Counter(
    "weather_outbox_dispatch_failures_total", "Outbox dispatch failures"
)
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
    except UnregisteredJob:
        FAILED.labels("unregistered").inc()
        LOG.error("Unregistered job type; failing durable job")
        async with engine.begin() as connection:
            await jobs.fail(connection, job, reason="unregistered", retry=False)
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


async def serve(engine: AsyncEngine, registry: Registry, stop: asyncio.Event) -> None:
    owner = str(uuid4())
    next_outbox = 0.0
    while not stop.is_set():
        try:
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
                job = await jobs.claim(connection, owner=owner)
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


def installed_registry(engine: AsyncEngine) -> Registry:
    registry = Registry()
    # This is a fixed source-owned module, never a path/module from request data.
    try:
        handlers = importlib.import_module("weather.scheduler.handlers")
    except ModuleNotFoundError as error:
        if error.name != "weather.scheduler.handlers":
            raise
    else:
        handlers.register(registry, engine)
    return registry


async def run() -> None:
    engine = create_database_engine()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await serve(engine, installed_registry(engine), stop)
    finally:
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
        LOG.error("Scheduler startup failed (%s)", type(error).__name__)
        raise SystemExit(2) from None
