# SPDX-License-Identifier: MIT
"""UNRUN PostgreSQL evidence-history proofs; fleet-only disposable socket fixture.

Collection and source lint are allowed on the Mac; executing any test here is not.
"""

import asyncio
import contextlib
import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from test_current_publication_integration import (
    SOURCE,
    device,
    engine,
    guard,
    incident,
    observation,
    quality,
    rejects,
    waits_on_lock,
    watermark,
)
from test_current_publication_integration import disposable_database as disposable_database
from weather.db.ddl_v4 import backfill_statement, lock_statements

pytestmark = pytest.mark.integration

AS_OF = """SELECT DISTINCT ON (id,message_type) *
FROM public_api.telemetry_evidence_revisions
WHERE revision_seq<=:watermark ORDER BY id,message_type,revision_seq DESC"""


@pytest.fixture(autouse=True)
def evidence_database(disposable_database: None) -> None:
    async def clean() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                await guard(connection)
                assert (
                    await connection.execute(
                        text(
                            "SELECT to_regclass('weather.telemetry_evidence_revision') IS NOT NULL "
                            "AND to_regclass('public_api.telemetry_evidence_revisions') IS NOT NULL"
                        )
                    )
                ).scalar_one()
                # The predecessor fixture truncates publication CASCADE, which
                # clears this FK-linked history. Outbox is independently scoped.
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.telemetry_evidence_revision")
                    )
                ).scalar_one() == 0
                await connection.execute(text("TRUNCATE weather.outbox"))
        finally:
            await db.dispose()

    asyncio.run(clean())


async def history(connection: AsyncConnection, boundary: int) -> list[dict[str, Any]]:
    rows = await connection.execute(text(AS_OF), {"watermark": boundary})
    return [dict(row) for row in rows.mappings()]


async def changed(connection: AsyncConnection) -> list[dict[str, Any]]:
    rows = await connection.execute(
        text(
            "SELECT idempotency_key,payload,not_before,deadline FROM weather.outbox "
            "WHERE topic='telemetry.changed' ORDER BY (payload->>'watermark')::bigint"
        )
    )
    return [dict(row) for row in rows.mappings()]


def test_initial_publication_reuses_sequence_and_outbox_exposes_only_safe_fields() -> None:
    async def check() -> None:
        db, api = engine(), engine("weather_api")
        families = (
            ("obs_st", {"air_temperature": 20}),
            ("rapid_wind", {"wind_speed": 3}),
            ("evt_precip", {}),
            ("evt_strike", {}),
            ("obs_air", {"air_temperature": 21}),
            ("obs_sky", {"wind_avg": 2}),
        )
        try:
            async with db.begin() as connection:
                d = await device(connection)
                for index, (family, values) in enumerate(families):
                    await observation(
                        connection,
                        d,
                        family=family,
                        values=values,
                        source=SOURCE + timedelta(seconds=index),
                    )
                await observation(connection, d, family="device_status", values={})
                await observation(connection, d, family="hub_status", values={})
            async with api.begin() as connection:
                boundary = await watermark(connection)
                rows = await history(connection, boundary)
                published = {
                    row["id"]: dict(row)
                    for row in (
                        await connection.execute(
                            text("SELECT * FROM public_api.telemetry_publications")
                        )
                    ).mappings()
                }
            assert boundary == len(rows) == 6
            assert all(
                published[row["id"]]
                == {key: value for key, value in row.items() if key != "revision_seq"}
                for row in rows
            )
            assert {row["message_type"] for row in rows} == {name for name, _ in families}
            assert all(row["revision_seq"] == row["publication_seq"] for row in rows)
            assert "fixture-private" not in json.dumps(rows, default=str)
            assert "private_extra" not in json.dumps(rows, default=str)
            async with db.begin() as connection:
                events = await changed(connection)
            assert len(events) == 6
            identities = {str(row["id"]): row for row in rows}
            for event in events:
                payload = event["payload"]
                assert set(payload) == {"id", "family", "station", "sourceTime", "watermark"}
                row = identities[payload["id"]]
                assert payload["family"] == row["message_type"]
                assert payload["station"] == row["station_alias"]
                assert datetime.fromisoformat(payload["sourceTime"]) == row["observed_at"]
                assert payload["watermark"] == row["revision_seq"]
                assert event["idempotency_key"] == f"telemetry-evidence:{row['revision_seq']}"
                assert event["deadline"] - event["not_before"] == timedelta(days=1)
        finally:
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_same_watermark_freezes_values_and_quality_after_effective_requalification() -> None:
    async def check() -> None:
        db, api, ingest = engine(), engine("weather_api"), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, raw = await observation(connection, d)
                conflict = await incident(connection, original, raw)
            async with api.begin() as connection:
                old_boundary = await watermark(connection)
                old = await history(connection, old_boundary)
            async with ingest.begin() as connection:
                await connection.execute(
                    text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                )
            async with api.begin() as connection:
                new_boundary = await watermark(connection)
                assert await history(connection, old_boundary) == old
                updated = await history(connection, new_boundary)
                legacy = (
                    await connection.execute(
                        text('SELECT "values" FROM public_api.telemetry_publications WHERE id=:id'),
                        {"id": original},
                    )
                ).scalar_one()
            assert new_boundary == old_boundary + 1
            assert old[0]["values"]["air_temperature"] == 20
            assert updated[0]["values"]["air_temperature"] is None
            assert updated[0]["field_quality"]["air_temperature"]["reason"] == "payload_conflict"
            assert not updated[0]["field_quality"]["air_temperature"]["eligible"]
            assert legacy["air_temperature"] is None  # Raw membership keeps current eligibility.
            assert old[0]["publication_seq"] == updated[0]["publication_seq"]
            async with db.begin() as connection:
                events = await changed(connection)
                assert len(events) == 2
                assert events[-1]["payload"]["watermark"] == new_boundary
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.telemetry_evidence_revision")
                    )
                ).scalar_one() == 2
                assert (
                    await connection.execute(
                        text("SELECT count(*) FROM weather.outbox WHERE topic='telemetry.conflict'")
                    )
                ).scalar_one() == 1
        finally:
            await ingest.dispose()
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_private_quality_change_and_idempotent_replay_do_not_publish_revisions() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, raw = await observation(connection, d)
                before = await watermark(connection)
                baseline = await history(connection, before)
                await connection.execute(
                    text(
                        "UPDATE weather.obs_st SET quality=jsonb_set(quality,'{private_extra}',"
                        "to_jsonb(CAST(:private AS text))) WHERE id=:id"
                    ),
                    {"id": original, "private": "fixture-private-never-serialized"},
                )
                await observation(connection, d, identity=original, receipt_id=raw)
                assert await watermark(connection) == before
                assert await history(connection, before) == baseline
                assert len(await changed(connection)) == 1
        finally:
            await db.dispose()

    asyncio.run(check())


def test_public_history_is_immutable_and_only_safe_view_is_granted_to_readers() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                await observation(connection, d)
                await rejects(
                    connection,
                    "UPDATE weather.telemetry_evidence_revision SET quality=quality",
                    {},
                    "55000",
                )
                await rejects(
                    connection, "DELETE FROM weather.telemetry_evidence_revision", {}, "55000"
                )
                options = (
                    await connection.execute(
                        text(
                            "SELECT reloptions FROM pg_class WHERE oid="
                            "'public_api.telemetry_evidence_revisions'::regclass"
                        )
                    )
                ).scalar_one()
                assert "security_barrier=true" in options
                for role in ("weather_api", "weather_worker", "weather_ingest", "weather_editor"):
                    assert not (
                        await connection.execute(
                            text(
                                "SELECT has_table_privilege(CAST(:role AS name),"
                                "'weather.telemetry_evidence_revision',"
                                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
                            ),
                            {"role": role},
                        )
                    ).scalar_one()
            for role in ("weather_api", "weather_worker", "weather_ingest", "weather_editor"):
                peer = engine(role)
                try:
                    async with peer.begin() as connection:
                        if role in ("weather_api", "weather_worker"):
                            assert len(await history(connection, 1)) == 1
                        else:
                            await rejects(
                                connection,
                                "SELECT * FROM public_api.telemetry_evidence_revisions",
                                {},
                                "42501",
                            )
                        await rejects(
                            connection,
                            "SELECT * FROM weather.telemetry_evidence_revision",
                            {},
                            "42501",
                        )
                        await rejects(
                            connection,
                            "UPDATE public_api.telemetry_evidence_revisions SET quality=quality",
                            {},
                            "42501",
                        )
                        await rejects(
                            connection, "SELECT weather.append_telemetry_evidence(1,1)", {}, "42501"
                        )
                finally:
                    await peer.dispose()
        finally:
            await db.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("commit_quality", [True, False])
def test_quality_and_new_publication_share_commit_serialization(commit_quality: bool) -> None:
    async def check() -> None:
        db, api, ingest = engine(), engine("weather_api"), engine("weather_ingest")
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, raw = await observation(connection, d)
                conflict = await incident(connection, original, raw)
            async with ingest.connect() as first, ingest.connect() as second, db.connect() as admin:
                tx1, tx2 = await first.begin(), await second.begin()
                pending = None
                try:
                    await first.execute(
                        text("SELECT weather.qualify_telemetry_conflict(:id)"), {"id": conflict}
                    )
                    pid = int((await second.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    pending = asyncio.create_task(
                        observation(second, d, source=SOURCE + timedelta(minutes=1))
                    )
                    await waits_on_lock(admin, pid)
                    async with api.begin() as reader:
                        assert await asyncio.wait_for(watermark(reader), timeout=2) == 1
                        rows = await history(reader, 1)
                        assert rows[0]["values"]["air_temperature"] == 20
                    if commit_quality:
                        await tx1.commit()
                    else:
                        await tx1.rollback()
                    later, _ = await asyncio.wait_for(pending, timeout=5)
                    await tx2.commit()
                finally:
                    if pending is not None and not pending.done():
                        pending.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pending
                    if tx1.is_active:
                        await tx1.rollback()
                    if tx2.is_active:
                        await tx2.rollback()
            async with db.begin() as connection:
                boundary = await watermark(connection)
                assert boundary == (3 if commit_quality else 2)
                revisions = (
                    (
                        await connection.execute(
                            text(
                                "SELECT revision_seq FROM weather.telemetry_evidence_revision "
                                "ORDER BY revision_seq"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert revisions == list(range(1, boundary + 1))
                rows = {row["id"]: row for row in await history(connection, boundary)}
                assert rows[later]["revision_seq"] == boundary
                assert (rows[original]["values"]["air_temperature"] is None) == commit_quality
                assert len(await changed(connection)) == boundary
        finally:
            await ingest.dispose()
            await api.dispose()
            await db.dispose()

    asyncio.run(check())


def test_backfill_retains_original_positions_and_emits_no_new_outbox_events() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                await observation(connection, d)
                await observation(
                    connection, d, source=SOURCE + timedelta(minutes=1), source_eligible=False
                )
                before = await watermark(connection)
                events = await changed(connection)
                await connection.execute(text("TRUNCATE weather.telemetry_evidence_revision"))
                for sql in lock_statements():
                    await connection.execute(text(sql))
                await connection.execute(text(backfill_statement()))
                assert await watermark(connection) == before
                assert await changed(connection) == events
                rows = await history(connection, before)
                assert len(rows) == 2
                assert all(row["revision_seq"] == row["publication_seq"] for row in rows)
        finally:
            await db.dispose()

    asyncio.run(check())


def test_outbox_insert_failure_rolls_back_quality_revision_and_clock() -> None:
    async def check() -> None:
        db = engine()
        try:
            async with db.begin() as connection:
                d = await device(connection)
                original, _ = await observation(connection, d)
                before = await history(connection, 1)
                await connection.execute(
                    text(
                        "INSERT INTO weather.outbox"
                        "(topic,idempotency_key,payload,not_before,deadline) "
                        "VALUES('telemetry.changed','telemetry-evidence:2','{}'::jsonb,"
                        "clock_timestamp(),clock_timestamp()+interval '1 day')"
                    )
                )
                q = quality({"air_temperature": 20, "relative_humidity": 50})
                q["fields"]["air_temperature"].update(
                    state="invalid", valid=False, eligible=False, reason="out_of_range"
                )
                await rejects(
                    connection,
                    "UPDATE weather.obs_st SET quality=CAST(:q AS jsonb) WHERE id=:id",
                    {"q": json.dumps(q), "id": original},
                    "23505",
                )
                assert await watermark(connection) == 1
                assert await history(connection, 10) == before
                current = (
                    await connection.execute(
                        text('SELECT "values" FROM public_api.telemetry_publications WHERE id=:id'),
                        {"id": original},
                    )
                ).scalar_one()
                assert current["air_temperature"] == 20
        finally:
            await db.dispose()

    asyncio.run(check())
