# SPDX-License-Identifier: MIT
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from weather.db import jobs
from weather.scheduler import runner
from weather.scheduler.registry import JobContext, Registry


def test_heartbeat_loss_cancels_handler_without_completing_and_counts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sleep = asyncio.sleep

    class Engine:
        @asynccontextmanager
        async def begin(self) -> AsyncIterator[None]:
            yield None

    async def expired(connection: Any, job: jobs.Job) -> bool:
        return False

    async def no_delay(seconds: float) -> None:
        await real_sleep(0)

    def loss_count() -> float:
        return next(
            sample.value
            for metric in runner.LOST.collect()
            for sample in metric.samples
            if sample.name == "weather_job_leases_lost_total"
        )

    async def check() -> None:
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def stalled(context: JobContext) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        registry = Registry()
        registry.register("stalled", stalled)
        job = jobs.Job(
            uuid4(),
            "stalled",
            {},
            None,
            datetime.now(UTC) + timedelta(minutes=10),
            1,
            "test",
            1,
        )
        before = loss_count()
        with pytest.raises(asyncio.CancelledError):
            await runner._run_job(Engine(), registry, job)  # type: ignore[arg-type]
        assert entered.is_set() and cancelled.is_set()
        assert loss_count() - before == 1

    monkeypatch.setattr(jobs, "heartbeat", expired)
    monkeypatch.setattr(runner.asyncio, "sleep", no_delay)
    asyncio.run(check())
