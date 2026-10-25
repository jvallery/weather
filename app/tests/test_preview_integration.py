# SPDX-License-Identifier: MIT
"""Previews (#39) against real PostgreSQL: the 0007 CHECKs, the security-barrier view's own
clock, grants, the producer's INSERT as the worker and the pointer read as the API role.

Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.db.models import metadata
from weather.pipeline import synthetic
from weather.pipeline.intake import Expected
from weather.preview.producer import PreviewProducer
from weather.preview.server import Files, PreviewMetrics, PreviewService, Source

pytestmark = pytest.mark.integration

NS = 1_000_000_000
W, H = 64, 32
INSERT = """
    INSERT INTO weather.camera_preview (camera_id,generation,variant,width,height,bytes,sha256,
      path,captured_lower,captured_upper,public_not_before,first_published_at,expires_at)
    SELECT :camera,:generation,:variant,64,32,10,:sha,:path,t.lower,t.upper,t.nb,t.nb,
      t.lower + interval '3600 seconds'
    FROM (SELECT n - make_interval(secs => a) AS lower, n - make_interval(secs => a - 1) AS upper,
            n - make_interval(secs => a - 631 - d) AS nb
          FROM (SELECT clock_timestamp() AS n, CAST(:age AS double precision) AS a,
                  CAST(:delay AS double precision) AS d) v) t
    RETURNING id
"""


def engine(role: str = "postgres") -> AsyncEngine:
    if os.environ.get("WEATHER_CI_IN_CLUSTER") != "1":
        pytest.fail("Integration requires fleet CI")
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket_dir = Path(str(url.query.get("host", "")))
    if url.database != "weather_ci" or not socket_dir.parent.name.startswith("weather-schema-ci."):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=4, max_overflow=0, pool_timeout=2)


@pytest.fixture(autouse=True)
def clean_test_database() -> None:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await connection.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
        finally:
            await db.dispose()

    asyncio.run(clean())


async def rejects(connection: AsyncConnection, params: dict[str, Any], sqlstate: str) -> None:
    sql = INSERT
    for name, expression in params.pop("replace", {}).items():
        sql = sql.replace(name, expression)
    with pytest.raises(DBAPIError) as failure:
        async with connection.begin_nested():
            await connection.execute(text(sql), params)
    assert getattr(failure.value.orig, "sqlstate", None) == sqlstate


def row(
    generation: int, *, age: int = 700, delay: int = 0, variant: str = "large"
) -> dict[str, Any]:
    return {
        "camera": "skycam_east",
        "generation": generation,
        "variant": variant,
        "sha": "a" * 64,
        "path": f"previews/skycam_east/{generation}-{variant}.jpg",
        "age": age,
        "delay": delay,
    }


def test_checks_view_clock_and_grants() -> None:
    async def check() -> None:
        admin, api = engine(), engine("weather_api")
        try:
            async with admin.begin() as connection:
                visible = (await connection.execute(text(INSERT), row(1))).scalar_one()
                # Eligible one second from now by the database clock.
                embargoed = (await connection.execute(text(INSERT), row(2, age=630))).scalar_one()
                expired = (await connection.execute(text(INSERT), row(3, age=3601))).scalar_one()
                # Every CHECK: embargo margin, reversed bounds, path, expiry, first publication.
                await rejects(connection, {**row(4), "delay": -1}, "23514")
                await rejects(
                    connection,
                    {**row(5), "replace": {"secs => a - 1)": "secs => a + 1)"}},
                    "23514",
                )
                await rejects(
                    connection,
                    {**row(6), "path": "previews/skycam_west/6-large.jpg"},
                    "23514",
                )
                await rejects(connection, {**row(7), "path": "sealed/skycam_east/x.tar"}, "23514")
                await rejects(
                    connection,
                    {**row(8), "replace": {"interval '3600 seconds'": "interval '7200 seconds'"}},
                    "23514",
                )
                await rejects(connection, {**row(9), "variant": "native"}, "23514")
            async with api.connect() as connection:
                ids = set(
                    (await connection.execute(text("SELECT id FROM public_api.previews"))).scalars()
                )
                assert ids == {visible}
                await asyncio.sleep(2.2)
                ids = set(
                    (await connection.execute(text("SELECT id FROM public_api.previews"))).scalars()
                )
                assert ids == {visible, embargoed} and expired not in ids
                with pytest.raises(DBAPIError) as denied:
                    await connection.execute(text("SELECT 1 FROM weather.camera_preview"))
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        finally:
            await admin.dispose()
            await api.dispose()

    asyncio.run(check())


def test_producer_commits_as_worker_and_pointer_reads_as_api(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(minutes=15)
    frames = [
        synthetic.Frame(int(start.timestamp() + s) * NS, synthetic.solid(W, H, s % 250))
        for s in range(0, 600, 2)
    ]
    synthetic.write_chunks(tmp_path, "skycam_east", frames, geometry=(W, H), cadence_ms=2000)

    async def check() -> None:
        worker, api = engine("weather_worker"), engine("weather_api")
        metrics = PreviewMetrics(CollectorRegistry())
        files = Files(tmp_path / "previews", metrics)
        try:
            producer = PreviewProducer(tmp_path, Expected("skycam_east", 2000, 0, W, H), worker)
            assert await producer.step()
            assert not await producer.step()  # same minute
            service = PreviewService(Source(api), files, metrics)
            body = (await service.pointer("skycam_east")).model_dump(mode="json")
            assert body["state"] == "fresh" and len(body["images"]) == 2
            assert body["age_seconds"] > 630
            url = body["images"][0]["url"]
            found = await service.resolve(UUID(url.rsplit("/", 1)[1]))
            assert found is not None
            on_disk = (tmp_path / found.locator).read_bytes()
            assert hashlib.sha256(on_disk).hexdigest() == found.sha256
            assert (await service.pointer("skycam_west")).reason == "no_preview"
        finally:
            files.close()
            await worker.dispose()
            await api.dispose()

    asyncio.run(check())
