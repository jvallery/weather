# SPDX-License-Identifier: MIT
"""Search behaviour without a database or Cortex: modes, fusion, predicates, bounds, embedding.

Vectors are the recorded embed.main outputs in fixtures/search-recorded.json; CI never calls
Cortex. The same fixture runs against PostgreSQL in test_search_integration.py.
"""

import asyncio
import base64
import importlib.util
import json
import math
import re
import struct
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from weather.api import build_app
from weather.api.models import Predicate, SearchRequest
from weather.api.registry import PublicError
from weather.api.search import (
    FILTER,
    LEXICAL_SQL,
    SEMANTIC_SQL,
    TERMS_SQL,
    Filters,
    SearchService,
    SearchSession,
    fuse,
    threshold,
)
from weather.api.source import SourceUnavailable
from weather.search.cortex import PROFILES, Embedder, EmbeddingUnavailable, validate
from weather.search.documents import VideoText, WindowText, documents

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / "fixtures/search-recorded.json").read_text())
PROFILE = PROFILES["embed-main-2560-v1"]
STOP = {"a", "an", "and", "at", "in", "of", "on", "or", "the", "to", "with", "not", "no"}


def unpack(encoded: str) -> list[float]:
    values = struct.unpack("<2560e", base64.b64decode(encoded))
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [v / norm for v in values]


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def video_text(spec: dict[str, Any]) -> VideoText:
    return VideoText(
        video_id=UUID(spec["video_id"]),
        camera_id=spec["camera_id"],
        camera_label=spec["camera_label"],
        segment=spec["segment"],
        video_start=utc(spec["video_start"]),
        video_end=utc(spec["video_end"]),
        public_not_before=utc(spec["video_end"]) + timedelta(seconds=630),
        media_revision=1,
        description_revision=1,
        editorial_revision=0,
        analysis_mode="visual",
        observed_conditions=spec["observed_conditions"],
        evidence_quality=spec["evidence_quality"],
        media_seconds=240.0,
        windows=tuple(
            WindowText(
                utc(w["source_start"]),
                utc(w["source_end"]),
                w["observed_conditions"],
                w["evidence_quality"],
                media_start_seconds=100.0,
                media_end_seconds=110.0,
            )
            for w in spec.get("windows", ())
        ),
    )


DOCS: list[dict[str, Any]] = []
NAMES: dict[UUID, str] = {}
for _spec in FIXTURE["documents"]:
    for _n, _row in enumerate(documents(video_text(_spec))):
        _row["id"] = UUID(int=UUID(_spec["video_id"]).int + (_n << 64))
        _row["vectors"] = {PROFILE.id: unpack(_spec["vector"])} if _row["kind"] == "video" else {}
        _row["visibility"] = "public"
        NAMES[_row["id"]] = _spec["name"] if _row["kind"] == "video" else f"{_spec['name']}#{_n}"
        DOCS.append(_row)
QUERIES = {q["name"]: (q["query"], unpack(q["vector"])) for q in FIXTURE["queries"]}
WINDOW = {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}
JULY = {"start": "2026-07-01T00:00:00Z", "end": "2026-07-31T00:00:00Z"}


def words(value: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", value.lower()) if w not in STOP]


class FakeSession:
    """In-memory stand-in with the SQL's filter and ordering rules (PostgreSQL FTS differs)."""

    def __init__(self, reader: FakeReader) -> None:
        self.reader = reader

    def _filtered(self, filters: Filters) -> list[dict[str, Any]]:
        p = filters.params()
        return [
            d
            for d in self.reader.docs
            if d["visibility"] == "public"
            and d["source_end"] > p["start"]
            and d["source_start"] < p["end"]
            and (not p["cameras"] or d["camera_id"] in p["cameras"])
            and (not p["segments"] or d["segment"] in p["segments"])
            and (not p["restricted"] or d["video_id"] in p["videos"])
        ]

    async def _step(self) -> None:
        self.reader.statements += 1
        if self.reader.fail_at is not None and self.reader.statements >= self.reader.fail_at:
            raise SourceUnavailable()

    async def terms(self, query: str) -> int:
        await self._step()
        return len(words(query))

    async def videos(self, filters: Filters, cap: int) -> list[dict[str, Any]]:
        await self._step()
        seen = {}
        for d in self._filtered(filters):
            if d["kind"] == "video":
                seen[d["video_id"]] = {k: d[k] for k in ("video_id", "video_start", "video_end")}
        return list(seen.values())[:cap]

    async def measurements(self, source: Any, intervals: Any, cap: float) -> list[Any]:
        await self._step()
        return [self.reader.aggregates[i] for i in intervals if i in self.reader.aggregates]

    async def semantic(self, filters: Filters, vector: list[float], profile: str) -> list[Any]:
        await self._step()
        scored = [
            (1 - sum(a * b for a, b in zip(vector, d["vectors"][profile], strict=True)), d["id"])
            for d in self._filtered(filters)
            if d["kind"] == "video" and profile in d["vectors"]
        ]
        by_id = {d["id"]: d for d in self.reader.docs}
        return [
            {"id": i, "video_id": by_id[i]["video_id"]} for _, i in sorted(scored)[:200]
        ]

    async def lexical(self, filters: Filters, query: str) -> list[Any]:
        await self._step()
        terms = words(query)
        hits = [
            (-sum(w in words(d["text"]) for w in terms), d["id"], d["video_id"])
            for d in self._filtered(filters)
            if all(w in words(d["text"]) for w in terms)
        ]
        return [{"id": i, "video_id": v} for _, i, v in sorted(hits)[:200]]

    async def newest(self, filters: Filters, top_k: int) -> list[Any]:
        await self._step()
        videos = [d for d in self._filtered(filters) if d["kind"] == "video"]
        return sorted(videos, key=lambda d: (-d["source_start"].timestamp(), d["video_id"]))[
            :top_k
        ]

    async def hydrate(self, ids: list[UUID]) -> list[Any]:
        await self._step()
        if self.reader.before_hydrate:
            self.reader.before_hydrate()
        return [d for d in self.reader.docs if d["id"] in ids and d["visibility"] == "public"]

    async def backlog(self, profile: str) -> int:
        return 0


class FakeReader:
    def __init__(self) -> None:
        self.docs = [dict(d) for d in DOCS]
        self.open = 0
        self.sessions = 0
        self.statements = 0
        self.fail_at: int | None = None
        self.before_hydrate: Any = None
        self.aggregates: dict[tuple[datetime, datetime], dict[str, Any]] = {}

    @asynccontextmanager
    async def session(self, seconds: float) -> AsyncIterator[FakeSession]:
        self.open += 1
        self.sessions += 1
        try:
            yield FakeSession(self)
        finally:
            self.open -= 1


class FakeEmbedder:
    def __init__(self, reader: FakeReader | None = None, delay: float = 0.0) -> None:
        self.profile = PROFILE
        self.reader, self.delay = reader, delay
        self.calls: list[str] = []
        self.down = False
        self.open_during_call: list[int] = []

    async def query(self, text: str, *, timeout: float) -> list[float]:
        self.calls.append(text)
        if self.reader is not None:
            self.open_during_call.append(self.reader.open)
        try:
            async with asyncio.timeout(timeout):
                await asyncio.sleep(self.delay)
        except TimeoutError:
            raise EmbeddingUnavailable("timeout") from None
        if self.down:
            raise EmbeddingUnavailable("error")
        for _, (query, vector) in QUERIES.items():
            if query == text:
                return vector
        raise EmbeddingUnavailable("error")


def make(reader: FakeReader, embedder: Any = None, profile: str = PROFILE.id) -> SearchService:
    return SearchService(
        reader,  # type: ignore[arg-type]
        embedder,
        profile_id=profile,
        registry=CollectorRegistry(),
        clock=lambda: datetime(2026, 10, 24, tzinfo=UTC),
    )


def request(**values: Any) -> SearchRequest:
    return SearchRequest.model_validate({**WINDOW, **values})


def names(result: Any) -> list[str]:
    return [NAMES[UUID(hit.document_id)] for hit in result.hits]


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# Recorded-vector relevance ---------------------------------------------------------------


@pytest.mark.parametrize(
    "query, first",
    [
        ("clear", "clear"),
        ("cloudy", "cloudy"),
        ("snow", "snow"),
        ("night", "night"),
        ("obstructed", "obstructed"),
        ("partial", "partial"),
    ],
)
def test_recorded_vectors_rank_each_scene_class_first(query: str, first: str) -> None:
    reader = FakeReader()
    text, _ = QUERIES[query]
    result = run(make(reader, FakeEmbedder()).search(request(query=text, top_k=3)))
    assert result.mode == "hybrid_exact" and result.degraded_reason is None
    assert result.profile_id == PROFILE.id
    assert names(result)[0] == first
    assert result.count == len(result.hits)


def test_rain_hits_both_cameras_as_separate_videos() -> None:
    result = run(make(FakeReader(), FakeEmbedder()).search(request(query="rainy weather")))
    assert set(names(result)[:2]) == {"rain_front", "rain_back"}
    assert {hit.camera_id for hit in result.hits[:2]} == {"frontyard_1", "backyard_1"}
    assert len({hit.video_id for hit in result.hits}) == len(result.hits)


def test_negative_filters_never_loosen_and_clear_is_not_wet() -> None:
    service = make(FakeReader(), FakeEmbedder())
    storm = QUERIES["negative"][0]
    assert names(run(service.search(request(query=storm))))[0] == "snow"
    backyard = run(service.search(request(query=storm, cameras=["backyard_1"])))
    assert "snow" not in names(backyard) and {h.camera_id for h in backyard.hits} == {
        "backyard_1"
    }
    july = run(service.search(request(query=storm, start="2026-07-01T00:00:00Z")))
    assert "snow" not in names(july)
    clear = names(run(service.search(request(query=QUERIES["clear"][0]))))
    assert not {"snow", "rain_front", "rain_back"} & set(clear[:2])


# Fusion ----------------------------------------------------------------------------------


def U(n: int) -> UUID:
    return UUID(int=n)


def test_rrf_scores_collapse_to_the_best_document_per_video() -> None:
    # Video 1 has many lexical windows; video 2 one document in both branches.
    semantic = [(U(20), U(2)), (U(10), U(1))]
    lexical = [(U(11), U(1)), (U(12), U(1)), (U(13), U(1)), (U(20), U(2))]
    assert fuse(semantic, lexical) == [(U(2), U(20)), (U(1), U(11))]
    # 1/61 + 1/64 for the shared document beats any single 1/61, never a sum of windows.


def test_rrf_ties_break_by_video_then_document() -> None:
    assert fuse([(U(5), U(9))], [(U(4), U(8))]) == [(U(8), U(4)), (U(9), U(5))]
    assert fuse([(U(7), U(1))], [(U(6), U(1))]) == [(U(1), U(6))]
    assert fuse([], []) == []


# Modes -----------------------------------------------------------------------------------


def test_empty_or_whitespace_query_browses_newest_without_the_model() -> None:
    reader, embedder = FakeReader(), FakeEmbedder()
    for text in ("", "   　 "):
        result = run(make(reader, embedder).search(request(query=text, top_k=20)))
        assert result.mode == "numeric" and result.degraded_reason is None
        assert result.profile_id is None
        starts = [h.video_interval.start for h in result.hits]
        assert starts == sorted(starts, reverse=True)
        assert all(h.evidence_precision == "segment" for h in result.hits)
    assert embedder.calls == []


def test_cortex_down_falls_back_to_labeled_lexical_with_filters() -> None:
    embedder = FakeEmbedder()
    embedder.down = True
    service = make(FakeReader(), embedder)
    result = run(service.search(request(query="puddles", cameras=["frontyard_1"])))
    assert result.mode == "lexical" and result.degraded_reason == "embedding_unavailable"
    assert result.profile_id is None
    assert names(result) and all(h.camera_id == "frontyard_1" for h in result.hits)
    unconfigured = run(make(FakeReader(), None).search(request(query="puddles")))
    assert unconfigured.mode == "lexical"
    assert unconfigured.degraded_reason == "embedding_unavailable"


def test_no_lexical_terms_is_semantic_only_or_empty_never_browse() -> None:
    reader = FakeReader()
    stop_words = "the and of"
    QUERIES["stop"] = (stop_words, QUERIES["snow"][1])
    try:
        semantic = run(make(reader, FakeEmbedder()).search(request(query=stop_words)))
    finally:
        del QUERIES["stop"]
    assert semantic.mode == "hybrid_exact" and semantic.hits
    down = FakeEmbedder()
    down.down = True
    empty = run(make(reader, down).search(request(query="!!! ...")))
    assert empty.mode == "lexical" and empty.degraded_reason == "lexical_no_terms"
    assert empty.hits == [] and empty.count == 0


def test_window_hit_cites_its_hour_and_whole_video_measurements_separately() -> None:
    reader = FakeReader()
    rain = next(d for d in reader.docs if NAMES[d["id"]] == "rain_front")
    reader.aggregates[(rain["video_start"], rain["video_end"])] = aggregate(
        rain["video_start"], rain["video_end"], maximum=14.0
    )
    down = FakeEmbedder()
    down.down = True
    gust = {"metric": "wind_gust", "statistic": "max", "op": ">=", "value": 10, "unit": "m/s"}
    result = run(
        make(reader, down).search(request(**JULY, query="hailstones", predicates=[gust]))
    )
    (hit,) = result.hits
    assert hit.evidence_precision == "interval"
    assert hit.source_interval.start == utc("2026-07-03T20:00:00Z")
    assert hit.source_interval.end == utc("2026-07-03T21:00:00Z")
    assert hit.seek is not None and hit.seek.media_seconds == 100.0
    assert hit.seek.source_at == hit.source_interval.start
    (measured,) = hit.matched_measurements
    assert (measured.interval.start, measured.interval.end) == (
        rain["video_start"],
        rain["video_end"],
    )
    assert measured.value == 14.0 and measured.unit == "m/s"


def test_strict_predicates_return_fewer_hits_never_loosened() -> None:
    reader = FakeReader()
    rain = next(d for d in reader.docs if NAMES[d["id"]] == "rain_front")
    reader.aggregates[(rain["video_start"], rain["video_end"])] = aggregate(
        rain["video_start"], rain["video_end"], total=6.0
    )
    wet = {"metric": "precipitation", "statistic": "sum", "op": ">", "value": 5, "unit": "mm"}
    result = run(make(reader, FakeEmbedder()).search(request(**JULY, predicates=[wet])))
    # Both rain videos share the interval and its station aggregate.
    assert sorted(names(result)) == ["rain_back", "rain_front"]
    dry = {**wet, "value": 50}
    assert run(make(reader, FakeEmbedder()).search(request(**JULY, predicates=[dry]))).hits == []


def test_withdrawal_during_a_request_drops_on_rehydration() -> None:
    reader = FakeReader()
    clear = next(d for d in reader.docs if NAMES[d["id"]] == "clear")

    def withdraw() -> None:
        clear["visibility"] = "withdrawn"

    reader.before_hydrate = withdraw
    result = run(make(reader, FakeEmbedder()).search(request(query="clear sunny sky")))
    assert "clear" not in names(result)
    assert result.count == len(result.hits) and [h.rank for h in result.hits] == list(
        range(1, len(result.hits) + 1)
    )


# Embedding boundary ----------------------------------------------------------------------


def test_slow_embedding_holds_no_connection_and_degrades_at_its_deadline() -> None:
    reader = FakeReader()
    embedder = FakeEmbedder(reader, delay=1.6)
    started = time.monotonic()
    result = run(make(reader, embedder).search(request(query="clear sunny sky")))
    elapsed = time.monotonic() - started
    assert embedder.open_during_call == [0]
    assert result.mode == "lexical" and result.degraded_reason == "embedding_unavailable"
    assert 1.4 < elapsed < 3


def test_embedding_within_its_deadline_still_precedes_any_connection() -> None:
    reader = FakeReader()
    embedder = FakeEmbedder(reader, delay=1.4)
    result = run(make(reader, embedder).search(request(query="clear sunny sky")))
    assert embedder.open_during_call == [0] and reader.sessions == 1
    assert result.mode == "hybrid_exact"


class Held:
    """A Cortex stand-in that keeps the per-pod slot until released."""

    def __init__(self, vector: list[float]) -> None:
        self.vector, self.started, self.release = vector, asyncio.Event(), asyncio.Event()

    async def __call__(self, req: httpx2.Request) -> httpx2.Response:
        self.started.set()
        await self.release.wait()
        return httpx2.Response(200, json=body([self.vector]))


def test_waiting_for_the_slot_is_busy_not_a_cortex_failure() -> None:
    async def scenario() -> tuple[Any, Any]:
        held = Held([1.0] + [0.0] * 2559)
        embedder = client(held)
        first = asyncio.create_task(embedder.query("rain", timeout=5))
        await held.started.wait()
        with pytest.raises(EmbeddingUnavailable) as caught:
            await embedder.query("snow", timeout=0.1)
        held.release.set()
        return caught.value.reason, await first

    reason, vector = run(scenario())
    assert reason == "busy" and len(vector) == 2560


def test_a_second_text_search_behind_the_slot_is_labelled_embedding_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weather.api.search as search_module

    monkeypatch.setattr(search_module, "EMBED_SECONDS", 0.2)

    async def scenario() -> Any:
        embedder = client(Held(QUERIES["clear"][1]))
        service = make(FakeReader(), embedder)
        # Another search holds the pod's slot for longer than this search's whole budget.
        # (Racing two real searches made their equal deadlines expire microseconds apart.)
        await embedder._slot.acquire()
        try:
            return await service.search(request(query="puddles"))
        finally:
            embedder._slot.release()

    second = run(scenario())
    assert second.mode == "lexical" and second.degraded_reason == "embedding_busy"
    assert names(second)  # lexical results are still served


def body(vectors: list[list[float]], **extra: Any) -> dict[str, Any]:
    return {"data": [{"index": i, "embedding": v, **extra} for i, v in enumerate(vectors)]}


def client(handler: Any) -> Embedder:
    transport = httpx2.MockTransport(handler)
    return Embedder("https://cortex.invalid/v1", "k", PROFILE, transport=transport)


def test_query_bytes_are_prefixed_and_otherwise_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    unit = [1.0] + [0.0] * 2559

    def handler(req: httpx2.Request) -> httpx2.Response:
        auth = req.headers["authorization"]
        seen.append({"url": str(req.url), "auth": auth, "json": json.loads(req.content)})
        return httpx2.Response(200, json=body([unit]))

    embedder = client(handler)
    for query in (" leading space", "café", "café"):
        assert run(embedder.query(query, timeout=1.5)) == unit
    assert [s["json"]["input"] for s in seen] == [
        [PROFILE.query_prefix + q] for q in (" leading space", "café", "café")
    ]
    assert PROFILE.query_prefix.endswith("\nQuery:")
    assert seen[0]["url"] == "https://cortex.invalid/v1/embeddings"
    assert seen[0]["json"]["model"] == "embed.main" and seen[0]["auth"] == "Bearer k"


@pytest.mark.parametrize(
    "data",
    [
        [],  # wrong count
        [{"index": 1, "embedding": [1.0] + [0.0] * 2559}],  # wrong position
        [{"index": 0, "embedding": [1.0] * 2559}],  # wrong dimension
        [{"index": 0, "embedding": [float("nan")] + [0.0] * 2559}],
        [{"index": 0, "embedding": [float("inf")] + [0.0] * 2559}],
        [{"index": 0, "embedding": [0.0] * 2560}],  # zero norm
        [{"index": 0, "embedding": [0.5] + [0.0] * 2559}],  # not normalized
        [{"index": 0, "embedding": [True] + [0.0] * 2559}],
        None,
    ],
)
def test_invalid_vectors_are_rejected(data: Any) -> None:
    with pytest.raises(EmbeddingUnavailable):
        validate(data, 1, 2560)


def test_provider_failures_are_sanitized_single_attempts() -> None:
    calls = 0

    def failing(req: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500, text="secret echo of the query")

    with pytest.raises(EmbeddingUnavailable) as caught:
        run(client(failing).query("rain", timeout=1.5))
    assert calls == 1 and "query" not in str(caught.value)

    def nonfinite(req: httpx2.Request) -> httpx2.Response:
        values = ",".join(["NaN"] * 2560)
        return httpx2.Response(200, content=f'{{"data":[{{"index":0,"embedding":[{values}]}}]}}')

    with pytest.raises(EmbeddingUnavailable):
        run(client(nonfinite).query("rain", timeout=1.5))


# SQL boundary ----------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement: Any, parameters: Any = None) -> Any:
        self.sql.append(str(statement))

        class Result:
            def mappings(self) -> Any:
                class Rows:
                    def all(self) -> list[dict[str, int]]:
                        return [{"terms": 1}]

                return Rows()

        return Result()


def test_each_statement_gets_the_smaller_remaining_budget_and_never_resets_it() -> None:
    recorder = Recorder()
    session = SearchSession(recorder, time.monotonic() + 0.3)  # type: ignore[arg-type]
    run(session.terms("rain"))
    found = re.search(r"statement_timeout = (\d+)", recorder.sql[0])
    assert found is not None
    limit = int(found.group(1))
    assert 0 < limit <= 300
    spent = SearchSession(recorder, time.monotonic() - 0.01)  # type: ignore[arg-type]
    with pytest.raises(SourceUnavailable):
        run(spent.terms("rain"))
    assert len(recorder.sql) == 2  # the exhausted budget ran nothing


def test_user_text_only_ever_binds_as_a_parameter() -> None:
    assert "plainto_tsquery('pg_catalog.english', CAST(:text AS text))" in TERMS_SQL
    assert "public_api.search_lexical(CAST(:text AS text)" in LEXICAL_SQL
    spec = importlib.util.spec_from_file_location(
        "migration_0004", ROOT / "app/migrations/versions/0004_search.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    body = migration.LEXICAL_BODY
    assert "pg_catalog.plainto_tsquery('pg_catalog.english', $1)" in body
    assert "to_tsquery" not in body.replace("plainto_tsquery", "")
    assert "ts_rank_cd(d.lexical, pg_catalog.plainto_tsquery" in body and "DESC, d.id" in body
    assert "d.visibility='public'" in body and "LIMIT least($8, 200)" in body
    assert "v.profile_id=:profile" in SEMANTIC_SQL and "LIMIT 200" in SEMANTIC_SQL
    assert "d.source_end > :start AND d.source_start < :end" in FILTER


def test_sql_failure_or_deadline_is_unavailable_never_empty_success() -> None:
    for fail_at in (1, 2, 3):
        reader = FakeReader()
        reader.fail_at = fail_at
        with pytest.raises(PublicError) as caught:
            run(make(reader, FakeEmbedder()).search(request(query="clear sunny sky")))
        assert caught.value.code == "unavailable"


# Predicates ------------------------------------------------------------------------------


def aggregate(start: datetime, end: datetime, coverage: float = 1.0, **values: Any) -> dict:
    row = {"total": None, "maximum": None, "minimum": None, "mean": None, **values}
    seconds = (end - start).total_seconds()
    return {
        **row,
        "bin_start": start,
        "bin_end": end,
        "support_seconds": seconds * coverage,
        "evidence_count": 10,
        "evidence_hash": "0" * 64,
    }


S, E = datetime(2026, 7, 3, 11, tzinfo=UTC), datetime(2026, 7, 4, 3, 30, tzinfo=UTC)


def check(predicate: dict[str, Any], row: dict[str, Any]) -> bool:
    return threshold(Predicate.model_validate(predicate)).matches(row) is not None


@pytest.mark.parametrize(
    "metric, statistic, column, canonical, units",
    [
        ("precipitation", "sum", "total", 12.7, [(0.5, "in"), (12.7, "mm")]),
        ("temperature", "max", "maximum", 10.0, [(50, "degF"), (10, "degC")]),
        ("temperature", "min", "minimum", -40.0, [(-40, "degF"), (-40, "degC")]),
        ("wind_gust", "max", "maximum", 22.352, [(50, "mph"), (22.352, "m/s"), (80.4672, "km/h")]),
    ],
)
def test_units_are_equivalent_and_boundaries_touch_exactly(
    metric: str, statistic: str, column: str, canonical: float, units: list[tuple[float, str]]
) -> None:
    # A sum of reports carries binary error; comparison is at 1e-6 resolution.
    row = aggregate(S, E, **{column: canonical + 3e-15})
    for value, unit in units:
        base = {"metric": metric, "statistic": statistic, "value": value, "unit": unit}
        assert check({**base, "op": ">="}, row) and check({**base, "op": "="}, row)
        assert check({**base, "op": "<="}, row)
        assert not check({**base, "op": ">"}, row) and not check({**base, "op": "<"}, row)


def test_zero_and_missing_rain_differ_and_low_coverage_never_matches() -> None:
    none = {"metric": "precipitation", "statistic": "sum", "op": "=", "value": 0, "unit": "mm"}
    assert check(none, aggregate(S, E, total=0.0))
    assert not check(none, aggregate(S, E, total=None))
    assert not check(none, aggregate(S, E, coverage=0.5, total=0.0))
    assert not check({**none, "op": "<"}, aggregate(S, E, coverage=0.0, total=None))
    assert not check(none, None)  # type: ignore[arg-type]
    loose = {**none, "min_coverage": 0.5}
    assert check(loose, aggregate(S, E, coverage=0.5, total=0.0))


def test_sparse_gusts_do_not_qualify_as_a_maximum() -> None:
    gust = {"metric": "wind_gust", "statistic": "max", "op": "<", "value": 5, "unit": "m/s"}
    assert check(gust, aggregate(S, E, maximum=3.0))
    assert not check(gust, aggregate(S, E, coverage=0.2, maximum=3.0))


# Request bounds and HTTP mapping ---------------------------------------------------------


def http(service: SearchService | None = None) -> TestClient:
    return TestClient(build_app(search=service or make(FakeReader(), FakeEmbedder())))


PREDICATE = {"metric": "precipitation", "statistic": "sum", "op": ">", "value": 1, "unit": "mm"}


@pytest.mark.parametrize(
    "extra",
    [
        {"query": "x" * 513},
        {"query": "rain\u0000"},
        {"query": "rain\ud800"},
        {"predicates": [PREDICATE] * 9},
        {"top_k": 21},
        {"top_k": 0},
        {"unknown": 1},
        {"predicates": [{**PREDICATE, "unit": "furlong"}]},
        {"predicates": [{**PREDICATE, "statistic": "mean"}]},
        {"predicates": [{**PREDICATE, "metric": "humidity"}]},
        {"predicates": [{**PREDICATE, "op": "!="}]},
        {"predicates": [{**PREDICATE, "min_coverage": 0}]},
        {"start": "2026-07-01T00:00:00+00:00"},
        {"end": "2026-01-01T00:00:00Z"},
        {"predicates": [PREDICATE], "start": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_requests_are_400_problems(extra: dict[str, Any]) -> None:
    # Raw JSON keeps the escaped lone surrogate that a client library would refuse to send.
    response = http().post(
        "/api/v1/search",
        content=json.dumps({**WINDOW, **extra}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    assert "x" * 20 not in response.text and "rain" not in response.text


def test_ceilings_are_inclusive_and_responses_are_no_store() -> None:
    response = http().post(
        "/api/v1/search",
        json={
            **WINDOW,
            "start": "2026-07-01T00:00:00Z",
            "end": "2026-07-31T00:00:00Z",
            "query": "y" * 512,
            "predicates": [PREDICATE] * 8,
            "top_k": 20,
        },
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["count"] == 0


def test_oversized_or_unsized_bodies_are_refused_before_parsing() -> None:
    headers = {"content-type": "application/json"}
    valid = json.dumps({**WINDOW, "query": "rain"})
    assert http().post("/api/v1/search", content=valid, headers=headers).status_code == 200
    # Whitespace keeps every field valid; only the size exceeds 16 KiB.
    padded = valid.replace(",", "," + " " * 9000)
    assert len(padded.encode()) > 16 * 1024
    assert json.loads(padded) == json.loads(valid)
    response = http().post("/api/v1/search", content=padded, headers=headers)
    assert response.status_code == 400 and response.json()["code"] == "invalid_request"

    def chunks() -> Any:
        yield valid.encode()

    # A generator body is sent chunked, without Content-Length.
    chunked = http().post("/api/v1/search", content=chunks(), headers=headers)
    assert chunked.request.headers.get("transfer-encoding") == "chunked"
    assert "content-length" not in chunked.request.headers
    assert chunked.status_code == 400


def test_busy_is_429_with_retry_after_and_failures_are_503() -> None:
    reader = FakeReader()
    service = make(reader, FakeEmbedder(delay=0.5))

    async def saturate() -> list[Any]:
        return await asyncio.gather(
            *(service.search(request(query="clear sunny sky")) for _ in range(4)),
            return_exceptions=True,
        )

    outcomes = run(saturate())
    rejected = [o for o in outcomes if isinstance(o, PublicError)]
    assert len(rejected) == 2 and all(o.code == "busy" for o in rejected)
    blocked = make(reader, FakeEmbedder())
    blocked._waiting = 4
    response = http(blocked).post("/api/v1/search", json={**WINDOW, "query": "rain"})
    assert response.status_code == 429 and response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    reader.fail_at = 1
    failed = http(make(reader, FakeEmbedder())).post("/api/v1/search", json=WINDOW)
    assert failed.status_code == 503 and failed.json()["code"] == "unavailable"
    assert http(None).post("/api/v1/search", json=WINDOW).status_code == 200
    no_service = TestClient(build_app()).post("/api/v1/search", json=WINDOW)
    assert no_service.status_code == 503


# Documents -------------------------------------------------------------------------------


def test_canonical_text_has_only_the_six_public_fields() -> None:
    spec = FIXTURE["documents"][2]
    rows = documents(video_text(spec))
    lines = rows[0]["text"].splitlines()
    assert [line.split(":", 1)[0] for line in lines] == [
        "Camera",
        "Interval",
        "Segment",
        "Analysis mode",
        "Observed conditions",
        "Evidence quality",
    ]
    assert lines[1] == "Interval: 2026-07-03T11:00:00Z to 2026-07-04T03:30:00Z"
    assert [r["kind"] for r in rows] == ["video", "window", "window"]
    assert [r["index_status"] for r in rows] == ["pending", "lexical", "lexical"]
    # The description interface has no slot for private editorial state.
    fields = set(VideoText.__dataclass_fields__) | set(WindowText.__dataclass_fields__)
    assert not {f for f in fields if re.search(r"favorite|note|operator|original", f)}
    with pytest.raises(ValueError):
        documents(video_text({**spec, "observed_conditions": "x" * 2001}))
    with pytest.raises(ValueError):
        documents(video_text({**spec, "camera_id": "Front Yard"}))


# Deployment wiring -----------------------------------------------------------------------


def test_cortex_key_is_read_only_in_the_api_and_search_has_per_client_edge_limits() -> None:
    import yaml

    search = ROOT / "deploy/k8s/search"
    component = yaml.safe_load((search / "kustomization.yaml").read_text())
    assert component["kind"] == "Component"
    assert component["patches"] == [
        {"path": "api-cortex.yaml", "target": {"kind": "Deployment", "name": "api"}}
    ]
    users = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "deploy").rglob("*.yaml")
        if "weather-cortex" in path.read_text()
    ]
    assert users == ["deploy/k8s/search/api-cortex.yaml"]
    pod = yaml.safe_load((search / "api-cortex.yaml").read_text())["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert container["name"] == "api"
    assert container["env"] == [
        {"name": "WEATHER_CORTEX_BASE_URL", "value": "https://cortex.vallery.net/v1"}
    ]
    assert container["volumeMounts"] == [
        {"name": "cortex", "mountPath": "/var/run/weather-cortex", "readOnly": True}
    ]
    assert pod["volumes"][0]["secret"]["defaultMode"] == 0o440
    assert pod["volumes"][0]["secret"]["optional"] is True
    policy = yaml.safe_load((search / "cortex-egress.yaml").read_text())
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "api"
    assert policy["spec"]["policyTypes"] == ["Egress"]
    ports = {p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]}
    assert ports == {53, 443, 8443}
    ingress = list(yaml.safe_load_all((search / "ingress.yaml").read_text()))
    middlewares = {m["metadata"]["name"]: m["spec"] for m in ingress if m["kind"] == "Middleware"}
    for spec in middlewares.values():
        (limit,) = spec.values()
        assert limit["sourceCriterion"] == {"requestHeaderName": "Cf-Connecting-Ip"}
    assert set(middlewares) == {"weather-search-rate-limit", "weather-search-in-flight"}
    (route,) = next(m for m in ingress if m["kind"] == "IngressRoute")["spec"]["routes"]
    assert "Path(`/api/v1/search`)" in route["match"] and route["priority"] > 100
    assert [m["name"] for m in route["middlewares"]][:2] == sorted(middlewares, reverse=True)


# Search-integrity vectors -----------------------------------------------------------------

COVERED = {
    "SI-01": "test_only_the_active_profile_ranks",
    "SI-02": "test_query_bytes_are_prefixed_and_otherwise_unchanged",
    "SI-03": "test_empty_or_whitespace_query_browses_newest_without_the_model",
    "SI-04": "test_invalid_vectors_are_rejected",
    "SI-05": "test_revision_withdraws_old_text_and_late_jobs_cannot_resurrect_it",
    # Measurements are never in document text: a late row changes only the next read.
    "SI-06": "test_predicates_units_zero_missing_coverage_dst_and_late_rows",
    "SI-07": "test_revision_withdraws_old_text_and_late_jobs_cannot_resurrect_it",
    "SI-08": "test_canonical_text_has_only_the_six_public_fields",
    "SI-09": "test_zero_and_missing_rain_differ_and_low_coverage_never_matches",
    "SI-10": "test_units_are_equivalent_and_boundaries_touch_exactly",
    "SI-11": "test_window_hit_cites_its_hour_and_whole_video_measurements_separately",
    "SI-12": "test_rrf_scores_collapse_to_the_best_document_per_video",
    "SI-13": "test_cortex_down_falls_back_to_labeled_lexical_with_filters",
    "SI-14": "test_strict_predicates_return_fewer_hits_never_loosened",
    "SI-17": "test_recorded_fixture_ranks_scene_classes_with_exact_cosine_and_fts",
    "SI-18": "test_sql_failure_or_deadline_is_unavailable_never_empty_success",
    "SI-19": "test_lexical_is_plain_english_and_never_operators_or_sql",
    "SI-20": "test_no_lexical_terms_is_semantic_only_or_empty_never_browse",
    "SI-22": "test_exhausted_sql_budget_is_unavailable_and_the_pool_recovers",
}


def test_search_integrity_vectors_map_to_tests() -> None:
    cases = json.loads((ROOT / "docs/contracts/search-integrity-vectors.json").read_text())
    obsolete = {c["id"] for c in cases["cases"] if "obsolete" in c}
    assert obsolete == {"SI-15", "SI-16", "SI-23"}
    assert set(COVERED) <= {c["id"] for c in cases["cases"]} - obsolete
    sources = "".join(
        (Path(__file__).parent / name).read_text()
        for name in ("test_search.py", "test_search_integration.py")
    )
    for case, name in COVERED.items():
        assert f"def {name}(" in sources, case
