# SPDX-License-Identifier: MIT
"""UNRUN fleet-only Changed/dispatch/revision/fence integration proofs.

Never execute on the Mac. The imported fixture validates the disposable socket,
server marker, PostgreSQL16.15 and vector0.8.6 before any test writes occur.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from test_current_publication_integration import device, engine, guard, incident, observation
from test_current_publication_integration import disposable_database as disposable_database
from weather.api.aggregate_config import support_binding
from weather.api.support_assembly import SupportPolicy
from weather.db import aggregate_store, jobs
from weather.scheduler.aggregate_worker import AggregateRevisionWorker, Target
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext, Registry

pytestmark = pytest.mark.integration
POLICIES = {"temperature": SupportPolicy("forward_hold", "synthetic_hold_v1", hold_seconds=3600)}


@pytest.fixture(autouse=True)
def worker_database(disposable_database: None) -> None:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                await guard(connection)
                assert (
                    await connection.execute(
                        text(
                            "SELECT to_regclass('weather.aggregate_revision') IS NOT NULL "
                            "AND to_regclass('public_api.telemetry_evidence_revisions') IS NOT NULL"
                        )
                    )
                ).scalar_one()
                await connection.execute(
                    text(
                        "TRUNCATE weather.aggregate_head,"
                        "weather.aggregate_revision,weather.outbox,weather.job CASCADE"
                    )
                )
        finally:
            await db.dispose()

    asyncio.run(clean())


def worker(db: Any) -> AggregateRevisionWorker:
    return AggregateRevisionWorker(db, POLICIES, support_binding(POLICIES))


def closed_hour() -> Target:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return Target("hour", end - timedelta(hours=1), end)


def payload(
    value: AggregateRevisionWorker, target: Target, alias: str, boundary: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": 1,
        "station": alias,
        "kind": target.kind,
        "start": target.start.isoformat().replace("+00:00", "Z"),
        "end": target.end.isoformat().replace("+00:00", "Z"),
        "policy": value.policy_revision,
    }
    if boundary is not None:
        result["watermark"] = boundary
    return result


async def enqueue_context(
    db: Any,
    value: AggregateRevisionWorker,
    target: Target,
    alias: str,
    key: str,
    boundary: int | None = None,
) -> JobContext:
    now = datetime.now(UTC)
    async with db.begin() as connection:
        await jobs.enqueue(
            connection,
            kind="aggregate.recompute",
            key=key,
            payload=payload(value, target, alias, boundary),
            not_before=now,
            deadline=now + timedelta(minutes=5),
        )
        claimed = await jobs.claim(connection, owner="synthetic-worker", lease_seconds=300)
    assert claimed is not None and claimed.kind == "aggregate.recompute"
    return JobContext(db, claimed)


async def drain(db: Any, registry: Registry) -> None:
    async with db.begin() as connection:
        await dispatch(connection, registry)
    for _ in range(30):
        async with db.begin() as connection:
            claimed = await jobs.claim(connection, owner="synthetic-worker", lease_seconds=300)
        if claimed is None:
            return
        context = JobContext(db, claimed)
        result = await registry.handler(claimed.kind)(context)
        if not context.committed:
            await context.commit(result=result)
    raise AssertionError("Synthetic worker exceeded its bounded job budget")


async def station(connection: Any) -> tuple[Any, str]:
    identity = await device(connection)
    alias = (
        await connection.execute(
            text(
                "SELECT s.alias FROM weather.station s JOIN weather.device d ON d.station_id=s.id "
                "WHERE d.id=:id"
            ),
            {"id": identity},
        )
    ).scalar_one()
    return identity, alias


async def head(connection: Any, target: Target) -> Any:
    return (
        (
            await connection.execute(
                text("""SELECT r.scope_key,r.id,r.document,h.watermark
    FROM weather.aggregate_head h JOIN weather.aggregate_revision r ON r.id=h.revision_id
    WHERE r.starts_at=:start AND r.ends_at=:end AND r.metric='temperature'
      AND r.statistic='mean' """),
                {"start": target.start, "end": target.end},
            )
        )
        .mappings()
        .one()
    )


def test_changed_dispatch_retains_corrections_and_stale_jobs_preserve_head() -> None:
    async def check() -> None:
        admin, db, ingest = engine(), engine("weather_worker"), engine("weather_ingest")
        value, registry, target = worker(db), Registry(), closed_hour()
        value.register(registry)
        try:
            async with admin.begin() as connection:
                d, alias = await station(connection)
                first, raw = await observation(
                    connection,
                    d,
                    source=target.start + timedelta(minutes=5),
                    received=target.start + timedelta(minutes=5),
                    values={"air_temperature": 10},
                )
            await drain(db, registry)
            async with admin.begin() as connection:
                initial = dict(await head(connection, target))
                assert initial["document"]["value"] == 10
                await observation(
                    connection,
                    d,
                    source=target.start + timedelta(minutes=30),
                    received=target.start + timedelta(minutes=30),
                    values={"air_temperature": 20},
                )
            await drain(db, registry)
            async with admin.begin() as connection:
                late = dict(await head(connection, target))
                assert late["id"] != initial["id"] and late["document"]["value"] > 10
                conflict = await incident(
                    connection, first, raw, source=target.start + timedelta(minutes=5)
                )
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            await drain(db, registry)
            async with admin.begin() as connection:
                revised = dict(await head(connection, target))
                assert revised["id"] not in {initial["id"], late["id"]}
                count = (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM weather.aggregate_revision WHERE scope_key=:scope"
                        ),
                        {"scope": revised["scope_key"]},
                    )
                ).scalar_one()
                assert count == 3
                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM weather.job "
                            "WHERE kind='aggregate.conflict' AND status='succeeded'"
                        )
                    )
                ).scalar_one() == 1
            stale = await enqueue_context(db, value, target, alias, "synthetic-stale", 1)
            result = await value.recompute(stale)
            assert result["superseded"] == 3
            async with admin.begin() as connection:
                assert dict(await head(connection, target)) == revised
                assert (
                    await connection.execute(
                        text("SELECT status FROM weather.job WHERE id=:id"), {"id": stale.job.id}
                    )
                ).scalar_one() == "succeeded"
                # Every retained result belongs to a closed interval; active days
                # may have queued close jobs but never an aggregate record.
                assert (
                    await connection.execute(
                        text(
                            "SELECT bool_and(ends_at<=clock_timestamp()) "
                            "FROM weather.aggregate_revision"
                        )
                    )
                ).scalar_one()
        finally:
            await ingest.dispose()
            await db.dispose()
            await admin.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("loss", ["before", "during"])
def test_lease_loss_rolls_back_aggregate_catalog_and_job_completion(
    monkeypatch: pytest.MonkeyPatch,
    loss: str,
) -> None:
    async def check() -> None:
        admin, db = engine(), engine("weather_worker")
        target, value = closed_hour(), worker(db)
        try:
            async with admin.begin() as connection:
                d, alias = await station(connection)
                await observation(
                    connection,
                    d,
                    source=target.start + timedelta(minutes=5),
                    received=target.start + timedelta(minutes=5),
                    values={"air_temperature": 10},
                )
            context = await enqueue_context(db, value, target, alias, "synthetic-fence")
            if loss == "before":
                async with admin.begin() as connection:
                    await connection.execute(
                        text("UPDATE weather.job SET fencing_token=fencing_token+1 WHERE id=:id"),
                        {"id": context.job.id},
                    )
            else:
                original = aggregate_store.persist

                async def expire_after_write(connection: Any, item: Any) -> Any:
                    status = await original(connection, item)
                    await connection.execute(
                        text(
                            "UPDATE weather.job SET lease_until="
                            "clock_timestamp()-interval '1 second' WHERE id=:id"
                        ),
                        {"id": context.job.id},
                    )
                    return status

                monkeypatch.setattr(aggregate_store, "persist", expire_after_write)
            with pytest.raises(jobs.LeaseLost):
                await value.recompute(context)
            async with admin.begin() as connection:
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.aggregate_revision")
                    )
                ).scalar_one() == 0
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.aggregate_head"))
                ).scalar_one() == 0
                assert (
                    await connection.execute(
                        text("SELECT status FROM weather.job WHERE id=:id"), {"id": context.job.id}
                    )
                ).scalar_one() == "running"
        finally:
            await db.dispose()
            await admin.dispose()

    asyncio.run(check())


def test_forged_change_without_authoritative_revision_creates_no_rollup_jobs() -> None:
    async def check() -> None:
        admin, db = engine(), engine("weather_worker")
        target, value = closed_hour(), worker(db)
        try:
            async with admin.begin() as connection:
                d, alias = await station(connection)
                observation_id, _ = await observation(
                    connection,
                    d,
                    source=target.start,
                    received=target.start,
                    values={"air_temperature": 10},
                )
            context = await enqueue_context(db, value, target, alias, "synthetic-forged")
            context.job.payload.clear()
            context.job.payload.update(
                id=str(observation_id),
                family="obs_st",
                station=alias,
                sourceTime=target.start.isoformat(),
                watermark=99999,
            )
            with pytest.raises(ValueError, match="authoritative"):
                await value.changed(context)
            async with admin.begin() as connection:
                assert (
                    await connection.execute(text("SELECT count(*) FROM weather.job"))
                ).scalar_one() == 1
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.aggregate_revision")
                    )
                ).scalar_one() == 0
        finally:
            await db.dispose()
            await admin.dispose()

    asyncio.run(check())
