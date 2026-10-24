# SPDX-License-Identifier: MIT
"""Weather events against the ephemeral PostgreSQL in fleet CI (#41).

The rules run as weather_worker and the feed reads as weather_api, so the grants are exercised.
Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.api.cursors import CursorCodec
from weather.api.models import EventQuery
from weather.api.service import WeatherService
from weather.api.source import WeatherReader
from weather.db.models import metadata
from weather.events import job, store
from weather.scheduler.outbox import dispatch
from weather.scheduler.runner import installed_registry
from weather.timemodel import weather_day

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]
VALID = {"valid": True, "reason": "valid", "invalid": []}
OPERATOR = {"author": "operator@example.invalid", "reason": "Reviewed against the station log"}


def engine(role: str = "postgres") -> AsyncEngine:
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or socket.name != "socket"
        or not socket.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=4, max_overflow=0)


Test = Callable[[AsyncEngine, AsyncEngine, AsyncEngine, list[UUID]], Awaitable[None]]


def database(test: Test) -> None:
    """Fresh tables, one station with two devices, and admin/worker/api engines."""

    async def run() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        try:
            async with admin.begin() as c:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
                station = (
                    await c.execute(
                        text(
                            "INSERT INTO weather.station(alias,site) VALUES('home','local')"
                            " RETURNING id"
                        )
                    )
                ).scalar_one()
                devices = [
                    (
                        await c.execute(
                            text("""INSERT INTO weather.device(station_id,alias,private_id,kind,
                                      valid_from) VALUES(:s,:alias,:private,'ST',
                                      '2020-01-01T00:00:00Z') RETURNING id"""),
                            {"s": station, "alias": f"synthetic-{n}", "private": f"SYNTHETIC-{n}"},
                        )
                    ).scalar_one()
                    for n in range(2)
                ]
            await test(admin, worker, api, devices)
        finally:
            for item in (admin, worker, api):
                await item.dispose()

    asyncio.run(run())


async def insert(
    c: AsyncConnection,
    device: UUID,
    family: str,
    at: datetime,
    values: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
) -> UUID:
    receipt = uuid4()
    await c.execute(
        text("""INSERT INTO weather.telemetry_receipt(id,received_at,raw_bytes,sha256,
                  parse_state,parser_revision,receiver_incarnation)
                VALUES(:id,:at,convert_to(:raw,'UTF8'),encode(sha256(convert_to(:raw,'UTF8')),'hex'),
                  'parsed','test','test')"""),
        {"id": receipt, "at": at, "raw": str(receipt)},
    )
    row = (
        await c.execute(
            text(f"""INSERT INTO weather.{family}(device_id,receipt_id,source_ts,row_idx,
                      "values",quality)
                    VALUES(:device,:receipt,:at,0,CAST(:values AS jsonb),CAST(:quality AS jsonb))
                    ON CONFLICT(device_id,message_type,source_ts,row_idx) DO NOTHING
                    RETURNING id"""),
            {
                "device": device,
                "receipt": receipt,
                "at": at,
                "values": json.dumps(values or {}),
                "quality": json.dumps(quality or VALID),
            },
        )
    ).scalar_one_or_none()
    return row  # type: ignore[no-any-return]


async def reconcile(worker: AsyncEngine, end: datetime, **kwargs: Any) -> dict[str, int]:
    async with worker.begin() as c:
        return await job.reconcile(c, end=end, **kwargs)


async def scalar(connection: AsyncConnection, sql: str, /, **params: Any) -> Any:
    return (await connection.execute(text(sql), params)).scalar_one()


def feed(api: AsyncEngine, now: datetime) -> WeatherService:
    return WeatherService(
        WeatherReader(api), CursorCodec(b"k" * 64), registry=CollectorRegistry(), clock=lambda: now
    )


async def rejects(c: AsyncConnection, sql: str, params: dict[str, Any], sqlstate: str) -> None:
    with pytest.raises(DBAPIError) as failure:
        async with c.begin_nested():
            await c.execute(text(sql), params)
    assert getattr(failure.value.orig, "sqlstate", None) == sqlstate


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_event_schema_grants_views_and_constraints() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, _: Any) -> None:
        async with admin.begin() as c:

            async def column(role: str, relation: str, name: str) -> bool:
                return bool(
                    await scalar(
                        c,
                        "SELECT has_column_privilege(:r,:t,:c,'SELECT')",
                        r=role,
                        t=relation,
                        c=name,
                    )
                )

            async def table(role: str, relation: str, privilege: str) -> bool:
                return bool(
                    await scalar(
                        c, "SELECT has_table_privilege(:r,:t,:p)", r=role, t=relation, p=privilege
                    )
                )

            # The worker reads only evidence columns, never receipts, serials or configuration.
            for family in ("evt_precip", "evt_strike", "obs_st"):
                assert await column("weather_worker", f"weather.{family}", "values")
                assert not await column("weather_worker", f"weather.{family}", "receipt_id")
            for family in ("rapid_wind", "telemetry_receipt", "device_status", "hub_status"):
                assert not await table("weather_worker", f"weather.{family}", "SELECT")
            assert await column("weather_worker", "weather.device", "station_id")
            assert not await column("weather_worker", "weather.device", "private_id")
            for relation in ("weather_event", "weather_event_revision", "weather_event_day"):
                name = f"weather.{relation}"
                assert await table("weather_worker", name, "INSERT")
                assert not await table("weather_worker", name, "DELETE")
                for role in ("weather_api", "weather_ingest", "weather_editor"):
                    assert not await table(role, name, "SELECT")
            assert not await table("weather_worker", "weather.weather_event_revision", "UPDATE")
            for view in ("event_revisions", "event_revision_markers", "event_days"):
                name = f"public_api.{view}"
                assert await table("weather_api", name, "SELECT")
                assert not await table("weather_api", name, "INSERT")
                for role in ("weather_worker", "weather_ingest", "weather_editor"):
                    assert not await table(role, name, "SELECT")
                assert "security_barrier=true" in await scalar(
                    c, "SELECT reloptions FROM pg_class WHERE oid=CAST(:v AS regclass)", v=name
                )
            exposed = set(
                (
                    await c.execute(
                        text("""SELECT column_name FROM information_schema.columns
                                WHERE table_schema='public_api' AND table_name='event_revisions'""")
                    )
                ).scalars()
            )
            assert not exposed & {"logical_key", "author", "reason", "visibility"}

            event = await scalar(
                c,
                "INSERT INTO weather.weather_event(logical_key,rule_family)"
                " VALUES('manual:test','manual') RETURNING id",
            )
            base = """INSERT INTO weather.weather_event_revision(event_id,revision,visibility,
              withdrawal_reason,origin,basis,starts_at,ends_at,time_precision,rule_revision,
              author_kind,author,reason,label,evidence_ids,evidence_hash)
              VALUES(:id,1,'public',NULL,:origin,:basis,:start,:end,:precision,'r',:author_kind,
              :author,:reason,'x',CAST(:evidence AS uuid[]),repeat('a',64))"""
            good = {
                "id": event,
                "origin": "measured",
                "basis": "instrument",
                "start": utc("2026-07-01T18:00:00Z"),
                "end": utc("2026-07-01T18:00:01Z"),
                "precision": "second",
                "author_kind": "rule",
                "author": None,
                "reason": None,
                "evidence": [uuid4()],
            }
            same = uuid4()
            for change, state in (
                ({"end": good["start"]}, "23514"),  # empty half-open interval
                ({"basis": "visual"}, "23514"),  # provenance
                ({"origin": "visual", "basis": "visual"}, "23514"),  # visual needs a clip
                ({"evidence": []}, "23514"),
                ({"evidence": [same, same]}, "23514"),
                ({"origin": "manual", "author_kind": "operator"}, "23514"),  # no author/reason
            ):
                await rejects(c, base, {**good, **change}, state)
            await c.execute(text(base), good)
            await rejects(c, "UPDATE weather.weather_event_revision SET label='y'", {}, "55000")
            await rejects(c, "DELETE FROM weather.weather_event_revision", {}, "55000")
            xid = await scalar(c, "SELECT recorded_xid FROM weather.weather_event_revision")
            assert xid == await scalar(
                c, "SELECT CAST(CAST(pg_current_xact_id() AS text) AS bigint)"
            )

    database(check)


def test_measured_events_retries_same_second_unknown_timing_and_outbox() -> None:
    async def check(
        admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, devices: Any
    ) -> None:
        t = utc("2026-07-01T18:00:05Z")
        async with admin.begin() as c:
            onset = await insert(c, devices[0], "evt_precip", t)
            # A duplicate receipt of the same canonical onset adds no row.
            assert await insert(c, devices[0], "evt_precip", t) is None
            future = {"valid": False, "reason": "future_timestamp", "invalid": []}
            await insert(c, devices[0], "evt_precip", t + timedelta(minutes=1), quality=future)
            # Two legitimate strikes in the same second, from two devices.
            for device in devices:
                await insert(c, device, "evt_strike", t, {"distance": 8, "energy": 12})
        end = utc("2026-07-01T19:00:00Z")
        # A bounded job defers the rest to the next slot, which finishes without repeats.
        partial = await reconcile(worker, end, max_settles=2)
        assert (partial["created"], partial["deferred"]) == (2, 1)
        first = await reconcile(worker, end)
        # Unchanged: the two settled strikes and the unknown-timing onset (no event).
        assert (first["created"], first["unchanged"], first["withdrawn"]) == (1, 3, 0)
        again = await reconcile(worker, end)
        assert again["created"] == again["revised"] == again["withdrawn"] == 0
        async with admin.begin() as c:
            assert await scalar(c, "SELECT count(*) FROM weather.weather_event_revision") == 3
            # Each revision emitted one outbox row; the installed scheduler routes the topic.
            assert await scalar(c, "SELECT count(*) FROM weather.outbox") == 3
        async with worker.begin() as c:
            assert await dispatch(c, installed_registry()) == 3
        async with admin.begin() as c:
            assert await scalar(c, "SELECT count(*) FROM weather.outbox WHERE error IS NULL") == 3
        page = await feed(api, end).events(EventQuery(day=date(2026, 7, 1)))
        kinds = sorted(item.kind for item in page.items)
        assert kinds == ["lightning_strike", "lightning_strike", "precipitation_onset"]
        precip = next(item for item in page.items if item.kind == "precipitation_onset")
        assert precip.evidence_ids == [str(onset)] and precip.precision == "second"
        assert precip.source_interval.end - precip.source_interval.start == timedelta(seconds=1)
        assert (precip.origin, precip.basis, precip.human_edited) == (
            "measured",
            "instrument",
            False,
        )
        assert [video.state for video in precip.videos] == ["unavailable"] * 3
        strikes = [item for item in page.items if item.kind == "lightning_strike"]
        assert len({item.id for item in strikes}) == 2 and all(
            item.strikes == 1 for item in strikes
        )

    database(check)


def test_gust_maximum_on_a_25_hour_day_is_revised_in_place_by_a_late_higher_gust() -> None:
    async def check(
        admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, devices: Any
    ) -> None:
        day = weather_day(date(2026, 11, 1))
        assert day.end - day.start == timedelta(hours=25)
        async with admin.begin() as c:
            for hour, gust in ((1, 0), (3, 7.5), (5, 7.5), (20, 4)):
                await insert(
                    c, devices[0], "obs_st", day.start + timedelta(hours=hour), {"wind_gust": gust}
                )
        end = day.end + job.GRACE
        assert (await reconcile(worker, end - timedelta(seconds=1)))["created"] == 0  # not closed
        assert (await reconcile(worker, end))["created"] == 1
        service = feed(api, end)
        (first,) = (await service.events(EventQuery(day=day.local_date))).items
        assert first.kind == "daily_gust_max" and first.gust_mps == 7.5
        # Equal gusts: the earlier report wins.
        assert first.source_interval.end == day.start + timedelta(hours=3)
        assert first.coverage is not None and first.coverage.expected_seconds == 25 * 3600
        async with admin.begin() as c:
            await insert(c, devices[1], "obs_st", day.start + timedelta(hours=24), {"wind_gust": 9})
        assert (await reconcile(worker, end))["revised"] == 1
        (second,) = (await service.events(EventQuery(day=day.local_date))).items
        assert (second.id, second.revision, second.gust_mps) == (first.id, 2, 9.0)
        assert second.local_dates == [day.local_date]

    database(check)


def test_cross_midnight_interval_and_one_lightning_authority() -> None:
    async def check(
        admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, devices: Any
    ) -> None:
        midnight = weather_day(date(2026, 10, 31)).end
        async with admin.begin() as c:
            await insert(
                c,
                devices[0],
                "obs_st",
                midnight + timedelta(seconds=30),
                {"lightning_strike_count": 4},
            )
            await insert(
                c, devices[0], "evt_strike", midnight + timedelta(seconds=10), {"distance": 3}
            )
        end = midnight + timedelta(hours=1)
        counts = await reconcile(worker, end, authority="interval")
        assert counts["created"] == 1
        service = feed(api, end)
        for local in (date(2026, 10, 31), date(2026, 11, 1)):
            (event,) = (await service.events(EventQuery(day=local))).items
            assert event.kind == "lightning_interval" and event.strikes == 4
            assert event.local_dates == [date(2026, 10, 31), date(2026, 11, 1)]
        # Switching the authority withdraws the interval event; both are never public together.
        counts = await reconcile(worker, end, authority="event")
        assert (counts["created"], counts["withdrawn"]) == (1, 1)
        items = (await service.events(EventQuery())).items
        assert [item.kind for item in items] == ["lightning_strike"]
        assert sum(item.strikes or 0 for item in items) == 1

    database(check)


def test_evidence_changing_mid_generation_and_withdrawal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check(
        admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, devices: Any
    ) -> None:
        day = weather_day(date(2026, 7, 1))
        async with admin.begin() as c:
            await insert(c, devices[0], "obs_st", day.start + timedelta(hours=2), {"wind_gust": 5})
            onset = await insert(c, devices[0], "evt_precip", day.start + timedelta(hours=3))
        end = day.end + job.GRACE
        real = job.current_states

        async def late(c: AsyncConnection, keys: list[str]) -> dict[str, store.Current]:
            # Evidence lands after the job prepared its candidates but before it writes.
            async with admin.begin() as other:
                await insert(
                    other, devices[0], "obs_st", day.start + timedelta(hours=4), {"wind_gust": 6}
                )
            return await real(c, keys)

        monkeypatch.setattr(job, "current_states", late)
        await reconcile(worker, end)
        monkeypatch.setattr(job, "current_states", real)
        service = feed(api, end)
        gust = next(
            item
            for item in (await service.events(EventQuery())).items
            if item.kind == "daily_gust_max"
        )
        # The write re-derived under the lock: the stale prepared maximum (5) was never published.
        assert gust.gust_mps == 6 and gust.revision == 1
        precip = next(
            item
            for item in (await service.events(EventQuery())).items
            if item.kind != "daily_gust_max"
        )
        # Invalidated evidence withdraws the public claim at once; history is retained.
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.evt_precip SET quality=CAST(:q AS jsonb) WHERE id=:id"""),
                {
                    "q": json.dumps({"valid": False, "reason": "unknown", "invalid": []}),
                    "id": onset,
                },
            )
        assert (await reconcile(worker, end))["withdrawn"] == 1
        assert await service.event(UUID(precip.id)) is None
        assert [item.kind for item in (await service.events(EventQuery())).items] == [
            "daily_gust_max"
        ]
        async with admin.begin() as c:
            assert (
                await scalar(
                    c,
                    "SELECT string_agg(visibility,',' ORDER BY revision)"
                    " FROM weather.weather_event_revision WHERE event_id=:id",
                    id=UUID(precip.id),
                )
                == "public,withdrawn"
            )

    database(check)


def test_operator_override_unchanged_and_changed_evidence_and_manual_events() -> None:
    async def check(
        admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, devices: Any
    ) -> None:
        day = weather_day(date(2026, 7, 1))
        async with admin.begin() as c:
            await insert(c, devices[0], "obs_st", day.start + timedelta(hours=2), {"wind_gust": 5})
        end = day.end + job.GRACE
        await reconcile(worker, end)
        service = feed(api, end)
        (event,) = (await service.events(EventQuery())).items
        async with worker.begin() as c:
            assert (
                await store.override(c, UUID(event.id), visibility="private", now=end, **OPERATOR)
                == 2
            )
        assert (await service.events(EventQuery())).items == []
        # A rerun on unchanged evidence keeps the human decision.
        assert (await reconcile(worker, end))["unchanged"] == 1
        async with admin.begin() as c:
            await insert(c, devices[0], "obs_st", day.start + timedelta(hours=3), {"wind_gust": 8})
        # Changed evidence invalidates the override; the human revision stays for review.
        assert (await reconcile(worker, end))["withdrawn"] == 1
        assert (await reconcile(worker, end))["withdrawn"] == 0
        async with admin.begin() as c:
            assert (
                await scalar(
                    c,
                    "SELECT string_agg(author_kind||':'||visibility||':'||"
                    "coalesce(withdrawal_reason,''),',' ORDER BY revision)"
                    " FROM weather.weather_event_revision WHERE event_id=:id",
                    id=UUID(event.id),
                )
                == "rule:public:,operator:private:,rule:withdrawn:override_stale"
            )
        async with worker.begin() as c:
            await store.override(
                c, UUID(event.id), visibility="public", label="Reviewed gust", now=end, **OPERATOR
            )
        (settled,) = (await service.events(EventQuery())).items
        assert settled.human_edited and settled.gust_mps == 8 and settled.label == "Reviewed gust"
        assert (await reconcile(worker, end))["unchanged"] == 1
        async with worker.begin() as c:
            manual, _ = await store.write_manual(
                c,
                label="Hail on the deck",
                basis="visual",
                start=day.start + timedelta(hours=6),
                end=day.start + timedelta(hours=6, minutes=5),
                camera="frontyard_1",
                now=end,
                **OPERATOR,
            )
        items = (await service.events(EventQuery(origin="manual"))).items
        assert [(item.id, item.origin, item.basis, item.human_edited) for item in items] == [
            (str(manual), "manual", "visual", True)
        ]
        assert (await service.events(EventQuery(camera="skycam_east", origin="manual"))).items == []
        with pytest.raises(ValueError):
            async with worker.begin() as c:
                await store.override(c, manual, visibility="public", author="", reason="x")

    database(check)


def test_feed_pages_never_repeat_or_drop_under_concurrent_inserts_and_revisions() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, _: Any) -> None:
        start = utc("2026-07-01T16:00:00Z")
        now = utc("2026-07-02T12:00:00Z")
        ids: list[UUID] = []
        async with worker.begin() as c:
            for minute in range(20):
                event_id, _ = await store.write_manual(
                    c,
                    label=f"Note {minute}",
                    basis="visual",
                    start=start + timedelta(minutes=minute),
                    end=start + timedelta(minutes=minute, seconds=30),
                    now=now,
                    **OPERATOR,
                )
                ids.append(event_id)

        async def manual(event_id: UUID | None, minute: int) -> UUID:
            async with worker.begin() as c:
                written, _ = await store.write_manual(
                    c,
                    label="Moved",
                    basis="visual",
                    start=start + timedelta(minutes=minute),
                    end=start + timedelta(minutes=minute, seconds=30),
                    event_id=event_id,
                    now=now,
                    **OPERATOR,
                )
                return written

        service = feed(api, now)
        # A writer that is in flight when the first page takes its snapshot.
        async with worker.connect() as pending:
            await pending.begin()
            hidden, _ = await store.write_manual(
                pending,
                label="In flight",
                basis="visual",
                start=start + timedelta(minutes=30),
                end=start + timedelta(minutes=31),
                now=now,
                **OPERATOR,
            )
            query = EventQuery(limit=5)
            page = await service.events(query)
            await pending.commit()
        seen = [item.id for item in page.items]
        # Concurrent changes after the snapshot: move an unseen event behind the cursor, move a
        # seen one ahead of it, add a new event ahead, withdraw an unseen one.
        await manual(ids[15], -10)
        await manual(ids[1], 50)
        await manual(None, 40)
        async with worker.begin() as c:
            await store.override(c, ids[18], visibility="withdrawn", now=now, **OPERATOR)
        while page.next_cursor:
            page = await service.events(query.model_copy(update={"cursor": page.next_cursor}))
            seen += [item.id for item in page.items]
        assert seen == [str(item) for item in ids]
        # A new feed sees the new state, including the writer that was in flight.
        fresh = []
        page = await service.events(EventQuery(limit=100))
        fresh = [item.id for item in page.items]
        assert str(hidden) in fresh and str(ids[18]) not in fresh
        assert fresh.index(str(ids[15])) == 0 and len(fresh) == len(set(fresh)) == 21
        descending = await service.events(EventQuery(limit=100, order="desc"))
        assert [item.id for item in descending.items] == fresh[::-1]

    database(check)
