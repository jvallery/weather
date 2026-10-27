# SPDX-License-Identifier: MIT
"""AI descriptions (#23) against the ephemeral PostgreSQL 16.15 in fleet CI.

The analyzer runs as weather_worker and the API reads as weather_api, so grants are exercised.
Cortex, Garage and FFmpeg are fakes (ai_support); replies are recorded-format fixtures.
Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from ai_support import CountingSlot, Script, StaticMedia, StaticWeather, by_task, station
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from weather.ai import store
from weather.ai.analyze import MAX_CALLS_PER_RUN, Analyzer, Run
from weather.ai.slot import CortexSlot
from weather.api.timelapses import DatabaseSource, build_analysis
from weather.db import jobs
from weather.db.models import metadata
from weather.scheduler.registry import JobContext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]
PROFILE = "embed-main-2560-v1"
SEED_PROFILE = """INSERT INTO weather.embedding_profile(id,model,dimensions,query_prefix)
VALUES(:id,'embed.main',2560,'Query:') ON CONFLICT DO NOTHING"""


def engine(role: str = "postgres", pool_size: int = 4) -> AsyncEngine:
    url = make_url(os.environ["WEATHER_CI_ADMIN_DATABASE_URL"]).set(username=role)
    socket = Path(str(url.query.get("host", "")))
    if (
        url.database != "weather_ci"
        or socket.name != "socket"
        or not socket.parent.name.startswith("weather-schema-ci.")
    ):
        pytest.fail("Integration requires its disposable socket-only database")
    return create_async_engine(url, echo=False, pool_size=pool_size, max_overflow=0)


Test = Callable[[AsyncEngine, AsyncEngine, AsyncEngine], Awaitable[None]]


def database(test: Test) -> None:
    async def run() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        try:
            async with admin.begin() as c:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
                await c.execute(text(SEED_PROFILE), {"id": PROFILE})
            await test(admin, worker, api)
        finally:
            for item in (admin, worker, api):
                await item.dispose()

    asyncio.run(run())


ASSET_SQL = """INSERT INTO weather.media_asset(class,bucket,visibility,sha256,extension,object_key,
  bytes,content_type,width,height,public_not_before,published_at)
VALUES(:class,'weather-media','public',:sha,:ext,:key,1000,:mime,320,180,:embargo,now())
RETURNING id"""
INTENT_SQL = """INSERT INTO weather.upload_intent(fence,product,camera_id,product_key,
  expected_revision,manifest,state,deadline,resolved_at)
VALUES(nextval('weather.publication_fence'),:product,:camera,:key,0,'{}'::jsonb,'committed',
  now() + interval '1 day',now()) RETURNING id,fence"""
PUBLICATION_SQL = """INSERT INTO weather.publication(product,camera_id,product_key,revision,fence,
  intent_id,state,source_start,source_end,public_not_before)
VALUES(:product,:camera,:key,:revision,:fence,:intent,'published',:start,:end,:embargo)
RETURNING id"""


async def publication(
    c: Any, product: str, key: str, roles: dict[str, tuple[str, str, str, str]], **times: Any
) -> UUID:
    intent = (
        await c.execute(text(INTENT_SQL), {"product": product, "camera": "skycam_east", "key": key})
    ).one()
    publication_id = (
        await c.execute(
            text(PUBLICATION_SQL),
            {
                "product": product,
                "camera": "skycam_east",
                "key": key,
                "revision": times.get("revision", 1),
                "fence": intent.fence,
                "intent": intent.id,
                **{k: v for k, v in times.items() if k != "revision"},
            },
        )
    ).scalar_one()
    for role, (asset_class, sha, ext, mime) in roles.items():
        # Assets are content-addressed: a later revision reuses an unchanged role's asset row.
        object_key = f"{asset_class}/{sha}.{ext}"
        asset = (
            await c.execute(
                text("SELECT id FROM weather.media_asset WHERE object_key=:key"), {"key": object_key}
            )
        ).scalar_one_or_none() or (
            await c.execute(
                text(ASSET_SQL),
                {
                    "class": asset_class,
                    "sha": sha,
                    "ext": ext,
                    "key": object_key,
                    "mime": mime,
                    "embargo": times["embargo"],
                },
            )
        ).scalar_one()
        await c.execute(
            text("""INSERT INTO weather.publication_asset(publication_id,product,role,asset_id,
              asset_class) VALUES(:p,:product,:role,:a,:class)"""),
            {
                "p": publication_id,
                "product": product,
                "role": role,
                "a": asset,
                "class": asset_class,
            },
        )
    return publication_id


def sha(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode()).hexdigest()


START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=2)
END = START + timedelta(hours=3)


async def seed(
    admin: AsyncEngine, *, web: str = "web-1", keogram: bool = False, segment: str = "daytime"
) -> tuple[UUID, UUID]:
    """One published, public 3-hour video (900 frames) and optionally its keogram."""
    key = f"{START.date().isoformat()}/{segment}"
    times = {"start": START, "end": END, "embargo": END + timedelta(seconds=630)}
    async with admin.begin() as c:
        roles = {
            "native": ("video", sha(web + "n"), "mp4", "video/mp4"),
            "web": ("video", sha(web), "mp4", "video/mp4"),
            "mobile": ("video", sha(web + "m"), "mp4", "video/mp4"),
            "poster": ("poster", sha(web + "p"), "jpg", "image/jpeg"),
        }
        publication_id = await publication(c, "video", key, roles, **times)
        video_id = (
            await c.execute(
                text("""INSERT INTO weather.video(camera_id,cycle_date,segment,publication_id,
                  revision,fence,state,starts_at,ends_at,frames,public_not_before,committed_at)
                SELECT 'skycam_east',:cycle,:segment,:p,1,p.fence,'published',:start,:end,900,
                  :embargo,now() FROM weather.publication p WHERE p.id=:p RETURNING id"""),
                {"cycle": START.date(), "p": publication_id, "segment": segment, **times},
            )
        ).scalar_one()
        if keogram:
            await publication(
                c,
                "keogram",
                key,
                {"keogram": ("keogram", sha(web + "k"), "png", "image/png")},
                **times,
            )
    return video_id, publication_id


async def claim(worker: AsyncEngine, kind: str, admin: AsyncEngine) -> JobContext:
    async with admin.begin() as c:
        await c.execute(
            text("UPDATE weather.job SET not_before=now() WHERE kind=:kind AND status='queued'"),
            {"kind": kind},
        )
    async with worker.begin() as c:
        job = await jobs.claim(c, kinds=[kind])
    assert job is not None
    return JobContext(worker, job)


def make(
    worker: AsyncEngine,
    script: Script,
    weather: StaticWeather | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Analyzer:
    async def no_sleep(_: float) -> None:
        return None

    return Analyzer(
        worker,
        script.cortex(),
        CortexSlot(worker, grace=0),
        StaticMedia(),
        weather or StaticWeather(station(START, END)),
        profile_id=PROFILE,
        sleep=no_sleep,
        **({"clock": clock} if clock else {}),
    )


async def head(admin: AsyncEngine, video: UUID) -> dict[str, Any]:
    async with admin.connect() as c:
        row = await store.head(c, video)
    assert row is not None
    return row


async def publish(worker: AsyncEngine, publication_id: UUID) -> bool:
    async with worker.begin() as c:
        return await store.on_published(c, publication_id, datetime.now(UTC))


async def count(admin: AsyncEngine, sql: str, **params: Any) -> int:
    async with admin.connect() as c:
        return int((await c.execute(text(sql), params)).scalar_one())


def test_describe_publishes_a_pinned_revision_search_documents_and_visual_events() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin, keogram=True)
        assert await publish(worker, publication_id)
        assert not await publish(worker, publication_id)  # the same bytes queue nothing
        script = Script(by_task(visual="rain_visible"))
        await make(worker, script).describe(await claim(worker, store.DESCRIBE_KIND, admin))
        current = await head(admin, video)
        assert current["state"] == "complete" and current["revision"] == 1
        assert len(script.bodies) == 3  # three hour windows; no synthesis call (C18)
        async with admin.connect() as c:
            revision = (
                (await c.execute(text("SELECT * FROM weather.analysis_revision"))).mappings().one()
            )
        assert revision["model"] == "llm.qwen38u-5080"
        assert revision["model_route"] == "llm.primary.longctx.mm"
        assert revision["media_sha256"] == sha("web-1") and revision["refresh"] == "full"
        assert revision["inputs"]["keogram"] is True and revision["inputs"]["frames"] == 6
        assert revision["coverage"]["windows_reviewed"] == 3
        # Template text only (C18): rendered from the enums and the station's values.
        assert revision["summary"].startswith(
            "The sampled east-sky frames show broken clouds in all 3 reviewed hour windows. "
            "The sampled east-sky frames show light rain in all 3 reviewed hour windows."
        )
        assert revision["author_kind"] == "model"
        # Search (#25): the description replaced the documents and queued one embedding.
        assert (
            await count(
                admin,
                "SELECT count(*) FROM weather.search_document WHERE video_id=:v "
                "AND visibility='public' AND kind='video' AND description_revision=1",
                v=video,
            )
            == 1
        )
        assert await count(admin, "SELECT count(*) FROM weather.job WHERE kind='search.embed'") == 1
        # Visual events (#41): one per clearly visible window, citing the revision.
        assert (
            await count(
                admin, "SELECT count(*) FROM weather.weather_event WHERE rule_family='visual'"
            )
            == 3
        )
        # Raw replies are private and short-lived.
        assert await count(admin, "SELECT count(*) FROM weather.ai_raw_response") == 3
        rows = await DatabaseSource(api).analyses([video])
        public = rows[video]
        assert "body" not in public and "observations" not in public
        analysis = build_analysis(public, True)
        assert analysis.state == "complete" and analysis.provenance is not None
        assert analysis.provenance.keogram_used and analysis.description

    database(body)


def test_a_late_result_never_replaces_a_newer_revision() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        before = await head(admin, video)  # what a slow first run read at its start
        late_context = await claim(worker, store.DESCRIBE_KIND, admin)
        async with worker.begin() as c:
            await store.enqueue_describe(
                c, video, publication_id, tag="retry", now=datetime.now(UTC)
            )
        fast = make(worker, Script(by_task()))
        await fast.describe(await claim(worker, store.DESCRIBE_KIND, admin))
        assert (await head(admin, video))["revision"] == 1
        packet_result = fast._result(before, await _packet(), (), {}, {}, refresh="full")
        await fast._publish(
            late_context, before, Run(video, "late"), packet_result, base=0, describe=True
        )
        current = await head(admin, video)
        assert current["revision"] == 1 and current["state"] == "complete"
        assert await count(admin, "SELECT count(*) FROM weather.analysis_revision") == 1

    database(body)


async def _packet() -> Any:
    from weather.ai.evidence import read_packet

    return await read_packet(StaticWeather(station(START, END)), "late", START, END)


def test_three_quick_evidence_changes_make_one_synthesis_only_refresh() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        weather = StaticWeather(station(START, END))
        unit = make(worker, Script(by_task(visual="rain_visible")), weather)
        await unit.describe(await claim(worker, store.DESCRIBE_KIND, admin))
        events = await count(admin, "SELECT count(*) FROM weather.weather_event_revision")
        async with admin.begin() as c:
            await c.execute(text("UPDATE weather.analysis SET evidence_checked_at=NULL"))
        # Local noon today, so the three checks share one local day.
        today = datetime.now(store.LOCAL).date()
        noon = datetime.combine(today, time(12), store.LOCAL).astimezone(UTC)
        for step, humidity in enumerate((60.0, 61.0, 62.0)):
            weather.items = station(START, END, humidity_mean=humidity)
            await unit.check_evidence(noon + timedelta(minutes=16 * step))
        assert (
            await count(
                admin, "SELECT count(*) FROM weather.job WHERE kind=:k", k=store.SYNTHESIZE_KIND
            )
            == 1
        )
        assert (await head(admin, video))["stale"] is True
        await unit.synthesize(await claim(worker, store.SYNTHESIZE_KIND, admin))
        current = await head(admin, video)
        assert current["revision"] == 2 and current["stale"] is False
        async with admin.connect() as c:
            refresh, visual, first, kept = (
                await c.execute(
                    text("""SELECT r2.refresh,r2.visual_id,r1.id,
                      (r2.model,r2.prompt_sha256,r2.schema_sha256)
                        = (r1.model,r1.prompt_sha256,r1.schema_sha256)
                    FROM weather.analysis_revision r2
                    JOIN weather.analysis_revision r1 ON r1.video_id=r2.video_id
                      AND r1.revision=1 WHERE r2.revision=2""")
                )
            ).one()
        # A re-render keeps the model and prompt that produced its observations.
        assert refresh == "measurements" and visual == first and kept
        # The visual events cite the unchanged Stage A, so nothing was revised.
        assert await count(admin, "SELECT count(*) FROM weather.weather_event_revision") == events

    database(body)


def test_cortex_down_stays_pending_retries_nightly_then_fails_after_seven_days() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        down = Script([("down",)])
        now = datetime.now(UTC)
        await make(worker, down, clock=lambda: now).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        first = await head(admin, video)
        assert first["state"] == "pending" and first["reason"] == "cortex_unavailable"
        assert len(down.bodies) == 3  # first try and two retries, then stop
        # The video itself is public on time; its analysis is labelled pending.
        public = (await DatabaseSource(api).analyses([video]))[video]
        assert build_analysis(public, True).state == "pending"
        retry_at = store.next_night(now)
        async with admin.connect() as c:
            slot = (
                await c.execute(
                    text("SELECT next_attempt_at FROM weather.analysis WHERE video_id=:v"),
                    {"v": video},
                )
            ).scalar_one()
        assert slot == retry_at
        async with worker.begin() as c:
            assert await store.nightly(c, retry_at - timedelta(seconds=1)) == 0
            assert await store.nightly(c, retry_at) == 1
        # The slot moves to the following night (a lost job is queued again) and the
        # head's call budget is refilled.
        moved = await head(admin, video)
        async with admin.connect() as c:
            slot, spent = (
                await c.execute(
                    text("""SELECT next_attempt_at,attempt_calls FROM weather.analysis
                    WHERE video_id=:v"""),
                    {"v": video},
                )
            ).one()
        assert moved["state"] == "pending" and slot == store.next_night(retry_at) and spent == 0
        await make(worker, Script(by_task())).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        assert (await head(admin, video))["state"] == "complete"
        # Another video still pending 7 days after its first attempt is failed, keeping its
        # reason; one never attempted (intel disabled) does not expire.
        other, other_publication = await seed(admin, web="web-2", segment="overnight")
        await publish(worker, other_publication)
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.analysis SET pending_since=now() - interval '9 days',
                  reason='cortex_unavailable' WHERE video_id=:v"""),
                {"v": other},
            )
        async with worker.begin() as c:
            assert await store.expire(c, datetime.now(UTC)) == 0
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.analysis SET attempted_at=now() - interval '8 days'
                WHERE video_id=:v"""),
                {"v": other},
            )
        async with worker.begin() as c:
            assert await store.nightly(c, datetime.now(UTC) + timedelta(days=2)) == 0
            assert await store.expire(c, datetime.now(UTC)) == 1
        failed = await head(admin, other)
        assert failed["state"] == "failed" and failed["reason"] == "cortex_unavailable"

    database(body)


def test_two_intel_processes_never_have_two_cortex_calls_in_flight() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        second = engine("weather_worker")
        try:
            slots = [CortexSlot(worker, grace=0), CortexSlot(second, grace=0)]
            gauge = CountingSlot()

            async def call(index: int) -> None:
                async with slots[index % 2].hold(30), gauge.hold(0):
                    await asyncio.sleep(0.05)

            await asyncio.gather(*(call(i) for i in range(8)))
            assert gauge.peak == 1
        finally:
            await second.dispose()

    database(body)


def test_raw_responses_are_private_purged_after_a_day_and_revisions_append_only() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        await make(worker, Script(by_task())).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        async with admin.begin() as c:
            await c.execute(
                text("""INSERT INTO weather.ai_raw_response(video_id,stage,call,outcome,body,
                  created_at,expires_at) VALUES(:v,'visual',1,'accepted','{}',
                  now() - interval '25 hours',now() - interval '1 hour')"""),
                {"v": video},
            )
        fresh = await count(admin, "SELECT count(*) FROM weather.ai_raw_response") - 1
        async with worker.begin() as c:
            assert await store.purge_raw(c) == 1
        assert await count(admin, "SELECT count(*) FROM weather.ai_raw_response") == fresh
        for sql in (
            "SELECT body FROM weather.ai_raw_response",
            "SELECT summary FROM weather.analysis_revision",
            "SELECT state FROM weather.analysis",
        ):
            with pytest.raises(DBAPIError) as denied:
                async with api.connect() as c:
                    await c.execute(text(sql))
            assert "permission denied" in str(denied.value.orig).lower()
        with pytest.raises(DBAPIError):
            async with admin.begin() as c:
                await c.execute(
                    text("""INSERT INTO weather.ai_raw_response(video_id,stage,call,outcome,
                      expires_at) VALUES(:v,'visual',1,'accepted',now() + interval '25 hours')"""),
                    {"v": video},
                )
        with pytest.raises(DBAPIError):
            async with admin.begin() as c:
                await c.execute(text("UPDATE weather.analysis_revision SET summary='edited'"))

    database(body)


def test_withdrawal_hides_the_analysis_and_withdraws_search_and_events() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        await make(worker, Script(by_task(visual="rain_visible"))).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        async with admin.begin() as c:
            await c.execute(
                text("""UPDATE weather.video SET withdrawn=true,withdrawn_at=now(),
                  withdraw_reason='test' WHERE id=:v"""),
                {"v": video},
            )
        async with worker.begin() as c:
            await store.on_withdrawn(c, video, datetime.now(UTC))
        assert await DatabaseSource(api).analyses([video]) == {}
        assert (
            await count(
                admin, "SELECT count(*) FROM weather.search_document WHERE visibility='public'"
            )
            == 0
        )
        assert (
            await count(
                admin,
                """SELECT count(*) FROM weather.weather_event e
                JOIN weather.weather_event_revision r ON r.event_id=e.id AND r.revision=e.revision
                WHERE r.visibility='public'""",
            )
            == 0
        )

    database(body)


def test_new_web_bytes_reset_the_head_and_the_old_revision_is_hidden() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        await make(worker, Script(by_task())).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        times = {"start": START, "end": END, "embargo": END + timedelta(seconds=630), "revision": 2}
        async with admin.begin() as c:
            newer = await publication(
                c,
                "video",
                f"{START.date().isoformat()}/daytime",
                {"web": ("video", sha("web-rerender"), "mp4", "video/mp4")},
                **times,
            )
            await c.execute(
                text("UPDATE weather.video SET publication_id=:p,revision=2 WHERE id=:v"),
                {"p": newer, "v": video},
            )
        assert await publish(worker, newer)
        current = await head(admin, video)
        assert current["state"] == "pending" and current["revision"] == 1
        public = (await DatabaseSource(api).analyses([video]))[video]
        assert public["revision_state"] is None
        assert build_analysis(public, True).description is None

    database(body)



def test_model_output_never_replaces_a_human_revision() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        weather = StaticWeather(station(START, END))
        unit = make(worker, Script(by_task()), weather)
        await unit.describe(await claim(worker, store.DESCRIBE_KIND, admin))
        # #44's editor appends an operator revision of the same media.
        async with admin.begin() as c:
            await c.execute(
                text("""INSERT INTO weather.analysis_revision(id,video_id,revision,publication_id,
                  media_sha256,state,reason,refresh,visual_id,model_route,model,prompt_sha256,
                  schema_sha256,evidence_hash,evidence_count,summary,claims,observations,
                  limitations,coverage,inputs,event_keys,calls,author_kind)
                SELECT gen_random_uuid(),video_id,2,publication_id,media_sha256,'complete',NULL,
                  refresh,visual_id,'editor','editor',prompt_sha256,schema_sha256,evidence_hash,
                  evidence_count,'Edited by hand.',claims,observations,limitations,coverage,
                  inputs,event_keys,0,'operator'
                FROM weather.analysis_revision WHERE video_id=:v AND revision=1"""),
                {"v": video},
            )
            await c.execute(
                text("""UPDATE weather.analysis SET revision=2,state='complete',reason=NULL,
                  evidence_checked_at=NULL WHERE video_id=:v"""),
                {"v": video},
            )
        # The worker itself can never write an author: its INSERT grant excludes the column.
        with pytest.raises(DBAPIError) as denied:
            async with worker.begin() as c:
                await c.execute(
                    text("""INSERT INTO weather.analysis_revision(id,video_id,revision,
                      publication_id,media_sha256,state,reason,refresh,visual_id,model_route,
                      model,prompt_sha256,schema_sha256,evidence_hash,evidence_count,summary,
                      claims,observations,limitations,coverage,inputs,event_keys,calls,
                      author_kind)
                    SELECT gen_random_uuid(),video_id,9,publication_id,media_sha256,state,
                      reason,refresh,visual_id,model_route,model,prompt_sha256,schema_sha256,
                      evidence_hash,evidence_count,summary,claims,observations,limitations,
                      coverage,inputs,event_keys,calls,'operator'
                    FROM weather.analysis_revision WHERE video_id=:v AND revision=1"""),
                    {"v": video},
                )
        assert "permission denied" in str(denied.value.orig).lower()
        # Changed measurements queue no refresh over a human revision...
        weather.items = station(START, END, humidity_mean=70.0)
        assert await unit.check_evidence(datetime.now(UTC)) == 0
        # ...and a refresh or late result based on it publishes nothing.
        async with worker.begin() as c:
            assert await store.request_refresh(
                c, video, publication_id, 2, datetime.now(UTC)
            )
        context = await claim(worker, store.SYNTHESIZE_KIND, admin)
        await unit.synthesize(context)
        result = unit._result(
            await head(admin, video), await _packet(), (), {}, {}, refresh="measurements"
        )
        await unit._publish(
            context, await head(admin, video), Run(video, "late"), result, base=2, describe=False
        )
        current = await head(admin, video)
        assert current["revision"] == 2
        assert await count(admin, "SELECT count(*) FROM weather.analysis_revision") == 2

    database(body)


def test_withdrawal_waits_for_an_in_flight_publication() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        await make(worker, Script(by_task(visual="rain_visible"))).describe(
            await claim(worker, store.DESCRIBE_KIND, admin)
        )
        second = engine("weather_worker")
        try:
            async with worker.begin() as publishing:
                # A publication holds the head; the video is withdrawn meanwhile.
                assert await store.head(publishing, video, lock=True) is not None
                async with admin.begin() as c:
                    await c.execute(
                        text("""UPDATE weather.video SET withdrawn=true,withdrawn_at=now(),
                          withdraw_reason='test' WHERE id=:v"""),
                        {"v": video},
                    )

                async def withdraw() -> None:
                    async with second.begin() as c:
                        await store.on_withdrawn(c, video, datetime.now(UTC))

                task = asyncio.create_task(withdraw())
                await asyncio.sleep(0.5)
                # The hook waits for the publication rather than reading stale event keys.
                assert not task.done()
            await asyncio.wait_for(task, timeout=10)
        finally:
            await second.dispose()
        assert (
            await count(
                admin,
                """SELECT count(*) FROM weather.weather_event e
                JOIN weather.weather_event_revision r ON r.event_id=e.id AND r.revision=e.revision
                WHERE r.visibility='public'""",
            )
            == 0
        )

    database(body)


def test_a_spent_call_budget_publishes_the_accepted_windows() -> None:
    async def body(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        video, publication_id = await seed(admin)
        await publish(worker, publication_id)
        # Earlier attempts of this queued describe spent all but one call.
        async with admin.begin() as c:
            await c.execute(
                text("UPDATE weather.analysis SET attempt_calls=:n WHERE video_id=:v"),
                {"n": MAX_CALLS_PER_RUN - 1, "v": video},
            )
        script = Script(by_task())
        await make(worker, script).describe(await claim(worker, store.DESCRIBE_KIND, admin))
        current = await head(admin, video)
        assert len(script.bodies) == 1
        assert current["state"] == "degraded" and current["reason"] == "call_budget"
        async with admin.connect() as c:
            coverage = (
                await c.execute(text("SELECT coverage FROM weather.analysis_revision"))
            ).scalar_one()
        assert coverage["windows_reviewed"] == 1 and coverage["windows_total"] == 3

    database(body)
