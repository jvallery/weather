# SPDX-License-Identifier: MIT
"""The private editor (#44) against the ephemeral PostgreSQL 16.15 in fleet CI.

Editorial writes run as weather_editor, the analyzer and the outbox as weather_worker and the
public reads as weather_api, so every grant is exercised. Cortex and media are #23's fakes.
Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import base64
import hashlib
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from ai_support import Script, StaticWeather, by_task, station
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from test_ai_integration import (
    END,
    PROFILE,
    SEED_PROFILE,
    START,
    claim,
    engine,
    make,
    publication,
    seed,
    sha,
)
from weather.ai import store as analysis
from weather.api.timelapses import CatalogQuery, DatabaseSource, build
from weather.db.models import metadata
from weather.editorial import receipts, store
from weather.editorial.models import ManualEvent, ReceiptQuery, SubjectPatch
from weather.scheduler.outbox import dispatch
from weather.scheduler.registry import JobContext
from weather.scheduler.runner import installed_registry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]
OPERATOR = "jason"
Test = Callable[[AsyncEngine, AsyncEngine, AsyncEngine, AsyncEngine], Awaitable[None]]


def database(test: Test) -> None:
    async def run() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        editor = engine("weather_editor")
        try:
            async with admin.begin() as c:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
                await c.execute(text(SEED_PROFILE), {"id": PROFILE})
                # Other suites truncate the seeded allowlist; restore 0012's row.
                await c.execute(
                    text("""INSERT INTO weather.editorial_operator(username,application)
                    VALUES('jason','weather-editor')""")
                )
            await test(admin, worker, api, editor)
        finally:
            for item in (admin, worker, api, editor):
                await item.dispose()

    asyncio.run(run())


def change(expected: int, reason: str = "Checked against the footage", **changes: Any) -> Any:
    return SubjectPatch.model_validate(
        {"expected_revision": expected, "reason": reason, "changes": changes}
    )


async def edit(editor: AsyncEngine, subject: UUID, body: SubjectPatch) -> Any:
    async with editor.begin() as c:
        return await store.patch(
            c, subject, body, actor=OPERATOR, profile_id=PROFILE, now=datetime.now(UTC)
        )


async def read(editor: AsyncEngine, subject: UUID) -> Any:
    async with editor.connect() as c:
        return await store.read(c, subject)


async def scalar(engine_: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine_.connect() as c:
        return (await c.execute(text(sql), params)).scalar_one()


async def public(api: AsyncEngine, video: UUID) -> Any:
    """The Timelapse the public API would serve, read as weather_api."""
    source = DatabaseSource(api)
    rows = await source.videos(CatalogQuery(limit=1), None, video)
    assets = await source.assets(
        [rows[0][k] for k in rows[0] if k.endswith("_asset_id") and rows[0][k] is not None]
    )
    return build(
        rows[0],
        assets,
        (await source.analyses([video])).get(video),
        (await source.editorials([video])).get(video),
    )


async def searchable(api: AsyncEngine, video: UUID, words: str) -> bool:
    return bool(
        await scalar(
            api,
            """SELECT EXISTS (SELECT 1 FROM public_api.search_documents
            WHERE video_id=:v AND lexical @@ plainto_tsquery('english', :q))""",
            v=video,
            q=words,
        )
    )


async def described(worker: AsyncEngine, admin: AsyncEngine, weather: StaticWeather) -> None:
    await make(worker, Script(by_task()), weather).describe(
        await claim(worker, analysis.DESCRIBE_KIND, admin)
    )


def test_editor_role_grants_and_the_public_view_are_least_privilege() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, _: Any) -> None:
        async def privilege(role: str, relation: str, kind: str) -> bool:
            return bool(
                await scalar(
                    admin, "SELECT has_table_privilege(:r,:t,:p)", r=role, t=relation, p=kind
                )
            )

        editorial = ("editorial_subject", "editorial_revision", "editorial_audit")
        for name in editorial:
            assert await privilege("weather_editor", f"weather.{name}", "INSERT")
            for kind in ("DELETE", "TRUNCATE"):
                assert not await privilege("weather_editor", f"weather.{name}", kind)
            for role in ("weather_api", "weather_worker", "weather_ingest"):
                assert not await privilege(role, f"weather.{name}", "SELECT")
        assert not await privilege("weather_editor", "weather.editorial_operator", "INSERT")
        assert not await privilege("weather_editor", "weather.editorial_revision", "UPDATE")
        # No telemetry or catalog writes, and no DDL.
        for relation in (
            "weather.telemetry_receipt",
            "weather.obs_st",
            "weather.video",
            "weather.publication",
            "weather.media_asset",
            "weather.publication_asset",
            "weather.analysis",
        ):
            for kind in ("INSERT", "UPDATE", "DELETE"):
                assert not await privilege("weather_editor", relation, kind)
        assert not await scalar(
            admin, "SELECT has_schema_privilege('weather_editor','weather','CREATE')"
        )
        assert not await scalar(
            admin,
            "SELECT has_column_privilege('weather_editor','weather.telemetry_receipt',"
            "'receiver_incarnation','SELECT')",
        )
        # weather_api: no editorial access and no EXECUTE on any weather-schema function.
        assert not await scalar(
            admin, "SELECT has_schema_privilege('weather_api','weather','USAGE')"
        )
        assert not await scalar(
            admin,
            """SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='weather' AND has_function_privilege('weather_api',p.oid,'EXECUTE'))""",
        )
        assert await privilege("weather_api", "public_api.video_editorial", "SELECT")
        assert not await privilege("weather_editor", "public_api.video_editorial", "SELECT")
        columns = await scalar(
            admin,
            """SELECT array_agg(attname::text ORDER BY attnum) FROM pg_attribute
            WHERE attrelid='public_api.video_editorial'::regclass AND attnum > 0""",
        )
        assert columns == ["video_id", "revision", "description", "human_edited", "tags"]
        assert "security_barrier=true" in await scalar(
            admin,
            "SELECT reloptions FROM pg_class WHERE oid='public_api.video_editorial'::regclass",
        )

    database(body)


def test_concurrent_patches_with_one_expected_revision_one_wins_with_no_side_effects() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        video, _ = await seed(admin)
        second = engine("weather_editor")
        try:
            for expected in (0, 1):
                async with editor.connect() as a, second.connect() as b:
                    first = await a.begin()
                    await store.patch(
                        a,
                        video,
                        change(expected, favorite=bool(expected)),
                        actor=OPERATOR,
                        profile_id=PROFILE,
                        now=datetime.now(UTC),
                    )

                    async def racer(b: AsyncConnection = b, expected: int = expected) -> Any:
                        async with b.begin():
                            return await store.patch(
                                b,
                                video,
                                change(expected, note="the loser"),
                                actor=OPERATOR,
                                profile_id=PROFILE,
                                now=datetime.now(UTC),
                            )

                    loser = asyncio.create_task(racer())
                    await asyncio.sleep(0.5)
                    assert not loser.done()  # waiting on the head's lock or unique key
                    await first.commit()
                    with pytest.raises(store.Conflict) as conflict:
                        await loser
                    assert conflict.value.current == expected + 1
        finally:
            await second.dispose()
        assert await scalar(admin, "SELECT revision FROM weather.editorial_subject") == 2
        for table in ("editorial_revision", "editorial_audit"):
            assert await scalar(admin, f"SELECT count(*) FROM weather.{table}") == 2
        assert (
            await scalar(
                admin, "SELECT count(*) FROM weather.outbox WHERE topic='editorial.changed'"
            )
            == 2
        )
        assert (
            await scalar(
                admin, "SELECT count(*) FROM weather.editorial_revision WHERE note IS NOT NULL"
            )
            == 0
        )

    database(body)


def test_human_description_survives_reruns_withdraws_on_evidence_change_and_rebases() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        video, publication_id = await seed(admin)
        async with worker.begin() as c:
            await analysis.on_published(c, publication_id, datetime.now(UTC))
        weather = StaticWeather(station(START, END))
        await described(worker, admin, weather)
        automated = (await public(api, video)).editorial
        assert automated.human_edited is False and automated.description.startswith("Broken")
        assert await searchable(api, video, "cumulus")

        # A correction reaches the API and search in the same transaction.
        text_ = "Thin cirrus all afternoon; the low cloud was smoke, not cumulus."
        edited = await edit(
            editor,
            video,
            change(
                0,
                description=text_,
                tags=[
                    {"tag": "smoke", "visibility": "public"},
                    {"tag": "check later", "visibility": "private"},
                ],
                favorite=True,
                note="Private: compare with the west camera.",
            ),
        )
        assert edited.revision == 1 and edited.description.active
        served = await public(api, video)
        assert served.editorial.description == text_ and served.editorial.human_edited
        assert served.editorial.tags == ["smoke"] and served.analysis.author == "editor"
        assert served.analysis.description == text_ and served.analysis.claims == []
        assert await searchable(api, video, "cirrus smoke")
        assert not await searchable(api, video, "drifted")  # the old AI text is unsearchable
        documents = await scalar(
            admin,
            "SELECT string_agg(text, ' ') FROM weather.search_document WHERE video_id=:v",
            v=video,
        )
        assert "compare with the west camera" not in documents and "check later" not in documents

        # An AI rerun on unchanged media and evidence keeps the human description active.
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.analysis SET state='pending',pending_since=now(),
                  next_attempt_at=NULL WHERE video_id=:v"""),
                {"v": video},
            )
            await analysis.enqueue_describe(
                c, video, publication_id, tag="rerun", now=datetime.now(UTC)
            )
        await described(worker, admin, weather)
        assert await scalar(admin, "SELECT revision FROM weather.analysis") == 2
        assert (await public(api, video)).editorial.description == text_
        assert await searchable(api, video, "cirrus") and not await searchable(
            api, video, "drifted"
        )

        # Changed evidence withdraws the human text from API and search but keeps the revision.
        async with admin.begin() as c:
            await c.execute(text("UPDATE weather.analysis SET evidence_checked_at=NULL"))
        weather.items = station(START, END, humidity_mean=71.0)
        analyzer = make(worker, Script(by_task()), weather)
        assert await analyzer.check_evidence(datetime.now(UTC)) == 1
        await analyzer.synthesize(await claim(worker, analysis.SYNTHESIZE_KIND, admin))
        served = await public(api, video)
        assert served.editorial.human_edited is False and served.editorial.description != text_
        assert not await searchable(api, video, "cirrus")
        stale = await read(editor, video)
        assert stale.description.stale and stale.description.human == text_
        assert stale.description.bound.evidence_hash != stale.description.current.evidence_hash
        assert stale.description.bound.media_sha256 == stale.description.current.media_sha256
        with pytest.raises(store.RebaseRequired):
            await edit(editor, video, change(1, description=text_ + " Edited."))
        rebased = await edit(editor, video, change(1, rebase=True))
        assert rebased.description.active and rebased.audit[0].action == "rebase"
        assert await searchable(api, video, "cirrus")
        with pytest.raises(store.Invalid):
            await edit(editor, video, change(2, rebase=True))  # nothing is stale now

        # null clears the override, falling back only to the eligible validated AI text.
        cleared = await edit(editor, video, change(2, description=None))
        assert cleared.description.human is None
        served = await public(api, video)
        assert served.editorial.human_edited is False
        assert served.editorial.description == served.analysis.description is not None
        assert served.analysis.author == "ai"
        assert not await searchable(api, video, "cirrus")
        # Favorites, private tags and notes survived every edit and never went public.
        assert cleared.favorite is True and cleared.note.startswith("Private")
        assert {(t.tag, t.visibility) for t in cleared.tags} == {
            ("smoke", "public"),
            ("check later", "private"),
        }

    database(body)


def test_new_media_withdraws_dependent_search_text_but_keeps_the_human_revision() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        video, publication_id = await seed(admin)
        async with worker.begin() as c:
            await analysis.on_published(c, publication_id, datetime.now(UTC))
        await described(worker, admin, StaticWeather(station(START, END)))
        await edit(editor, video, change(0, description="Fog lifted by noon."))
        assert await searchable(api, video, "fog")
        times = {"start": START, "end": END, "embargo": END + timedelta(seconds=630)}
        async with admin.begin() as c:
            roles = {
                "native": ("video", sha("v2n"), "mp4", "video/mp4"),
                "web": ("video", sha("v2"), "mp4", "video/mp4"),
                "mobile": ("video", sha("v2m"), "mp4", "video/mp4"),
                "poster": ("poster", sha("v2p"), "jpg", "image/jpeg"),
            }
            key = f"{START.date().isoformat()}/daytime"
            newer = await publication(c, "video", key, roles, revision=2, **times)
            await c.execute(
                text("UPDATE weather.video SET publication_id=:p,revision=2 WHERE id=:v"),
                {"p": newer, "v": video},
            )
        async with worker.begin() as c:
            assert await analysis.on_published(c, newer, datetime.now(UTC))
        assert not await searchable(api, video, "fog")
        served = await public(api, video)
        assert served.editorial.human_edited is False and served.editorial.description is None
        kept = await read(editor, video)
        assert kept.description.human == "Fog lifted by noon." and kept.description.stale
        assert kept.description.bound.media_sha256 == sha("web-1")
        assert kept.description.current.media_sha256 == sha("v2")

    database(body)


def test_every_edit_is_audited_in_its_transaction_and_audit_is_append_only() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        video, publication_id = await seed(admin)
        async with worker.begin() as c:
            await analysis.on_published(c, publication_id, datetime.now(UTC))
        await described(worker, admin, StaticWeather(station(START, END)))
        await edit(editor, video, change(0, "First pass", description="Clear and calm."))
        await edit(editor, video, change(1, "Revert", description=None))
        async with admin.connect() as c:
            rows = (
                (
                    await c.execute(
                        text("SELECT * FROM weather.editorial_audit ORDER BY new_revision")
                    )
                )
                .mappings()
                .all()
            )
        assert [(r["old_revision"], r["new_revision"]) for r in rows] == [(0, 1), (1, 2)]
        first = rows[0]
        assert first["actor"] == OPERATOR and first["reason"] == "First pass"
        assert first["action"] == "edit" and first["recorded_at"] is not None
        assert first["input"] == {"description": "Clear and calm."}
        assert first["media_sha256"] == sha("web-1") and len(first["evidence_hash"]) == 64
        assert rows[1]["input"] == {"description": None} and rows[1]["media_sha256"] is None
        # Audit and revisions are immutable, even for the database owner's sessions.
        for statement in (
            "UPDATE weather.editorial_audit SET reason='rewritten'",
            "DELETE FROM weather.editorial_audit",
            "UPDATE weather.editorial_revision SET favorite=true",
        ):
            with pytest.raises(DBAPIError):
                async with admin.begin() as c:
                    await c.execute(text(statement))
        # A refused write leaves nothing behind: the CAS fails before any side effect.
        with pytest.raises(store.Conflict):
            await edit(editor, video, change(1, "stale", favorite=True))
        assert await scalar(admin, "SELECT count(*) FROM weather.editorial_audit") == 2

    database(body)


def test_event_corrections_withdrawals_and_manual_events_go_through_the_hooks() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        evidence = UUID(int=7)
        manual = ManualEvent.model_validate(
            {
                "reason": "Hail on the deck",
                "label": "Small hail",
                "basis": "visual",
                "start": "2026-07-01T20:00:00Z",
                "end": "2026-07-01T20:10:00Z",
                "camera": "frontyard_1",
                "evidence_ids": [str(evidence)],
            }
        )
        async with editor.begin() as c:
            created = await store.create_event(c, manual, actor=OPERATOR, now=datetime.now(UTC))
        event = created.subject_id
        assert created.kind == "event" and created.revision == 1
        assert created.event.origin == "manual" and created.event.current.author_kind == "operator"
        assert created.audit[0].action == "manual_event"
        assert await scalar(
            admin, "SELECT evidence_ids FROM weather.editorial_audit WHERE subject_id=:s", s=event
        ) == [evidence]

        async def visible() -> Any:
            return await scalar(
                api,
                "SELECT count(*) FROM public_api.event_revisions WHERE id=:e "
                "AND revision=(SELECT max(revision) FROM public_api.event_revision_markers "
                "WHERE id=:e)",
                e=event,
            )

        assert await visible() == 1
        relabelled = await edit(editor, event, change(1, event={"label": "Pea-sized hail"}))
        assert relabelled.event.current.label == "Pea-sized hail"
        assert relabelled.event.current.visibility == "public"
        withdrawn = await edit(editor, event, change(2, event={"visibility": "withdrawn"}))
        assert withdrawn.event.current.withdrawal_reason == "operator"
        assert await visible() == 0
        async with admin.connect() as c:
            author, reason = (
                await c.execute(
                    text("""SELECT author,reason FROM weather.weather_event_revision
                    WHERE event_id=:e ORDER BY revision DESC LIMIT 1"""),
                    {"e": event},
                )
            ).one()
        assert author == OPERATOR and reason == "Checked against the footage"
        with pytest.raises(store.Invalid):
            await edit(editor, event, change(3, description="Not for events"))

    database(body)


def test_private_receipts_as_the_editor_hash_and_page_exactly() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        at = datetime(2026, 7, 1, tzinfo=UTC)
        async with admin.begin() as c:
            for n in range(7):
                raw = f'{{"type":"rapid_wind","serial_number":"SYNTHETIC","n":{n}}}'.encode()
                await c.execute(
                    text("""INSERT INTO weather.telemetry_receipt(received_at,raw_bytes,sha256,
                      parse_state,parser_revision,receiver_incarnation)
                    VALUES(:at,:raw,:sha,'parsed','test','test')"""),
                    {
                        "at": at + timedelta(seconds=n),
                        "raw": raw,
                        "sha": hashlib.sha256(raw).hexdigest(),
                    },
                )
            # Rows inserted after the export's first page never shift it.
            await c.execute(
                text("UPDATE weather.telemetry_receipt SET inserted_at=now() - interval '1 hour'")
            )
        query = ReceiptQuery(start=at, end=at + timedelta(hours=1), limit=3)
        seen = []
        async with editor.connect() as c:
            page = await receipts.page(c, b"k" * 32, query)
            while True:
                seen.extend(page.items)
                if page.next_cursor is None:
                    break
                page = await receipts.page(
                    c, b"k" * 32, query.model_copy(update={"cursor": page.next_cursor})
                )
        assert len(seen) == len({r.id for r in seen}) == 7
        for item in seen:
            assert hashlib.sha256(base64.b64decode(item.bytes_base64)).hexdigest() == item.sha256
            assert item.family == "rapid_wind"

    database(body)


def test_editorial_changed_is_routed_and_its_handler_reprojects() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine, editor: Any) -> None:
        video, publication_id = await seed(admin)
        async with worker.begin() as c:
            await analysis.on_published(c, publication_id, datetime.now(UTC))
        await described(worker, admin, StaticWeather(station(START, END)))
        await edit(editor, video, change(0, description="Dust on the lens all day."))
        async with worker.begin() as c:
            await dispatch(c, installed_registry())
        assert (
            await scalar(
                admin,
                "SELECT count(*) FROM weather.outbox WHERE topic='editorial.changed' "
                "AND job_id IS NOT NULL AND error IS NULL",
            )
            == 1
        )
        context: JobContext = await claim(worker, "editorial.changed", admin)
        await installed_registry().handler("editorial.changed")(context)
        assert (
            await scalar(admin, "SELECT status FROM weather.job WHERE kind='editorial.changed'")
            == "succeeded"
        )
        assert await searchable(api, video, "dust lens")

    database(body)
