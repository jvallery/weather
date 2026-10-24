# SPDX-License-Identifier: MIT
"""Search against the ephemeral PostgreSQL 16.15 / pgvector 0.8.6 in fleet CI.

Documents are written by the builder as weather_worker and read by the API as weather_api.
Vectors are the recorded embed.main outputs; Cortex is never called.
Run only through scripts/ci_integration.sh in the approved fleet validation image.
"""

import asyncio
import base64
import itertools
import json
import math
import os
import struct
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from weather.api import search as search_module
from weather.api.models import SearchRequest
from weather.api.registry import PublicError
from weather.api.search import SearchReader, SearchService
from weather.db import jobs
from weather.db.models import metadata
from weather.scheduler.registry import JobContext
from weather.search.cortex import PROFILES, EmbeddingUnavailable
from weather.search.documents import (
    EMBED_KIND,
    StaleRevision,
    VideoText,
    WindowText,
    documents,
    embed_handler,
    enqueue_missing,
    replace,
    withdraw,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WEATHER_CI_IN_CLUSTER") != "1", reason="Approved fleet CI image required"
    ),
]
FIXTURE = json.loads((Path(__file__).parent / "fixtures/search-recorded.json").read_text())
PROFILE = PROFILES["embed-main-2560-v1"]
VALID = '{"valid":true,"reason":"valid","invalid":[]}'
SEED_PROFILE = """INSERT INTO weather.embedding_profile(id,model,dimensions,query_prefix)
VALUES(:id,'embed.main',2560,:prefix) ON CONFLICT DO NOTHING"""


def unpack(encoded: str) -> list[float]:
    values = struct.unpack("<2560e", base64.b64decode(encoded))
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [v / norm for v in values]


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


QUERIES = {q["name"]: (q["query"], unpack(q["vector"])) for q in FIXTURE["queries"]}


def video_text(spec: dict[str, Any], revision: int = 1, **changes: Any) -> VideoText:
    values: dict[str, Any] = {
        "video_id": UUID(spec["video_id"]),
        "camera_id": spec["camera_id"],
        "camera_label": spec["camera_label"],
        "segment": spec["segment"],
        "video_start": utc(spec["video_start"]),
        "video_end": utc(spec["video_end"]),
        "public_not_before": utc(spec["video_end"]) + timedelta(seconds=630),
        "media_revision": 1,
        "description_revision": revision,
        "editorial_revision": 0,
        "analysis_mode": "visual",
        "observed_conditions": spec["observed_conditions"],
        "evidence_quality": spec["evidence_quality"],
        "media_seconds": 240.0,
        "windows": tuple(
            WindowText(
                utc(w["source_start"]), utc(w["source_end"]), w["observed_conditions"],
                w["evidence_quality"], 100.0, 110.0,
            )
            for w in spec.get("windows", ())
        ),
    }
    values.update(changes)
    return VideoText(**values)


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
    """Empty catalog plus the seeded profile; admin, builder (worker) and API engines."""

    async def run() -> None:
        admin, worker, api = engine(), engine("weather_worker"), engine("weather_api")
        try:
            async with admin.begin() as c:
                tables = ",".join("weather." + t.name for t in metadata.sorted_tables)
                await c.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
                seed = {"id": PROFILE.id, "prefix": PROFILE.query_prefix}
                await c.execute(text(SEED_PROFILE), seed)
            await test(admin, worker, api)
        finally:
            for item in (admin, worker, api):
                await item.dispose()

    asyncio.run(run())


class DocumentEmbedder:
    """Recorded document vectors keyed by canonical text; never contacts Cortex."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.profile = PROFILE
        self.vectors = vectors

    async def embed(self, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        try:
            return [self.vectors[t] for t in texts]
        except KeyError:
            raise EmbeddingUnavailable("error") from None


class QueryEmbedder:
    def __init__(
        self, delay: float = 0.0, probe: Any = None, extra: dict[str, list[float]] | None = None
    ) -> None:
        self.profile, self.delay, self.probe = PROFILE, delay, probe
        self.extra = extra or {}
        self.down = False

    async def query(self, query: str, *, timeout: float) -> list[float]:
        if self.probe is not None:
            await self.probe()
        try:
            async with asyncio.timeout(timeout):
                await asyncio.sleep(self.delay)
        except TimeoutError:
            raise EmbeddingUnavailable("timeout") from None
        known = {**dict(QUERIES.values()), **self.extra}
        if self.down or query not in known:
            raise EmbeddingUnavailable("error")
        return known[query]


FENCES = itertools.count(1)
# The minimal #18 catalog chain a search document's video_id references.
CATALOG_SQL = """WITH i AS (
  INSERT INTO weather.upload_intent(fence,product,camera_id,product_key,expected_revision,
    manifest,state,deadline,resolved_at)
  VALUES(:fence,'video',:camera,:key,0,'{}'::jsonb,'committed',now() + interval '1 day',now())
  RETURNING id
), p AS (
  INSERT INTO weather.publication(product,camera_id,product_key,revision,fence,intent_id,state,
    source_start,source_end,public_not_before)
  SELECT 'video',:camera,:key,1,:fence,i.id,'published',:start,:end,:embargo FROM i RETURNING id
)
INSERT INTO weather.video(id,camera_id,cycle_date,segment,publication_id,revision,fence,state,
  starts_at,ends_at,frames,public_not_before,committed_at)
SELECT :video,:camera,:cycle,:segment,p.id,1,:fence,'published',:start,:end,7200,:embargo,now()
FROM p"""


async def catalog(videos: Sequence[VideoText]) -> None:
    admin = engine()
    try:
        async with admin.begin() as c:
            for video in videos:
                if await c.scalar(
                    text("SELECT 1 FROM weather.video WHERE id=:v"), {"v": video.video_id}
                ):
                    continue
                cycle = video.video_start.date()
                await c.execute(
                    text(CATALOG_SQL),
                    {
                        "fence": next(FENCES),
                        "camera": video.camera_id,
                        "key": f"{cycle.isoformat()}/{video.segment}",
                        "video": video.video_id,
                        "cycle": cycle,
                        "segment": video.segment,
                        "start": video.video_start,
                        "end": video.video_end,
                        "embargo": video.public_not_before,
                    },
                )
    finally:
        await admin.dispose()


async def publish(worker: AsyncEngine, videos: Sequence[VideoText]) -> dict[UUID, list[UUID]]:
    await catalog(videos)
    ids = {}
    async with worker.begin() as c:
        for video in videos:
            ids[video.video_id] = await replace(
                c, video, profile_id=PROFILE.id, now=datetime.now(UTC)
            )
    return ids


async def index(worker: AsyncEngine, embedder: Any) -> int:
    """Run every queued embedding job the way the scheduler would."""
    done = 0
    handler = embed_handler(embedder)
    while True:
        async with worker.begin() as c:
            job = await jobs.claim(c, kinds=[EMBED_KIND])
        if job is None:
            return done
        context = JobContext(worker, job)
        await handler(context)
        if not context.committed:
            await context.commit()
        done += 1


async def seed_fixture(worker: AsyncEngine) -> dict[str, UUID]:
    videos = [video_text(spec) for spec in FIXTURE["documents"]]
    await publish(worker, videos)
    vectors = {
        documents(video)[0]["text"]: unpack(spec["vector"])
        for video, spec in zip(videos, FIXTURE["documents"], strict=True)
    }
    assert await index(worker, DocumentEmbedder(vectors)) == len(videos)
    return {spec["name"]: UUID(spec["video_id"]) for spec in FIXTURE["documents"]}


def service(api: AsyncEngine, embedder: Any, profile: str = PROFILE.id) -> SearchService:
    return SearchService(
        SearchReader(api), embedder, profile_id=profile, registry=CollectorRegistry()
    )


WINDOW = {"start": "2026-01-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}


def ask(**values: Any) -> SearchRequest:
    return SearchRequest.model_validate({**WINDOW, **values})


def videos(result: Any, names: dict[str, UUID]) -> list[str]:
    by_id = {str(v): k for k, v in names.items()}
    return [by_id[hit.video_id] for hit in result.hits]


def test_recorded_fixture_ranks_scene_classes_with_exact_cosine_and_fts() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        names = await seed_fixture(worker)
        search = service(api, QueryEmbedder())
        for query, first in (
            ("clear", "clear"),
            ("cloudy", "cloudy"),
            ("snow", "snow"),
            ("night", "night"),
            ("obstructed", "obstructed"),
            ("partial", "partial"),
        ):
            result = await search.search(ask(query=QUERIES[query][0], top_k=5))
            assert result.mode == "hybrid_exact", query
            assert videos(result, names)[0] == first, query
        rain = await search.search(ask(query=QUERIES["rain"][0]))
        assert set(videos(rain, names)[:2]) == {"rain_front", "rain_back"}
        storm = QUERIES["negative"][0]
        assert videos(await search.search(ask(query=storm)), names)[0] == "snow"
        backyard = await search.search(ask(query=storm, cameras=["backyard_1"]))
        assert "snow" not in videos(backyard, names)
        assert {h.camera_id for h in backyard.hits} == {"backyard_1"}
        july = await search.search(ask(query=storm, start="2026-07-01T00:00:00Z"))
        assert "snow" not in videos(july, names)
        overnight = await search.search(ask(query=QUERIES["clear"][0], segments=["overnight"]))
        assert videos(overnight, names) == ["night"]
        # A window match carries its hour, its seek and interval precision.
        down = QueryEmbedder()
        down.down = True
        hail = await service(api, down).search(ask(query="hailstones"))
        assert hail.mode == "lexical" and hail.degraded_reason == "embedding_unavailable"
        (hit,) = hail.hits
        assert hit.evidence_precision == "interval" and hit.seek is not None
        assert hit.source_interval.start == utc("2026-07-03T20:00:00Z")
        assert hit.seek.source_at == hit.source_interval.start

    database(check)


def test_lexical_is_plain_english_and_never_operators_or_sql() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        names = await seed_fixture(worker)
        down = QueryEmbedder()
        down.down = True
        search = service(api, down)
        # Stemming: "puddle" matches "puddles"; all terms must match (AND).
        assert set(videos(await search.search(ask(query="puddle")), names)) == {"rain_front"}
        assert (await search.search(ask(query="puddle snowfall"))).hits == []
        for hostile in (
            "rain | snow",
            "!snow & (hail",
            "snow:* OR rain",
            "'); DROP TABLE weather.search_document; --",
            "snow <-> lawn",
            "-snow",
        ):
            result = await search.search(ask(query=hostile))
            assert result.mode == "lexical"
        # Negation is not semantic: "-snow" still finds the snow video.
        assert "snow" in videos(await search.search(ask(query="-snow")), names)
        for empty in ("the and of", "!!! ..."):
            result = await search.search(ask(query=empty))
            assert result.degraded_reason == "lexical_no_terms" and result.hits == []
        # With a vector, no-terms text is semantic only, never numeric browsing.
        stop = QueryEmbedder(extra={"the and of": QUERIES["snow"][1]})
        semantic = await service(api, stop).search(ask(query="the and of"))
        assert semantic.mode == "hybrid_exact" and videos(semantic, names)[0] == "snow"
        async with admin.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM weather.search_document")) == 10

    database(check)


def test_lexical_matching_uses_the_partial_gin_index() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        await seed_fixture(worker)
        # weather_api runs the SECURITY DEFINER function but cannot read the table itself.
        async with api.connect() as c:
            rows = (
                await c.execute(
                    text(search_module.LEXICAL_SQL),
                    {
                        "text": "snow", "start": utc(WINDOW["start"]), "end": utc(WINDOW["end"]),
                        "cameras": [], "segments": [], "restricted": False, "videos": [],
                    },
                )
            ).all()
            assert len(rows) == 1
        # Its body, planned with the definer's (migrator's) privileges, reaches the index.
        migrator = engine("weather_migrator")
        try:
            async with migrator.connect() as c:
                body = await c.scalar(
                    text("SELECT prosrc FROM pg_proc WHERE proname='search_lexical'")
                )
                for n, value in enumerate(
                    (
                        "'snow'::text", "'2026-01-01T00:00:00Z'::timestamptz",
                        "'2026-10-01T00:00:00Z'::timestamptz",
                        "'{}'::text[]", "'{}'::text[]", "false", "'{}'::uuid[]", "200",
                    ),
                    start=1,
                ):
                    body = body.replace(f"${n}", value)
                await c.execute(text("SET enable_seqscan = off"))
                plan = "\n".join((await c.exec_driver_sql("EXPLAIN " + body)).scalars().all())
            assert "ix_search_document_lexical" in plan, plan
        finally:
            await migrator.dispose()

    database(check)


def test_only_the_active_profile_ranks() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        names = await seed_fixture(worker)
        clear = unpack(FIXTURE["queries"][0]["vector"])
        async with admin.begin() as c:
            await c.execute(text(SEED_PROFILE), {"id": "other-v1", "prefix": "x"})
            # Under another profile the snow document carries the query's own vector.
            await c.execute(
                text("""INSERT INTO weather.search_embedding(document_id,profile_id,text_hash,
                          embedding)
                        SELECT id,'other-v1',text_hash,CAST(CAST(:v AS text) AS vector)
                        FROM weather.search_document WHERE video_id=:video AND kind='video'"""),
                {"v": "[" + ",".join(map(repr, clear)) + "]", "video": names["snow"]},
            )
        result = await service(api, QueryEmbedder()).search(ask(query=QUERIES["clear"][0]))
        assert videos(result, names)[0] == "clear"
        other = await service(api, QueryEmbedder(), "other-v1").search(
            ask(query=QUERIES["clear"][0])
        )
        # Only the snow vector exists for other-v1, so only it ranks semantically.
        assert videos(other, names) == ["snow"]

    database(check)


def test_revision_withdraws_old_text_and_late_jobs_cannot_resurrect_it() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        spec = FIXTURE["documents"][0]
        old = video_text(spec, observed_conditions="Thick fog hides the street all morning.")
        (old_doc, *_) = (await publish(worker, [old]))[old.video_id]
        # The embedding job for the old text is claimed before the human correction lands.
        async with worker.begin() as c:
            late = await jobs.claim(c, kinds=[EMBED_KIND])
        assert late is not None and late.payload["document_id"] == str(old_doc)
        new = video_text(spec, revision=2, editorial_revision=1)
        await publish(worker, [new])
        down = QueryEmbedder()
        down.down = True
        search = service(api, down)
        # Stale text stops matching at commit, before any reindex.
        assert (await search.search(ask(query="fog"))).hits == []
        assert len((await search.search(ask(query="sunshine"))).hits) == 1
        # The late job finishes after the correction: compare-and-set stores nothing.
        context = JobContext(worker, late)
        recorded = DocumentEmbedder({documents(old)[0]["text"]: unpack(spec["vector"])})
        await embed_handler(recorded)(context)  # type: ignore[arg-type]
        if not context.committed:
            await context.commit()
        async with admin.connect() as c:
            assert await c.scalar(
                text("SELECT count(*) FROM weather.search_embedding WHERE document_id=:d"),
                {"d": old_doc},
            ) == 0
        # Replaying the producer (outbox crash) is idempotent; an older revision is refused.
        again = await publish(worker, [new])
        assert len(again[new.video_id]) == 1
        with pytest.raises(StaleRevision):
            await publish(worker, [old])
        with pytest.raises(StaleRevision):
            await publish(worker, [video_text(spec, revision=2, editorial_revision=1,
                                              evidence_quality="Different text.")])
        async with worker.connect() as c:
            with pytest.raises(DBAPIError) as failure:
                async with c.begin():
                    await c.execute(
                        text("""UPDATE weather.search_document
                                SET visibility='public',withdrawn_at=NULL WHERE id=:d"""),
                        {"d": old_doc},
                    )
            assert getattr(failure.value.orig, "sqlstate", None) == "55000"
            with pytest.raises(DBAPIError) as denied:
                async with c.begin():
                    await c.execute(text("UPDATE weather.search_document SET text='x'"))
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        # A withdrawal (publication or editor) removes every document of the video at once.
        async with worker.begin() as c:
            assert await withdraw(c, new.video_id) == 1
        assert (await search.search(ask(query="sunshine"))).hits == []
        assert (await search.search(ask(query=""))).hits == []
        # A late replay of the withdrawn revision cannot bring it back; a newer one can.
        with pytest.raises(StaleRevision):
            await publish(worker, [new])
        await publish(worker, [video_text(spec, revision=2, editorial_revision=2)])
        assert len((await search.search(ask(query="sunshine"))).hits) == 1

    database(check)


def test_database_constraints_bind_vectors_to_current_text_and_unit_norm() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        await seed_fixture(worker)
        async with admin.connect() as c:
            # A window document has no vector yet, so only the constraint under test can fail.
            doc, digest = (
                await c.execute(
                    text("""SELECT id,text_hash FROM weather.search_document
                            WHERE kind='window' ORDER BY id LIMIT 1""")
                )
            ).one()
            await c.rollback()
            half = "[" + ",".join(["0.5"] + ["0"] * 2559) + "]"
            for vector, digest_, profile, state in (
                (half, digest, PROFILE.id, "23514"),  # not unit norm
                ("[" + ",".join(["0"] * 2560) + "]", digest, PROFILE.id, "23514"),
                ("[1" + ",0" * 2559 + "]", "0" * 64, PROFILE.id, "23503"),  # stale text hash
                ("[1" + ",0" * 2559 + "]", digest, "missing-v1", "23503"),
            ):
                with pytest.raises(DBAPIError) as failure:
                    async with c.begin():
                        await c.execute(
                            text("""INSERT INTO weather.search_embedding
                                    VALUES(:d,:p,:h,CAST(CAST(:v AS text) AS vector),now())"""),
                            {"d": doc, "p": profile, "h": digest_, "v": vector},
                        )
                assert getattr(failure.value.orig, "sqlstate", None) == state
            with pytest.raises(DBAPIError):
                async with c.begin():
                    await c.execute(
                        text("UPDATE weather.search_document SET text_hash=:h WHERE id=:d"),
                        {"h": "0" * 64, "d": doc},
                    )
            # Every document belongs to a catalog video (#18).
            with pytest.raises(DBAPIError) as orphan:
                async with c.begin():
                    await c.execute(
                        text("UPDATE weather.search_document SET video_id=:v WHERE id=:d"),
                        {"v": uuid4(), "d": doc},
                    )
            assert getattr(orphan.value.orig, "sqlstate", None) == "23503"
        async with api.connect() as c:
            for relation in ("weather.search_document", "weather.search_embedding"):
                with pytest.raises(DBAPIError):
                    async with c.begin():
                        await c.execute(text(f"SELECT 1 FROM {relation} LIMIT 1"))

    database(check)


def test_no_connection_is_held_while_the_query_embeds() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        names = await seed_fixture(worker)
        observed: list[tuple[int, int]] = []

        async def probe() -> None:
            async def later() -> None:
                await asyncio.sleep(0.75)
                async with admin.connect() as c:
                    busy = await c.scalar(
                        text("""SELECT count(*) FROM pg_stat_activity
                                WHERE usename='weather_api' AND state <> 'idle'""")
                    )
                observed.append((api.sync_engine.pool.checkedout(), int(busy)))  # type: ignore[attr-defined]

            asyncio.get_running_loop().create_task(later())

        for delay, mode in ((1.4, "hybrid_exact"), (1.6, "lexical")):
            started = time.monotonic()
            result = await service(api, QueryEmbedder(delay, probe)).search(
                ask(query=QUERIES["clear"][0])
            )
            assert result.mode == mode
            assert time.monotonic() - started < 5
        assert observed == [(0, 0), (0, 0)]
        assert names

    database(check)


def test_exhausted_sql_budget_is_unavailable_and_the_pool_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        await seed_fixture(worker)
        search = service(api, QueryEmbedder())
        monkeypatch.setattr(search_module, "SQL_SECONDS", 0.0005)
        with pytest.raises(PublicError) as caught:
            await search.search(ask(query=QUERIES["clear"][0]))
        assert caught.value.code == "unavailable"
        monkeypatch.setattr(search_module, "SQL_SECONDS", 1.5)
        assert api.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        result = await search.search(ask(query=QUERIES["clear"][0]))
        assert result.hits

    database(check)


def test_profile_change_backlog_and_reembedding() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        await seed_fixture(worker)
        search = service(api, QueryEmbedder())
        await search.refresh_backlog()
        assert search.metrics.backlog._value.get() == 0  # type: ignore[attr-defined]
        async with admin.begin() as c:
            await c.execute(text(SEED_PROFILE), {"id": "next-v1", "prefix": "x"})
        nxt = service(api, None, "next-v1")
        await nxt.refresh_backlog()
        assert nxt.metrics.backlog._value.get() == 8  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        async with worker.begin() as c:
            assert await enqueue_missing(c, "next-v1", now) == 8
            assert await enqueue_missing(c, "next-v1", now) == 8  # idempotent per day

    database(check)


# Measured predicates over #14's on-read aggregates -------------------------------------


async def telemetry(
    c: AsyncConnection, device: UUID, rows: Sequence[tuple[datetime, dict[str, Any]]]
) -> None:
    ids = [uuid4() for _ in rows]
    await c.execute(
        text("""INSERT INTO weather.telemetry_receipt(id,received_at,raw_bytes,sha256,
                  parse_state,parser_revision,receiver_incarnation)
                SELECT id,ts,convert_to(id::text,'UTF8'),
                  encode(sha256(convert_to(id::text,'UTF8')),'hex'),'parsed','test','test'
                FROM unnest(CAST(:ids AS uuid[]),CAST(:ts AS timestamptz[])) AS t(id,ts)"""),
        {"ids": ids, "ts": [at for at, _ in rows]},
    )
    await c.execute(
        text("""INSERT INTO weather.obs_st(device_id,receipt_id,source_ts,row_idx,"values",
                  quality)
                SELECT :device,rid,ts,0,CAST(v AS jsonb),CAST(:quality AS jsonb)
                FROM unnest(CAST(:ids AS uuid[]),CAST(:ts AS timestamptz[]),
                  CAST(:vals AS text[])) AS t(rid,ts,v)"""),
        {
            "device": device,
            "ids": ids,
            "ts": [at for at, _ in rows],
            "vals": [json.dumps(v) for _, v in rows],
            "quality": VALID,
        },
    )


def reports(start: datetime, end: datetime, every: int = 60, **values: Any) -> list[Any]:
    """One obs_st report per minute ending at each minute: full coverage of (start, end]."""
    count = int((end - start).total_seconds() // 60)
    return [
        (start + timedelta(seconds=60 * (n + 1)), dict(values))
        for n in range(count)
        if (n * 60) % every == 0
    ]


def test_predicates_units_zero_missing_coverage_dst_and_late_rows() -> None:
    async def check(admin: AsyncEngine, worker: AsyncEngine, api: AsyncEngine) -> None:
        base = FIXTURE["documents"][5]  # overnight
        # Overnight segments across the 2026-03-08 America/Denver DST start (09:00 UTC).
        spans = {
            "rain": ("2026-03-07T01:00:00Z", "2026-03-07T13:00:00Z"),
            "dry": ("2026-03-08T01:00:00Z", "2026-03-08T13:00:00Z"),
            "missing": ("2026-03-09T01:00:00Z", "2026-03-09T13:00:00Z"),
            "sparse": ("2026-03-10T01:00:00Z", "2026-03-10T13:00:00Z"),
        }
        made = {
            name: video_text(
                base, video_id=uuid4(), video_start=utc(s), video_end=utc(e),
                public_not_before=utc(e) + timedelta(seconds=630),
            )
            for name, (s, e) in spans.items()
        }
        await publish(worker, list(made.values()))
        async with admin.begin() as c:
            station = await c.scalar(
                text("INSERT INTO weather.station(alias,site) VALUES('home','local') RETURNING id")
            )
            device = await c.scalar(
                text("""INSERT INTO weather.device(station_id,alias,private_id,kind,valid_from)
                        VALUES(:s,'st','SYNTHETIC-ST','ST','2020-01-01T00:00:00Z')
                        RETURNING id"""),
                {"s": station},
            )
            r = made["rain"]
            await telemetry(c, device, reports(
                r.video_start, r.video_end, precip_accumulated=12.7 / 720,
                air_temperature=10.0, wind_gust=22.352,
            ))
            d = made["dry"]
            await telemetry(c, device, reports(
                d.video_start, d.video_end, precip_accumulated=0.0, air_temperature=-5.0,
                wind_gust=3.0,
            ))
            s = made["sparse"]
            await telemetry(c, device, reports(
                s.video_start, s.video_end, every=600, precip_accumulated=0.0, wind_gust=3.0,
            ))
        search = service(api, None)
        window = {"start": "2026-03-01T00:00:00Z", "end": "2026-03-31T00:00:00Z"}

        async def matching(*predicates: dict[str, Any]) -> set[str]:
            result = await search.search(
                SearchRequest.model_validate({**window, "predicates": list(predicates)})
            )
            assert result.mode == "numeric"
            ids = {str(v.video_id): k for k, v in made.items()}
            return {ids[h.video_id] for h in result.hits}

        rain = {"metric": "precipitation", "statistic": "sum"}
        # Zero is not missing, and under-covered zero is not zero.
        assert await matching({**rain, "op": "=", "value": 0, "unit": "mm"}) == {"dry"}
        assert await matching({**rain, "op": "<", "value": 0.1, "unit": "in"}) == {"dry"}
        # 0.5 in is exactly 12.7 mm: the touching boundary matches >= and =, not >.
        for value, unit in ((0.5, "in"), (12.7, "mm")):
            assert await matching({**rain, "op": ">=", "value": value, "unit": unit}) == {"rain"}
            assert await matching({**rain, "op": "=", "value": value, "unit": unit}) == {"rain"}
            assert await matching({**rain, "op": ">", "value": value, "unit": unit}) == set()
        gust = {"metric": "wind_gust", "statistic": "max"}
        for value, unit in ((50, "mph"), (22.352, "m/s"), (80.4672, "km/h")):
            assert await matching({**gust, "op": ">=", "value": value, "unit": unit}) == {"rain"}
        # Sparse gust reports cover too little of the segment to qualify, even at min 0.5.
        calm = {**gust, "op": "<", "value": 5, "unit": "m/s"}
        assert await matching(calm) == {"dry"}
        assert await matching({**calm, "min_coverage": 0.5}) == {"dry"}
        temperature = {"metric": "temperature", "statistic": "max"}
        assert await matching({**temperature, "op": ">=", "value": 50, "unit": "degF"}) == {
            "rain"
        }
        assert await matching(
            {**temperature, "op": ">=", "value": 10, "unit": "degC"},
            {**rain, "op": ">", "value": 0, "unit": "mm"},
        ) == {"rain"}
        result = await search.search(
            SearchRequest.model_validate(
                {**window, "predicates": [{**rain, "op": ">", "value": 0, "unit": "mm"}]}
            )
        )
        (measured,) = result.hits[0].matched_measurements
        assert measured.interval.start == made["rain"].video_start
        assert measured.coverage.fraction == 1.0 and measured.evidence_count == 720
        assert measured.evidence_hash is not None and len(measured.evidence_hash) == 64
        # A late report changes the next read; the documents themselves never change.
        async with admin.begin() as c:
            late = made["dry"].video_start + timedelta(seconds=90)
            await telemetry(c, device, [(late, {"precip_accumulated": 1.0})])
        assert await matching({**rain, "op": "=", "value": 0, "unit": "mm"}) == set()

    database(check)
