# SPDX-License-Identifier: MIT
"""The `intel` analysis jobs (#23): sampled frames + keogram -> structured visual observations
(Stage A), on-read aggregates -> measured packet (Stage B), both -> public text from fixed
templates (`render.py`, Stage C). No model-written prose is ever published (C18), and there is
no synthesis call: the only Cortex calls are one image request per hour window.

Budgets: one Cortex call in flight across processes (advisory lock), at most four calls per
window (first try, two transport retries, one repair; auth and media errors stop at once), and
a ceiling per queued describe that scheduler retries share (`attempt_calls`); once it is spent
the run publishes what its accepted windows support. Cortex being down, or the slot busy or
unreachable, leaves the video `pending` for the nightly retry, and a process-level breaker then
skips every further call until that nightly window; nothing here delays media publication.
Publication is a CAS on the head's media revision and current analysis revision, so a late
result never replaces a newer one. Every revision is immutable and pinned to its model, prompt,
template and schema hashes, evidence hash and media SHA-256.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any, TypeVar
from uuid import UUID, uuid4

from prometheus_client import Counter, Histogram
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.api.source import SourceUnavailable
from weather.events.rules import Candidate
from weather.events.store import record
from weather.scheduler.registry import JobContext, Registry
from weather.search import documents
from weather.search.documents import StaleRevision, VideoText, WindowText

from . import render, store, validate
from .client import MODEL_ROUTE, Cortex, CortexError, Reply, request_body
from .effective import LABEL, STATION_LABEL
from .evidence import AggregateSource, Packet, read_packet
from .media import MediaError, MediaReader, Source, StoredObject, WindowMedia
from .slot import CortexSlot, SlotBusy
from .validate import Observation, Rejected, VisualResult
from .windows import FPS, Window, hour_windows

LOG = logging.getLogger("weather.ai")
CONTRACTS = validate.CONTRACTS
VISUAL_PROMPT = (CONTRACTS / "visual-system.txt").read_text()
# The prompt hash also pins the public templates that turn observations into text.
PROMPT_SHA256 = hashlib.sha256(
    (CONTRACTS / "visual-system.txt").read_bytes() + b"\0" + render.RENDER_REVISION.encode()
).hexdigest()
SCHEMA_SHA256 = hashlib.sha256((CONTRACTS / "visual-analysis.schema.json").read_bytes()).hexdigest()
CALLS_PER_WINDOW = 4
TRANSPORT_ATTEMPTS = 3
REPAIRS = 1
MAX_CALLS_PER_RUN = 80
SLOT_WAIT_SECONDS = 600.0
BACKOFF_SECONDS = (5.0, 20.0)
VISUAL_TOKENS = 1024
EVIDENCE_LOOKBACK = timedelta(days=3)
EVIDENCE_INTERVAL = timedelta(minutes=15)
MAX_REFRESHES = 3
CAMERA_LABELS = {
    "skycam_east": "East sky camera",
    "skycam_west": "West sky camera",
    "frontyard_1": "Front yard camera",
}
SCENES = {
    "skycam_east": "Fixed sky camera looking east; mostly sky above a low horizon.",
    "skycam_west": "Fixed sky camera looking west; mostly sky above a low horizon.",
    "frontyard_1": "Fixed ground-level camera over a front yard with some sky.",
}
# Visual events from validated observations only (#41's model hook), with fixed labels.
EVENT_LABELS = {
    "rain": "Rain visible on camera",
    "snowfall": "Snowfall visible on camera",
    "lightning": "Lightning visible on camera",
    "fog_visibility": "Fog or reduced visibility on camera",
}
CALLS = Counter(
    "weather_ai_calls_total", "Cortex chat calls by stage and result", ["stage", "result"]
)
REJECTIONS = Counter(
    "weather_ai_rejections_total", "Model replies rejected by validation", ["stage", "code"]
)
OUTCOMES = Counter("weather_ai_outcomes_total", "Analysis run outcomes", ["outcome"])
LATENCY = Histogram(
    "weather_ai_call_seconds",
    "Cortex chat call latency",
    ["stage"],
    buckets=(1, 2, 5, 10, 20, 30, 60, 90, 120),
)
T = TypeVar("T")


class BudgetSpent(RuntimeError):
    """The head's call budget is spent: publish what the accepted windows support."""


class CortexDown(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Run:
    video_id: UUID
    nonce: str
    calls: int = 0
    model: str = MODEL_ROUTE
    # Describe runs spend from this publication's head budget, shared by the job's retries.
    publication_id: UUID | None = None


@dataclass
class Result:
    state: str
    reason: str | None
    claims: list[dict[str, Any]]
    limitations: list[str]
    observations: list[dict[str, Any]]
    coverage: dict[str, Any]
    inputs: dict[str, Any]
    packet: Packet
    refresh: str
    visual_id: UUID | None = None
    event_windows: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    # A re-render keeps the model, prompt and schema that produced its observations.
    model: str | None = None
    prompt_sha256: str | None = None
    schema_sha256: str | None = None


def nonce_for(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:8]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def frame_time(start: datetime, end: datetime, frames: int, index: int) -> datetime:
    step = Fraction(int((end - start).total_seconds() * 1_000_000), frames)
    return start + timedelta(microseconds=int(step * index + step / 2))


def visual_request(
    row: dict[str, Any], media: WindowMedia, nonce: str, note: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Stage A: frames, optional keogram columns and context; never a measurement."""
    window = media.window
    evidence: list[dict[str, Any]] = []
    images = []
    for index, image in media.frames:
        label = f"f-{nonce}-{window.index}-{index}"
        evidence.append(
            {
                "id": label,
                "kind": "frame",
                "source_time": _iso(
                    frame_time(row["starts_at"], row["ends_at"], row["frames"], index)
                ),
            }
        )
        images.append((label, image))
    if media.keogram is not None:
        label = f"k-{nonce}-{window.index}"
        evidence.append(
            {
                "id": label,
                "kind": "keogram",
                "note": "Columns run left to right through this source interval; each column "
                "is one vertical slice of the view.",
            }
        )
        images.append((label, media.keogram))
    caveats = ["Frames are sampled from the published web rendition, not the originals."]
    if row["segment"] == "overnight":
        caveats.append("Overnight footage: darkness and infrared imaging limit observation.")
    if row["video_state"] == "partial":
        caveats.append("Capture coverage is partial; some frames may show missing capture.")
    if media.keogram is None:
        caveats.append("No keogram is available for this interval.")
    context: dict[str, Any] = {
        "task": "visual_analysis",
        "camera": SCENES.get(row["camera_id"], "Fixed outdoor camera."),
        "segment": row["segment"],
        "source_interval": {"start": _iso(window.start), "end": _iso(window.end)},
        "evidence": evidence,
        "caveats": caveats,
        "schema": validate.schema(),
    }
    if note is not None:
        context["previous_reply_rejected"] = note
    body = request_body(
        VISUAL_PROMPT, json.dumps(context, ensure_ascii=False), images, max_tokens=VISUAL_TOKENS
    )
    return body, [item["id"] for item in evidence]


def coverage_record(
    row: dict[str, Any], windows: Sequence[Window], reviewed: set[int]
) -> dict[str, Any]:
    expected = (row["ends_at"] - row["starts_at"]).total_seconds()
    seconds = sum(w.seconds for w in windows if w.index in reviewed)
    return {
        "algorithm_revision": "hour-windows-v1",
        "reviewed_seconds": seconds,
        "expected_seconds": expected,
        "fraction": min(1.0, seconds / expected) if expected > 0 else 0.0,
        "windows_reviewed": len(reviewed),
        "windows_total": len(windows),
        "gaps": [
            {"start": _iso(w.start), "end": _iso(w.end)} for w in windows if w.index not in reviewed
        ],
    }


class Analyzer:
    def __init__(
        self,
        engine: AsyncEngine,
        cortex: Cortex,
        slot: CortexSlot,
        media: MediaReader,
        weather: AggregateSource,
        *,
        profile_id: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.cortex, self.slot = engine, cortex, slot
        self.media, self.weather, self.profile_id = media, weather, profile_id
        self.sleep, self.clock = sleep, clock
        # Process-level breaker: after Cortex is down, no call until the next nightly window.
        self._breaker: tuple[datetime, str] | None = None

    def breaker(self) -> str | None:
        """The reason Cortex calls are skipped until the next nightly window, if they are."""
        if self._breaker is not None and self.clock() >= self._breaker[0]:
            self._breaker = None
        return self._breaker[1] if self._breaker is not None else None

    def _trip(self, reason: str) -> CortexDown:
        self._breaker = (store.next_night(self.clock()), reason)
        return CortexDown(reason)

    async def _spend(self, run: Run) -> None:
        if run.publication_id is None:
            return
        try:
            async with self.engine.begin() as connection:
                spent = await store.spend_call(
                    connection, run.video_id, run.publication_id, MAX_CALLS_PER_RUN
                )
        except (SQLAlchemyError, OSError):
            raise CortexDown("slot_unavailable") from None
        if not spent:
            raise BudgetSpent("Call budget spent")

    # Calls --------------------------------------------------------------------------------------

    async def _raw(
        self,
        run: Run,
        stage: str,
        window_start: datetime | None,
        outcome: str,
        reply: Reply | None,
    ) -> None:
        try:
            async with self.engine.begin() as connection:
                await store.store_raw(
                    connection,
                    video_id=run.video_id,
                    stage=stage,
                    window_start=window_start,
                    call=run.calls,
                    outcome=outcome,
                    finish_reason=reply.finish_reason if reply else None,
                    body=reply.body if reply else None,
                )
        except Exception as error:
            # Diagnostics only; their loss never changes an outcome.
            LOG.warning("Raw response not stored (%s)", type(error).__name__)

    async def call(
        self,
        run: Run,
        build: Callable[[str | None], dict[str, Any]],
        accept: Callable[[Reply], T],
        *,
        stage: str,
        window_start: datetime | None = None,
    ) -> T | None:
        """At most four calls; None when this window or stage yields nothing usable.

        Raises CortexDown when Cortex is unreachable, busy or refuses the key (tripping the
        breaker), or when the inference slot stays busy or its database is unreachable, so the
        run is deferred to the nightly retry instead of spending more calls. Raises BudgetSpent
        when the head's call budget is used up.
        """
        failures = repairs = window_calls = 0
        note: str | None = None
        while window_calls < CALLS_PER_WINDOW and run.calls < MAX_CALLS_PER_RUN:
            reason = self.breaker()
            if reason is not None:
                raise CortexDown(reason)
            try:
                body = build(note)
            except CortexError:
                CALLS.labels(stage, "media").inc()
                return None
            reply: Reply | None = None
            failure: CortexError | None = None
            try:
                async with self.slot.hold(SLOT_WAIT_SECONDS) as uncertain:
                    await self._spend(run)
                    window_calls += 1
                    run.calls += 1
                    try:
                        with LATENCY.labels(stage).time():
                            reply = await self.cortex.chat(body)
                    except CortexError as error:
                        uncertain.value = error.timeout
                        failure = error
            except SlotBusy:
                raise CortexDown("slot_busy") from None
            except (SQLAlchemyError, OSError) as error:
                if reply is None and failure is None:
                    raise CortexDown("slot_unavailable") from None
                # Only the release failed: the slot dropped its session (and so the lock), and
                # the call's outcome still stands.
                LOG.warning("Inference slot release failed (%s)", type(error).__name__)
            if failure is not None:
                CALLS.labels(stage, failure.kind).inc()
                await self._raw(run, stage, window_start, failure.kind, None)
                if failure.kind == "auth":
                    raise self._trip("cortex_auth")
                if failure.kind == "media":
                    return None
                if failure.kind == "invalid":
                    if repairs >= REPAIRS:
                        return None
                    repairs, note = repairs + 1, "invalid_reply"
                    continue
                failures += 1
                if failures >= TRANSPORT_ATTEMPTS:
                    raise self._trip(
                        "cortex_busy" if failure.kind == "busy" else "cortex_unavailable"
                    )
                await self.sleep(failure.retry_after or BACKOFF_SECONDS[failures - 1])
                continue
            assert reply is not None
            try:
                result = accept(reply)
            except Rejected as rejection:
                CALLS.labels(stage, "rejected").inc()
                REJECTIONS.labels(stage, rejection.code).inc()
                await self._raw(run, stage, window_start, rejection.code, reply)
                if repairs >= REPAIRS:
                    return None
                repairs, note = repairs + 1, rejection.code
                continue
            CALLS.labels(stage, "accepted").inc()
            run.model = reply.model
            await self._raw(run, stage, window_start, "accepted", reply)
            return result
        return None

    # Jobs ---------------------------------------------------------------------------------------

    async def describe(self, context: JobContext) -> None:
        payload = context.job.payload
        video_id, publication_id = (
            UUID(str(payload["video_id"])),
            UUID(str(payload["publication_id"])),
        )
        async with self.engine.connect() as connection:
            row = await store.head(connection, video_id)
            assets = await store.media(connection, row) if row is not None else None
        if (
            row is None
            or assets is None
            or row["publication_id"] != publication_id
            or row["state"] != "pending"
            or row["withdrawn"]
        ):
            return
        now = self.clock()
        run = Run(
            video_id,
            nonce_for(
                video_id, publication_id, row["revision"], context.job.id, context.job.attempt
            ),
            publication_id=publication_id,
        )
        tripped = self.breaker()
        if tripped is not None:
            await self._defer(context, row, tripped, run, now)
            return
        web = assets["web"]
        if web is None or web.sha256 != row["media_sha256"]:
            await self._fail(context, row, "media_unavailable", run, now)
            return
        windows = hour_windows(row["starts_at"], row["ends_at"], row["frames"])
        keogram = assets["keogram"]
        source = Source(
            StoredObject(web.object_key, web.sha256, int(web.bytes)),
            StoredObject(keogram.object_key, keogram.sha256, int(keogram.bytes))
            if keogram is not None
            else None,
        )
        try:
            # A transient store error (MediaUnavailable) raises: the job retries with backoff
            # and the head stays pending. Only definitely missing or invalid media fails it.
            prepared = await self.media.prepare(source, windows)
        except MediaError as error:
            await self._fail(context, row, f"media_{error.reason}"[:32], run, now)
            return
        # A telemetry read failure raises: the job retries with backoff.
        packet = await read_packet(self.weather, run.nonce, row["starts_at"], row["ends_at"])
        observations: list[Observation] = []
        codes: dict[str, int] = {}
        reviewed: set[int] = set()
        spent = False
        bounds = {w.index: (w.start, w.end) for w in windows}
        try:
            for media in prepared:
                ids: list[str] = []

                def build(
                    note: str | None, media: WindowMedia = media, ids: list[str] = ids
                ) -> dict[str, Any]:
                    body, supplied = visual_request(row, media, run.nonce, note)
                    ids[:] = supplied
                    return body

                def accept(
                    reply: Reply, window: Window = media.window, ids: list[str] = ids
                ) -> VisualResult:
                    return validate.visual(
                        reply.content,
                        reply.finish_reason,
                        supplied=ids,
                        window=window.index,
                        nonce=run.nonce,
                    )

                try:
                    visual = await self.call(
                        run, build, accept, stage="visual", window_start=media.window.start
                    )
                except BudgetSpent:
                    # Scheduler retries spent the head's budget: keep the accepted windows.
                    spent = True
                    break
                if visual is not None:
                    observations.extend(visual.observations)
                    for code in visual.limitations:
                        codes[code] = codes.get(code, 0) + 1
                    reviewed.add(media.window.index)
        except CortexDown as down:
            await self._defer(context, row, down.reason, run, now)
            return
        result = self._result(
            row,
            packet,
            observations,
            coverage_record(row, windows, reviewed),
            {
                "rendition": "web",
                "frames": sum(len(m.frames) for m in prepared),
                "windows": len(prepared),
                "keogram": any(m.keogram is not None for m in prepared),
                "limitations": codes,
                "call_budget_spent": spent,
            },
            refresh="full",
        )
        result.event_windows = {o.id: bounds[o.window] for o in observations}
        if result.state == "failed":
            await self._fail(context, row, result.reason or "no_evidence", run, now)
            return
        await self._publish(context, row, run, result, base=row["revision"], describe=True)

    def _result(
        self,
        row: dict[str, Any],
        packet: Packet,
        observations: Sequence[Observation],
        coverage: dict[str, Any],
        inputs: dict[str, Any],
        *,
        refresh: str,
    ) -> Result:
        """Render the public claims and limitations (C18) and choose the state."""
        reviewed = int(coverage.get("windows_reviewed", 0))
        available = int(inputs.get("windows", 0))
        claims = render.claims(
            packet, observations, reviewed, row["camera_id"], inputs.get("limitations", {})
        )
        limitations = render.limitations(
            observations,
            reviewed,
            inputs.get("limitations", {}),
            keogram=bool(inputs.get("keogram")),
            unreviewed=reviewed < int(coverage.get("windows_total", 0)),
            withheld=packet.withheld,
        )
        if not claims:
            state, reason = "failed", "no_evidence"
        elif reviewed and reviewed == available:
            state, reason = "complete", None
        else:
            state = "degraded"
            reason = (
                "call_budget"
                if inputs.get("call_budget_spent")
                else "visual_partial"
                if reviewed
                else "visual_invalid"
            )
        return Result(
            state=state,
            reason=reason,
            claims=claims,
            limitations=limitations,
            observations=[o.record() for o in observations],
            coverage=coverage,
            inputs=inputs,
            packet=packet,
            refresh=refresh,
        )

    async def synthesize(self, context: JobContext) -> None:
        """Re-render after the measured evidence changed: Stage A is reused and no model is
        called, so a refresh never waits on Cortex or the breaker."""
        payload = context.job.payload
        video_id = UUID(str(payload["video_id"]))
        publication_id = UUID(str(payload["publication_id"]))
        base = int(payload["base_revision"])
        async with self.engine.connect() as connection:
            row = await store.head(connection, video_id)
            previous = (
                (
                    await connection.execute(
                        text("""SELECT id,visual_id,evidence_hash,observations,coverage,inputs,
                          author_kind,model,prompt_sha256,schema_sha256
                        FROM weather.analysis_revision
                        WHERE video_id=:video AND revision=:revision"""),
                        {"video": video_id, "revision": base},
                    )
                )
                .mappings()
                .first()
            )
        if (
            row is None
            or previous is None
            or row["publication_id"] != publication_id
            or row["revision"] != base
            or row["state"] not in ("complete", "degraded")
            or row["withdrawn"]
            or previous["author_kind"] != "model"
        ):
            return
        run = Run(
            video_id, nonce_for(video_id, publication_id, base, context.job.id, context.job.attempt)
        )
        packet = await read_packet(self.weather, run.nonce, row["starts_at"], row["ends_at"])
        if packet.evidence_hash == previous["evidence_hash"]:

            async def unchanged(connection: AsyncConnection) -> None:
                await connection.execute(
                    text("""UPDATE weather.analysis SET stale=false WHERE video_id=:video
                      AND revision=:revision"""),
                    {"video": video_id, "revision": base},
                )

            await context.commit(unchanged)
            return
        records = list(previous["observations"])
        observations = [Observation.from_record(r) for r in records]
        result = self._result(
            row,
            packet,
            observations,
            dict(previous["coverage"]),
            dict(previous["inputs"]),
            refresh="measurements",
        )
        if result.state == "failed":
            return
        result.visual_id = previous["visual_id"]
        result.model = previous["model"]
        result.prompt_sha256 = previous["prompt_sha256"]
        result.schema_sha256 = previous["schema_sha256"]
        result.event_windows = {r["id"]: (_parse(r["start"]), _parse(r["end"])) for r in records}
        await self._publish(context, row, run, result, base=base, describe=False)

    # Writes -------------------------------------------------------------------------------------

    async def _defer(
        self, context: JobContext, row: dict[str, Any], reason: str, run: Run, now: datetime
    ) -> None:
        async def write(connection: AsyncConnection) -> None:
            await store.defer(
                connection,
                row["video_id"],
                row["publication_id"],
                reason=reason,
                calls=run.calls,
                now=now,
            )

        await context.commit(write)
        OUTCOMES.labels("pending").inc()

    async def _fail(
        self, context: JobContext, row: dict[str, Any], reason: str, run: Run, now: datetime
    ) -> None:
        async def write(connection: AsyncConnection) -> None:
            await store.fail(
                connection,
                row["video_id"],
                row["publication_id"],
                reason=reason,
                calls=run.calls,
                now=now,
            )

        await context.commit(write)
        OUTCOMES.labels("failed").inc()

    async def _publish(
        self,
        context: JobContext,
        row: dict[str, Any],
        run: Run,
        result: Result,
        *,
        base: int,
        describe: bool,
    ) -> None:
        now = self.clock()
        outcome = {"value": "superseded"}

        async def write(connection: AsyncConnection) -> None:
            current = await store.head(connection, row["video_id"], lock=True)
            # CAS: the same media revision, the same base analysis revision, not withdrawn.
            if (
                current is None
                or current["publication_id"] != row["publication_id"]
                or current["media_sha256"] != row["media_sha256"]
                or current["revision"] != base
                or current["withdrawn"]
                or (describe and current["state"] != "pending")
            ):
                return
            # Withdrawal (#15): re-read the video under a share lock, so a withdrawal either
            # committed first (seen here) or waits for this transaction, after which
            # `store.on_withdrawn` (which locks the head) withdraws what this one wrote.
            withdrawn = (
                await connection.execute(
                    text("SELECT withdrawn FROM weather.video WHERE id=:video FOR SHARE"),
                    {"video": row["video_id"]},
                )
            ).scalar_one()
            if withdrawn:
                return
            # Model output never replaces a human (#44) revision of the same media.
            author = (
                await connection.execute(
                    text("""SELECT author_kind,media_sha256 FROM weather.analysis_revision
                    WHERE video_id=:video AND revision=:revision"""),
                    {"video": row["video_id"], "revision": base},
                )
            ).first()
            if (
                author is not None
                and author.author_kind != "model"
                and author.media_sha256 == row["media_sha256"]
            ):
                outcome["value"] = "kept_human"
                return
            revision_id = uuid4()
            visual_id = result.visual_id or revision_id
            keys = {
                c.key: c
                for c in visual_events(row, result.observations, result.event_windows, visual_id)
            }
            summary = " ".join(c["text"] for c in result.claims) or None
            await connection.execute(
                text(INSERT_REVISION),
                {
                    "id": revision_id,
                    "video": row["video_id"],
                    "revision": base + 1,
                    "publication": row["publication_id"],
                    "sha": row["media_sha256"],
                    "state": result.state,
                    "reason": result.reason,
                    "refresh": result.refresh,
                    "visual": visual_id,
                    "route": MODEL_ROUTE,
                    "model": result.model or run.model,
                    "prompt": result.prompt_sha256 or PROMPT_SHA256,
                    "schema": result.schema_sha256 or SCHEMA_SHA256,
                    "evidence_hash": result.packet.evidence_hash,
                    "evidence_count": result.packet.evidence_count,
                    "summary": summary,
                    "claims": json.dumps(result.claims),
                    "observations": json.dumps(
                        [
                            {
                                **o,
                                "start": _iso(result.event_windows[o["id"]][0]),
                                "end": _iso(result.event_windows[o["id"]][1]),
                            }
                            for o in result.observations
                        ]
                    ),
                    "limitations": json.dumps(result.limitations),
                    "coverage": json.dumps(result.coverage),
                    "inputs": json.dumps(result.inputs),
                    "event_keys": json.dumps(sorted(keys)),
                    "calls": run.calls,
                },
            )
            await connection.execute(
                text("""UPDATE weather.analysis SET state=:state,reason=:reason,
                  revision=:revision,stale=false,pending_since=NULL,next_attempt_at=NULL,
                  runs=runs+1,calls=calls+:calls,refreshes=refreshes+:refresh,
                  evidence_checked_at=:now,updated_at=:now WHERE video_id=:video"""),
                {
                    "state": result.state,
                    "reason": result.reason,
                    "revision": base + 1,
                    "calls": run.calls,
                    "refresh": 0 if describe else 1,
                    "now": now,
                    "video": row["video_id"],
                },
            )
            await self._events(connection, row, base, keys, now)
            await self._search(connection, row, base + 1, result, summary, now)
            outcome["value"] = result.state

        await context.commit(write)
        OUTCOMES.labels(outcome["value"]).inc()

    async def _events(
        self,
        connection: AsyncConnection,
        row: dict[str, Any],
        base: int,
        keys: dict[str, Candidate],
        now: datetime,
    ) -> None:
        previous = (
            await connection.execute(
                text("""SELECT event_keys FROM weather.analysis_revision
                WHERE video_id=:video AND revision=:revision"""),
                {"video": row["video_id"], "revision": base},
            )
        ).scalar_one_or_none() or []
        for key in sorted(set(previous) - set(keys)):
            await store.withdraw_event(connection, key, now)
        for key in sorted(keys):
            await record(connection, keys[key], now=now)

    async def _search(
        self,
        connection: AsyncConnection,
        row: dict[str, Any],
        revision: int,
        result: Result,
        summary: str | None,
        now: datetime,
    ) -> None:
        poster = (
            await connection.execute(
                text("""SELECT pa.asset_id FROM weather.publication_asset pa
                WHERE pa.publication_id=:publication AND pa.role='poster'"""),
                {"publication": row["publication_id"]},
            )
        ).scalar_one_or_none()
        try:
            await documents.replace(
                connection,
                video_text(row, revision, result, summary, poster),
                profile_id=self.profile_id,
                now=now,
            )
        except StaleRevision:
            LOG.info("Search document already newer; not replaced")

    # Periodic -----------------------------------------------------------------------------------

    async def check_evidence(self, now: datetime, *, limit: int = 12) -> int:
        """Queue a re-render where a recent revision's measurements changed."""
        async with self.engine.connect() as connection:
            rows = list(
                (
                    await connection.execute(
                        text("""SELECT a.video_id,a.publication_id,a.revision,r.evidence_hash,
                          v.starts_at,v.ends_at FROM weather.analysis a
                        JOIN weather.analysis_revision r
                          ON r.video_id=a.video_id AND r.revision=a.revision
                        JOIN weather.video v ON v.id=a.video_id
                        WHERE a.state IN ('complete','degraded') AND a.refreshes<:cap
                          AND r.author_kind='model'
                          AND NOT v.withdrawn AND v.ends_at>:since
                          AND (a.evidence_checked_at IS NULL OR a.evidence_checked_at<:due)
                        ORDER BY a.evidence_checked_at NULLS FIRST,a.video_id LIMIT :limit"""),
                        {
                            "cap": MAX_REFRESHES,
                            "since": now - EVIDENCE_LOOKBACK,
                            "due": now - EVIDENCE_INTERVAL,
                            "limit": limit,
                        },
                    )
                ).mappings()
            )
        queued = 0
        for item in rows:
            try:
                packet = await read_packet(
                    self.weather, "check", item["starts_at"], item["ends_at"]
                )
            except SourceUnavailable:
                return queued
            async with self.engine.begin() as connection:
                if packet.evidence_hash != item["evidence_hash"]:
                    queued += await store.request_refresh(
                        connection,
                        item["video_id"],
                        item["publication_id"],
                        item["revision"],
                        now,
                    )
                else:
                    await connection.execute(
                        text("""UPDATE weather.analysis SET evidence_checked_at=:now
                        WHERE video_id=:video AND revision=:revision"""),
                        {"now": now, "video": item["video_id"], "revision": item["revision"]},
                    )
        return queued


INSERT_REVISION = """INSERT INTO weather.analysis_revision(id,video_id,revision,publication_id,
  media_sha256,state,reason,refresh,visual_id,model_route,model,prompt_sha256,schema_sha256,
  evidence_hash,evidence_count,summary,claims,observations,limitations,coverage,inputs,
  event_keys,calls)
VALUES(:id,:video,:revision,:publication,:sha,:state,:reason,:refresh,:visual,:route,:model,
  :prompt,:schema,:evidence_hash,:evidence_count,:summary,CAST(:claims AS jsonb),
  CAST(:observations AS jsonb),CAST(:limitations AS jsonb),CAST(:coverage AS jsonb),
  CAST(:inputs AS jsonb),CAST(:event_keys AS jsonb),:calls)"""


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def visual_events(
    row: dict[str, Any],
    observations: Sequence[dict[str, Any]],
    windows: dict[str, tuple[datetime, datetime]],
    visual_id: UUID,
) -> list[Candidate]:
    """One clip-precision event per (window, category) clearly visible with medium/high
    confidence. Labels are fixed templates, so no model text reaches an event. Events cite the
    revision whose Stage A produced them, so a measured-evidence re-render leaves them unchanged."""
    found: dict[str, Candidate] = {}
    for item in observations:
        category = item["category"]
        if (
            category not in EVENT_LABELS
            or item["assessment"] != "visible"
            or item["confidence"] not in ("medium", "high")
        ):
            continue
        start, end = windows[item["id"]]
        key = f"visual:{row['video_id']}:{_iso(start)}:{category}"
        found[key] = Candidate(
            key=key,
            family="visual",
            origin="visual",
            start=start,
            end=end,
            precision="clip",
            rule_revision=f"visual-analysis/{PROMPT_SHA256[:16]}",
            evidence=(visual_id,),
            camera=row["camera_id"],
            label=EVENT_LABELS[category],
        )
    return list(found.values())


def video_text(
    row: dict[str, Any], revision: int, result: Result, summary: str | None, poster: UUID | None
) -> VideoText:
    """The search projection of a published description (#25's replace hook)."""
    coverage = result.coverage
    keogram = "with keogram" if result.inputs.get("keogram") else "frames only"
    label = LABEL if coverage.get("windows_reviewed") else STATION_LABEL
    quality = (
        f"{label}; {result.state}; {coverage['windows_reviewed']} of "
        f"{coverage['windows_total']} hour windows reviewed, {keogram}"
    )
    by_window: dict[datetime, list[Observation]] = {}
    for item in result.observations:
        by_window.setdefault(result.event_windows[item["id"]][0], []).append(
            Observation.from_record(item)
        )
    windows = []
    for window in hour_windows(row["starts_at"], row["ends_at"], row["frames"]):
        note = render.window_text(by_window.get(window.start, ()))
        if not window.available or note is None:
            continue
        windows.append(
            WindowText(
                window.start,
                window.end,
                note,
                "Sampled-frame visual observations",
                window.first_frame / FPS,
                window.end_frame / FPS,
            )
        )
    return VideoText(
        video_id=row["video_id"],
        camera_id=row["camera_id"],
        camera_label=CAMERA_LABELS.get(row["camera_id"], row["camera_id"]),
        segment=row["segment"],
        video_start=row["starts_at"],
        video_end=row["ends_at"],
        public_not_before=row["public_not_before"],
        media_revision=row["video_revision"],
        description_revision=revision,
        editorial_revision=0,
        analysis_mode="sampled_frames",
        observed_conditions=summary or "No supported description.",
        evidence_quality=quality,
        media_seconds=row["frames"] / FPS,
        poster_asset_id=poster,
        windows=tuple(windows),
    )


def register(registry: Registry, analyzer: Analyzer) -> None:
    async def describe(context: JobContext) -> None:
        await analyzer.describe(context)

    async def synthesize(context: JobContext) -> None:
        await analyzer.synthesize(context)

    registry.register(store.DESCRIBE_KIND, describe)
    registry.register(store.SYNTHESIZE_KIND, synthesize)
