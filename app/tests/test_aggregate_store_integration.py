# SPDX-License-Identifier: MIT
"""Actual retained-revision/fence proofs, restricted to the disposable cluster database."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from weather.api.aggregate_adapter import compute
from weather.db import jobs
from weather.db.aggregate_store import persist, prepare
from weather.products.aggregate import Interval, Support
from weather.scheduler.registry import JobContext

pytestmark = pytest.mark.integration
START = datetime(2026, 1, 1, tzinfo=UTC)


def engine(role="weather_worker"):
    url = os.environ.get("WEATHER_CI_DATABASE_URL")
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1" or not url:
        pytest.fail("Aggregate revision proofs require disposable in-cluster PostgreSQL")
    parsed = make_url(url)
    if parsed.database != "weather_ci" or parsed.host not in {None, ""}:
        pytest.fail("Aggregate revision proofs require the isolated socket-only database")
    return create_async_engine(
        parsed.set(username=role), echo=False, hide_parameters=True, pool_size=2, max_overflow=0
    )


@pytest.fixture(autouse=True)
def disposable_database():
    async def clean():
        db = engine("weather_migrator")
        try:
            async with db.begin() as connection:
                row = (
                    await connection.execute(
                        text("""SELECT current_database(),
                    current_setting('server_version_num')::integer,
                    current_setting('weather.integration_test',true),inet_server_addr(),
                    (SELECT extversion FROM pg_extension WHERE extname='vector')""")
                    )
                ).one()
                assert tuple(row) == ("weather_ci", 160015, "on", None, "0.8.6")
                await connection.execute(
                    text("""TRUNCATE weather.aggregate_head,
                    weather.aggregate_revision,weather.outbox,weather.job CASCADE""")
                )
        finally:
            await db.dispose()

    asyncio.run(clean())


def value(number=Fraction(1, 3), revision="evidence_1", boundary="1"):
    window = Interval(START, START + timedelta(seconds=60))
    result = compute(
        window,
        "temperature",
        "mean",
        {
            "temperature": [Support(window, number, "synthetic-evidence")],
        },
        evidence_revision=revision,
    )
    return prepare(
        "Synthetic fixture",
        result,
        boundary=boundary,
        policy_revision="policy_1",
        evaluated_at=START + timedelta(hours=1),
        policy_document={},
    )


async def context(db, key):
    now = datetime.now(UTC)
    async with db.begin() as connection:
        await jobs.enqueue(
            connection,
            kind="aggregate.recompute",
            key=key,
            payload={},
            not_before=now,
            deadline=now + timedelta(minutes=1),
        )
        job = await jobs.claim(connection, owner="synthetic-worker")
    assert job is not None
    return JobContext(db, job)


def test_late_revision_is_retained_and_duplicate_computation_is_idempotent():
    async def check():
        db = engine()
        try:
            first, correction = value(), value(Fraction(2, 3), "evidence_2", "2")
            statuses = []
            for index, item in enumerate((first, first, correction)):
                owner = await context(db, "revision-" + str(index))

                async def write(connection, current=item):
                    statuses.append(await persist(connection, current))

                await owner.commit(write)
            assert statuses == ["created", "unchanged", "created"]
            async with db.begin() as connection:
                rows = (
                    await connection.execute(
                        text("""SELECT id,exact_numerator,
                    exact_denominator FROM weather.aggregate_revision ORDER BY watermark""")
                    )
                ).all()
                assert [tuple(row) for row in rows] == [
                    (first.id, "1", "3"),
                    (correction.id, "2", "3"),
                ]
                head = (
                    await connection.execute(
                        text("""SELECT revision_id,revision,watermark
                    FROM weather.aggregate_head""")
                    )
                ).one()
                assert tuple(head) == (correction.id, 2, 2)
        finally:
            await db.dispose()

    asyncio.run(check())


def test_expired_job_fence_rolls_back_revision_and_head_together():
    async def check():
        db = engine()
        try:
            owner = await context(db, "expiry")

            async def expired(connection):
                await persist(connection, value())
                await connection.execute(
                    text("""UPDATE weather.job
                    SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"""),
                    {"id": owner.job.id},
                )

            with pytest.raises(jobs.LeaseLost):
                await owner.commit(expired)
            async with db.begin() as connection:
                for table in ("aggregate_revision", "aggregate_head"):
                    assert (
                        await connection.execute(text("SELECT count(*) FROM weather." + table))
                    ).scalar_one() == 0
        finally:
            await db.dispose()

    asyncio.run(check())


def test_older_snapshot_cannot_replace_newer_head():
    async def check():
        db = engine()
        try:
            newer = value(Fraction(2, 3), "evidence_2", "2")
            async with db.begin() as connection:
                assert await persist(connection, newer) == "created"
                assert await persist(connection, value()) == "superseded"
                assert (
                    await connection.execute(text("SELECT revision_id FROM weather.aggregate_head"))
                ).scalar_one() == newer.id
        finally:
            await db.dispose()

    asyncio.run(check())


def test_reused_identity_with_different_evidence_is_rejected_even_for_current_head():
    async def check():
        db = engine()
        try:
            original = value()
            async with db.begin() as connection:
                await persist(connection, original)
            altered = dict(original.parameters)
            altered["numerator"] = "2"
            with pytest.raises(ValueError, match="different evidence"):
                async with db.begin() as connection:
                    await persist(connection, replace(original, parameters=altered))
        finally:
            await db.dispose()

    asyncio.run(check())


def test_public_reader_only_gets_whitelisted_projection_and_revisions_are_append_only():
    async def check():
        worker, api, admin = engine(), engine("weather_api"), engine("weather_migrator")
        try:
            async with worker.begin() as connection:
                await persist(connection, value())
                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(text("DELETE FROM weather.aggregate_revision"))
                await connection.execute(
                    text("""INSERT INTO weather.aggregate_revision
                  (id,scope_key,station_alias,starts_at,ends_at,metric,statistic,policy_revision,
                   watermark,evaluated_at,policy_document,document,
                   exact_numerator,exact_denominator,details)
                  SELECT 'synthetic_privacy_fixture',scope_key,station_alias,starts_at,ends_at,
                    metric,statistic,policy_revision,watermark,evaluated_at,policy_document,
                    jsonb_set(document,'{coverage,private_extra}','"synthetic_private_marker"'),
                    exact_numerator,exact_denominator,details
                  FROM weather.aggregate_revision LIMIT 1""")
                )
            async with admin.begin() as connection:
                with pytest.raises(DBAPIError) as error:
                    async with connection.begin_nested():
                        await connection.execute(text("DELETE FROM weather.aggregate_revision"))
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
            async with api.begin() as connection:
                for table in ("aggregate_revision", "aggregate_head"):
                    with pytest.raises(DBAPIError) as error:
                        async with connection.begin_nested():
                            await connection.execute(text("SELECT * FROM weather." + table))
                    assert getattr(error.value.orig, "sqlstate", None) == "42501"
                document = (
                    await connection.execute(
                        text("""SELECT document
                    FROM public_api.aggregate_revisions
                    WHERE id='synthetic_privacy_fixture'""")
                    )
                ).scalar_one()
                assert document["metric"] == "temperature" and document["value"] > 0
                assert "details" not in document and "exact_numerator" not in document
                assert "synthetic_private_marker" not in json.dumps(document)
                assert set(document["coverage"]) == {
                    "fraction",
                    "support_seconds",
                    "expected_seconds",
                    "algorithm_revision",
                    "gaps",
                }
        finally:
            await api.dispose()
            await worker.dispose()
            await admin.dispose()

    asyncio.run(check())
