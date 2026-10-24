# SPDX-License-Identifier: MIT
"""Hybrid search (#25): exact cosine, English full text and typed whole-video predicates.

Order of work per request:
1. Validate (400) and admit (one in-process semaphore; 429 when busy).
2. Embed nonempty query text through Cortex, holding no database connection (≤1.5 s, one
   attempt). Failure degrades to labeled lexical search; empty text never calls the model.
3. One READ ONLY transaction (1.5 s cumulative, 1 s per statement): whole-video predicates on
   the on-read aggregates, then up to 200 candidates per branch under every filter, fused by
   reciprocal rank, then re-hydration so a document withdrawn meanwhile drops out.

Query text never reaches logs, metrics labels or SQL text; it is always a bind parameter.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any
from uuid import UUID

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.search.cortex import Embedder, EmbeddingUnavailable, vector_text

from .models import (
    Coverage,
    DegradedReason,
    Interval,
    MatchedMeasurement,
    Predicate,
    SearchHit,
    SearchMode,
    SearchRequest,
    SearchResults,
    Seek,
)
from .registry import (
    DEFAULT_CADENCES,
    REPORT_SECONDS,
    Cadence,
    Family,
    PublicError,
    entry,
    sourced,
    unavailable,
)
from .registry import Source as MetricSource
from .source import Row, SourceUnavailable, aggregate_sql

CANDIDATES = 200
RRF_K = 60
REQUEST_SECONDS = 5.0
EMBED_SECONDS = 1.5
SQL_SECONDS = 1.5
STATEMENT_MS = 1000
LOCK_MS = 100
# Two searches at a time per pod (the pool has four connections); a third waits ≤100 ms.
SLOTS = 2
WAITERS = 4
WAIT_SECONDS = 0.1
# Whole-video predicates aggregate telemetry on read; bound the scanned history.
PREDICATE_WINDOW = timedelta(days=31)
MAX_PREDICATE_VIDEOS = 1000
SNIPPET = 280
BACKLOG_SECONDS = 60
VALUE = {"sum": "total", "max": "maximum", "min": "minimum", "mean": "mean"}

FILTER = """d.source_end > :start AND d.source_start < :end
  AND (cardinality(CAST(:cameras AS text[])) = 0 OR d.camera_id = ANY(CAST(:cameras AS text[])))
  AND (cardinality(CAST(:segments AS text[])) = 0
    OR d.segment = ANY(CAST(:segments AS text[])))
  AND (NOT CAST(:restricted AS boolean) OR d.video_id = ANY(CAST(:videos AS uuid[])))"""
COLUMNS = """d.id,d.video_id,d.kind,d.camera_id,d.segment,d.video_start,d.video_end,
  d.source_start,d.source_end,d.media_start_seconds,d.media_end_seconds,d.media_revision,
  d.analysis_mode,d.poster_asset_id,d.text"""
# English plain-text parsing: user text never becomes tsquery operators or SQL.
QUERY = "plainto_tsquery('pg_catalog.english', CAST(:text AS text))"
TERMS_SQL = f"SELECT numnode({QUERY}) AS terms"
VIDEOS_SQL = f"""SELECT DISTINCT d.video_id,d.video_start,d.video_end
FROM public_api.search_documents d WHERE d.kind='video' AND {FILTER} LIMIT :cap"""
SEMANTIC_SQL = f"""SELECT d.id,d.video_id FROM public_api.search_documents d
JOIN public_api.search_vectors v ON v.document_id=d.id AND v.profile_id=:profile
WHERE d.kind='video' AND {FILTER}
ORDER BY v.embedding OPERATOR(public.<=>) CAST(CAST(:vector AS text) AS public.vector), d.id
LIMIT {CANDIDATES}"""
# A SECURITY DEFINER function (0004) so the partial GIN index serves `@@`; it applies the
# same public filter as the view, ranks by ts_rank_cd(…, 1) then document ID.
LEXICAL_SQL = f"""SELECT id,video_id FROM public_api.search_lexical(CAST(:text AS text),
  CAST(:start AS timestamptz),CAST(:end AS timestamptz),CAST(:cameras AS text[]),
  CAST(:segments AS text[]),CAST(:restricted AS boolean),CAST(:videos AS uuid[]),{CANDIDATES})"""
NEWEST_SQL = f"""SELECT {COLUMNS} FROM public_api.search_documents d
WHERE d.kind='video' AND {FILTER} ORDER BY d.source_start DESC, d.video_id LIMIT :top_k"""
HYDRATE_SQL = f"""SELECT {COLUMNS} FROM public_api.search_documents d
WHERE d.id = ANY(CAST(:ids AS uuid[]))"""
BACKLOG_SQL = """SELECT count(*) AS backlog FROM public_api.search_documents d
WHERE d.kind='video' AND NOT EXISTS (SELECT 1 FROM public_api.search_vectors v
  WHERE v.document_id=d.id AND v.profile_id=:profile)"""


def busy() -> PublicError:
    return PublicError("busy", "Search is busy; retry shortly.")


@dataclass(frozen=True)
class Filters:
    start: datetime
    end: datetime
    cameras: Sequence[str] = ()
    segments: Sequence[str] = ()
    # None: no predicates; otherwise only these videos may match.
    videos: Sequence[UUID] | None = None

    def params(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "cameras": sorted(set(self.cameras)),
            "segments": sorted(set(self.segments)),
            "restricted": self.videos is not None,
            "videos": list(self.videos or ()),
        }


class SearchSession:
    """Statements of one READ ONLY transaction, each bounded by what is left of the budget."""

    def __init__(self, connection: AsyncConnection, deadline: float) -> None:
        self._connection, self._deadline = connection, deadline

    async def _rows(self, sql: str, parameters: Mapping[str, Any]) -> list[Row]:
        remaining = int((self._deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise SourceUnavailable()
        limit = min(STATEMENT_MS, remaining)
        await self._connection.execute(text(f"SET LOCAL statement_timeout = {limit}"))
        result = await self._connection.execute(text(sql), dict(parameters))
        return [dict(row) for row in result.mappings().all()]

    async def terms(self, query: str) -> int:
        return int((await self._rows(TERMS_SQL, {"text": query}))[0]["terms"])

    async def videos(self, filters: Filters, cap: int) -> list[Row]:
        return await self._rows(VIDEOS_SQL, {**filters.params(), "cap": cap})

    async def measurements(
        self, source: MetricSource, intervals: Sequence[tuple[datetime, datetime]], cap: float
    ) -> list[Row]:
        """On-read aggregates (#14's SQL) over each whole-video interval."""
        start, end = min(lo for lo, _ in intervals), max(hi for _, hi in intervals)
        reach = timedelta(seconds=max(cap, REPORT_SECONDS))
        parameters: dict[str, Any] = {
            "start": start,
            "end": end,
            "fetch_start": start - reach,
            "fetch_end": end + reach,
            "los": [lo for lo, _ in intervals],
            "his": [hi for _, hi in intervals],
        }
        if source.support == "sample":
            parameters["cap"] = timedelta(seconds=cap)
        else:
            parameters["span"] = timedelta(seconds=REPORT_SECONDS)
        return await self._rows(
            aggregate_sql(source, day=False, wind=False, intervals=True), parameters
        )

    async def semantic(self, filters: Filters, vector: Sequence[float], profile: str) -> list[Row]:
        return await self._rows(
            SEMANTIC_SQL, {**filters.params(), "vector": vector_text(vector), "profile": profile}
        )

    async def lexical(self, filters: Filters, query: str) -> list[Row]:
        return await self._rows(LEXICAL_SQL, {**filters.params(), "text": query})

    async def newest(self, filters: Filters, top_k: int) -> list[Row]:
        return await self._rows(NEWEST_SQL, {**filters.params(), "top_k": top_k})

    async def hydrate(self, ids: Sequence[UUID]) -> list[Row]:
        return await self._rows(HYDRATE_SQL, {"ids": list(ids)})

    async def backlog(self, profile: str) -> int:
        return int((await self._rows(BACKLOG_SQL, {"profile": profile}))[0]["backlog"])


class SearchReader:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def session(self, seconds: float) -> AsyncIterator[SearchSession]:
        """One transaction; the whole block, including pool checkout, ends by `seconds`."""
        deadline = time.monotonic() + seconds
        try:
            async with asyncio.timeout(seconds), self._engine.connect() as connection:
                try:
                    async with connection.begin():
                        for statement in (
                            "SET TRANSACTION READ ONLY",
                            f"SET LOCAL lock_timeout = {LOCK_MS}",
                            "SET LOCAL max_parallel_workers_per_gather = 0",
                        ):
                            await connection.execute(text(statement))
                        yield SearchSession(connection, deadline)
                except BaseException:
                    # A cancelled or timed-out statement may still be running: never reuse it.
                    await connection.invalidate()
                    raise
        except (PublicError, SourceUnavailable):
            raise
        except Exception:
            # Never retain or log driver errors, SQL or parameters.
            raise SourceUnavailable() from None


def fuse(
    semantic: Sequence[tuple[UUID, UUID]], lexical: Sequence[tuple[UUID, UUID]]
) -> list[tuple[UUID, UUID]]:
    """RRF over (document, video) ranks; the best document per video, never a sum of windows.

    score = 1/(60 + semantic rank) + 1/(60 + lexical rank), ranks one-based, an absent branch
    contributing zero. Videos order by score, then video ID, then document ID.
    """
    scores: dict[UUID, Fraction] = {}
    videos: dict[UUID, UUID] = {}
    for branch in (semantic, lexical):
        for rank, (document, video) in enumerate(branch, start=1):
            scores[document] = scores.get(document, Fraction(0)) + Fraction(1, RRF_K + rank)
            videos[document] = video
    best: dict[UUID, tuple[Fraction, UUID]] = {}
    for document, score in scores.items():
        video = videos[document]
        held = best.get(video)
        if held is None or (-score, document) < (-held[0], held[1]):
            best[video] = (score, document)
    ordered = sorted(best.items(), key=lambda item: (-item[1][0], item[0], item[1][1]))
    return [(video, document) for video, (_, document) in ordered]


@dataclass(frozen=True)
class Threshold:
    """A predicate converted exactly to canonical units, compared at 1e-6 resolution."""

    predicate: Predicate
    source: MetricSource
    canonical: int

    def matches(self, row: Row | None) -> MatchedMeasurement | None:
        """Unknown or under-covered never matches, whatever the operator (even "= 0")."""
        if row is None:
            return None
        value = row.get(VALUE[self.predicate.statistic])
        if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
            return None
        start, end = row["bin_start"], row["bin_end"]
        expected = (end - start).total_seconds()
        support = min(float(row["support_seconds"] or 0), expected)
        if support / expected < self.predicate.min_coverage:
            return None
        measured = _micro(Fraction(float(value)))
        if not {
            "<": measured < self.canonical,
            "<=": measured <= self.canonical,
            "=": measured == self.canonical,
            ">=": measured >= self.canonical,
            ">": measured > self.canonical,
        }[self.predicate.op]:
            return None
        return MatchedMeasurement(
            metric=self.predicate.metric,
            statistic=self.predicate.statistic,
            value=round(float(value), 6),
            unit=entry(self.predicate.metric).unit,
            interval=Interval(start=start, end=end),
            coverage=Coverage(
                fraction=round(support / expected, 6),
                support_seconds=round(support, 6),
                expected_seconds=expected,
            ),
            evidence_count=int(row["evidence_count"]),
            evidence_hash=row["evidence_hash"],
        )


def _micro(value: Fraction) -> int:
    return round(value * 1_000_000)


def threshold(predicate: Predicate) -> Threshold:
    """Deterministic unit conversion: display = canonical * scale + offset, solved exactly."""
    found, source = sourced(predicate.metric)
    conversions = {found.unit: (Fraction(1), Fraction(0))}
    conversions.update({unit: (scale, offset) for unit, scale, offset in found.conversions})
    if predicate.unit not in conversions:
        raise PublicError("invalid_request", "Unsupported unit for this metric.")
    scale, offset = conversions[predicate.unit]
    # The decimal the client wrote, not its binary approximation.
    given = Fraction(Decimal(repr(predicate.value)))
    return Threshold(predicate, source, _micro((given - offset) / scale))


def snippet(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("Observed conditions: "):
            body = line.removeprefix("Observed conditions: ")
            break
    return body if len(body) <= SNIPPET else body[: SNIPPET - 1] + "…"


def _hit(rank: int, row: Row, measured: Sequence[MatchedMeasurement]) -> SearchHit:
    seek = None
    if row["media_start_seconds"] is not None:
        seek = Seek(media_seconds=row["media_start_seconds"], source_at=row["source_start"])
    return SearchHit(
        rank=rank,
        video_id=str(row["video_id"]),
        document_id=str(row["id"]),
        camera_id=row["camera_id"],
        segment=row["segment"],
        video_interval=Interval(start=row["video_start"], end=row["video_end"]),
        source_interval=Interval(start=row["source_start"], end=row["source_end"]),
        evidence_precision="segment" if row["kind"] == "video" else "interval",
        analysis_mode=row["analysis_mode"],
        snippet=snippet(row["text"]),
        media_revision=row["media_revision"],
        poster_asset_id=str(row["poster_asset_id"]) if row["poster_asset_id"] else None,
        seek=seek,
        matched_measurements=list(measured),
    )


class SearchMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.requests = Counter(
            "weather_search_requests_total",
            "Search requests by mode and outcome",
            ["mode", "outcome"],
            registry=registry,
        )
        self.latency = Histogram(
            "weather_search_duration_seconds", "Search latency by mode", ["mode"],
            registry=registry,
        )
        self.embedding_failures = Counter(
            "weather_search_embedding_failures_total",
            "Query embeddings that fell back to lexical search",
            ["reason"],
            registry=registry,
        )
        self.timeouts = Counter(
            "weather_search_timeouts_total", "Search deadlines exhausted", ["stage"],
            registry=registry,
        )
        self.backlog = Gauge(
            "weather_search_index_backlog",
            "Current video documents without a vector for the active profile",
            registry=registry,
        )
        for reason in ("busy", "timeout", "error", "invalid", "unconfigured"):
            self.embedding_failures.labels(reason)
        for stage in ("request", "sql"):
            self.timeouts.labels(stage)


class SearchService:
    def __init__(
        self,
        reader: SearchReader,
        embedder: Embedder | None,
        *,
        profile_id: str,
        cadences: Mapping[Family, Cadence] = DEFAULT_CADENCES,
        registry: CollectorRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.reader, self.embedder, self.profile_id = reader, embedder, profile_id
        self.cadences, self.now = dict(cadences), clock
        self.metrics = SearchMetrics(registry if registry is not None else CollectorRegistry())
        self._slots = asyncio.Semaphore(SLOTS)
        self._waiting = 0
        self._task: asyncio.Task[None] | None = None

    # Admission ---------------------------------------------------------------------------

    async def _admit(self) -> None:
        if self._waiting >= WAITERS:
            raise busy()
        self._waiting += 1
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                await self._slots.acquire()
        except TimeoutError:
            raise busy() from None
        finally:
            self._waiting -= 1

    async def search(self, request: SearchRequest) -> SearchResults:
        started = time.monotonic()
        mode = "none"
        try:
            thresholds = [threshold(p) for p in request.predicates]
            if thresholds and request.end - request.start > PREDICATE_WINDOW:
                raise PublicError(
                    "range_too_large", "Measured filters cover at most 31 days per search."
                )
            await self._admit()
            try:
                async with asyncio.timeout(REQUEST_SECONDS):
                    result = await self._search(request, thresholds)
            except TimeoutError:
                self.metrics.timeouts.labels("request").inc()
                raise unavailable() from None
            finally:
                self._slots.release()
        except PublicError as error:
            self.metrics.requests.labels(mode, error.code).inc()
            raise
        mode = result.mode
        self.metrics.requests.labels(mode, "degraded" if result.degraded_reason else "ok").inc()
        self.metrics.latency.labels(mode).observe(time.monotonic() - started)
        return result

    # Work --------------------------------------------------------------------------------

    async def _embed(self, query: str) -> tuple[list[float] | None, str | None]:
        """The query vector, or the failure reason (`busy` while the pod's slot is taken)."""
        if self.embedder is None:
            self.metrics.embedding_failures.labels("unconfigured").inc()
            return None, "unconfigured"
        try:
            return await self.embedder.query(query, timeout=EMBED_SECONDS), None
        except EmbeddingUnavailable as error:
            self.metrics.embedding_failures.labels(error.reason).inc()
            return None, error.reason

    async def _search(self, request: SearchRequest, thresholds: list[Threshold]) -> SearchResults:
        browse = not request.query.strip()
        # Before any connection is taken: no transaction spans the model call.
        vector, failure = (None, None) if browse else await self._embed(request.query)
        filters = Filters(request.start, request.end, request.cameras, request.segments)
        mode: SearchMode = "numeric" if browse else "hybrid_exact" if vector else "lexical"
        reason: DegradedReason | None = None
        ranked: list[tuple[int, Row]] = []
        measured: dict[UUID, list[MatchedMeasurement]] = {}
        try:
            async with self.reader.session(SQL_SECONDS) as session:
                if thresholds:
                    allowed, measured = await self._measure(session, filters, thresholds)
                    filters = Filters(
                        request.start, request.end, request.cameras, request.segments, allowed
                    )
                if browse:
                    if filters.videos != []:
                        rows = await session.newest(filters, request.top_k)
                        ranked = list(enumerate(rows, start=1))
                else:
                    terms = await session.terms(request.query)
                    if vector is None:
                        reason = (
                            "lexical_no_terms"
                            if not terms
                            else "embedding_busy"
                            if failure == "busy"
                            else "embedding_unavailable"
                        )
                    if filters.videos != []:
                        ranked = await self._rank(session, filters, request, vector, terms)
        except SourceUnavailable:
            self.metrics.timeouts.labels("sql").inc()
            raise unavailable() from None
        hits = [
            _hit(rank, row, measured.get(row["video_id"], ())) for rank, row in ranked
        ]
        return SearchResults(
            generated_at=self.now(),
            mode=mode,
            degraded_reason=reason,
            profile_id=self.profile_id if mode == "hybrid_exact" else None,
            count=len(hits),
            hits=hits,
        )

    async def _rank(
        self,
        session: SearchSession,
        filters: Filters,
        request: SearchRequest,
        vector: list[float] | None,
        terms: int,
    ) -> list[tuple[int, Row]]:
        semantic = (
            await session.semantic(filters, vector, self.profile_id) if vector is not None else []
        )
        lexical = await session.lexical(filters, request.query) if terms else []
        fused = fuse(
            [(row["id"], row["video_id"]) for row in semantic],
            [(row["id"], row["video_id"]) for row in lexical],
        )[: request.top_k]
        if not fused:
            return []
        # Re-hydrate: a document withdrawn since ranking drops out (fewer hits are honest).
        current = {row["id"]: row for row in await session.hydrate([d for _, d in fused])}
        kept = [current[document] for _, document in fused if document in current]
        return list(enumerate(kept, start=1))

    async def _measure(
        self, session: SearchSession, filters: Filters, thresholds: Sequence[Threshold]
    ) -> tuple[list[UUID], dict[UUID, list[MatchedMeasurement]]]:
        """Videos whose whole-segment aggregates satisfy every predicate, with the evidence."""
        videos = await session.videos(filters, MAX_PREDICATE_VIDEOS + 1)
        if len(videos) > MAX_PREDICATE_VIDEOS:
            raise PublicError("range_too_large", "Too many videos for measured filters.")
        if not videos:
            return [], {}
        intervals = sorted({(v["video_start"], v["video_end"]) for v in videos})
        aggregates: dict[tuple[str, datetime, datetime], Row] = {}
        for metric in sorted({t.predicate.metric for t in thresholds}):
            source = next(t.source for t in thresholds if t.predicate.metric == metric)
            cap = self.cadences[source.family].cap_seconds
            for row in await session.measurements(source, intervals, cap):
                aggregates[(metric, row["bin_start"], row["bin_end"])] = row
        allowed: list[UUID] = []
        measured: dict[UUID, list[MatchedMeasurement]] = {}
        for video in videos:
            matches = [
                t.matches(aggregates.get((t.predicate.metric, video["video_start"],
                                          video["video_end"])))
                for t in thresholds
            ]
            if all(match is not None for match in matches):
                allowed.append(video["video_id"])
                unique = {(m.metric, m.statistic): m for m in matches if m is not None}
                measured[video["video_id"]] = list(unique.values())
        return allowed, measured

    # Backlog signal ----------------------------------------------------------------------

    async def refresh_backlog(self) -> None:
        try:
            async with self.reader.session(SQL_SECONDS) as session:
                self.metrics.backlog.set(await session.backlog(self.profile_id))
        except SourceUnavailable:
            pass

    async def _loop(self) -> None:
        while True:
            await self.refresh_backlog()
            await asyncio.sleep(BACKLOG_SECONDS)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

