# SPDX-License-Identifier: MIT
"""#23 guards without a database: the structured Stage A schema, the public templates (C18: no
model-written prose is ever published), the inference slot's lock release, call deferral and
budgets, the Cortex breaker, the nightly slot, the published model name and author, and the
optional keogram."""

import asyncio
import hashlib
import io
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from ai_support import CountingSlot, RawSink, Script, StaticMedia, StaticWeather, station, tiny_jpeg
from PIL import Image as PILImage
from sqlalchemy.exc import OperationalError
from weather.ai import media, render, store, validate
from weather.ai.analyze import Analyzer, BudgetSpent, CortexDown, Run, visual_request
from weather.ai.client import MODEL_ROUTE, Cortex, CortexError
from weather.ai.effective import EDITOR_LABEL, LABEL, STATION_LABEL, public_model
from weather.ai.evidence import Packet, build_packet
from weather.ai.media import (
    GarageMedia,
    MediaError,
    MediaUnavailable,
    Source,
    StoredObject,
    WindowMedia,
    keogram_windows,
)
from weather.ai.slot import LOCK, UNLOCK, CortexSlot, SlotBusy, Uncertain
from weather.ai.validate import Observation, Rejected
from weather.ai.windows import hour_windows, samples
from weather.api.timelapses import build_analysis
from weather.publish.objects import StoreError

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
END = T0 + timedelta(hours=2)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def packet(coverage: dict[str, float] | None = None, **values: float) -> Packet:
    aggregates = station(T0, END, **values)
    for index, item in enumerate(aggregates):
        fraction = (coverage or {}).get(f"{item.metric}_{item.statistic}")
        if fraction is not None:
            aggregates[index] = item.model_copy(
                update={"coverage": item.coverage.model_copy(update={"fraction": fraction})}
            )
    return build_packet("n0nce000", T0, END, aggregates)


# Stage A: structured output only ---------------------------------------------------------------


def stage_a(*observations: dict[str, Any], limitations: Sequence[Any] = (), **top: Any) -> str:
    """The rejection code of a one-window Stage A reply, or "accepted"."""
    required = [
        {"category": "rain", "assessment": "not_observed", "confidence": "medium"},
        {"category": "fog_visibility", "assessment": "not_observed", "confidence": "medium"},
    ]
    items = [{"evidence_ids": ["f-1"], **o} for o in (*required, *observations)]
    content = json.dumps(
        {"schema_version": 2, "observations": items, "limitations": list(limitations), **top},
        ensure_ascii=False,
    )
    try:
        validate.visual(content, "stop", supplied=["f-1"], window=0, nonce="n0nce000")
    except Rejected as rejected:
        return rejected.code
    return "accepted"


CLOUD = {"category": "cloud_cover", "assessment": "visible", "confidence": "high"}


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (stage_a(CLOUD), "accepted"),
        (stage_a({**CLOUD, "sky_cover": "overcast"}, limitations=["darkness"]), "accepted"),
        # No free text anywhere, however harmless it looks.
        (stage_a({**CLOUD, "description": "Broken clouds."}), "schema"),
        (stage_a({**CLOUD, "note": "x"}), "schema"),
        (stage_a(CLOUD, limitations=["Night frames were dark."]), "schema"),
        (stage_a(CLOUD, summary="A dry day."), "schema"),
        (stage_a({**CLOUD, "sky_cover": "mostly cloudy"}), "schema"),
        (stage_a({**CLOUD, "confidence": 0.9}), "schema"),
        (stage_a({**CLOUD, "evidence_ids": ["f-2"]}), "unknown_evidence"),
        (stage_a({**CLOUD, "intensity": "heavy"}), "misplaced_intensity"),
        (
            stage_a({"category": "rain", "assessment": "visible", "confidence": "high"}),
            "duplicate_category",
        ),
        (
            stage_a({"category": "sunlight", "assessment": "visible", "confidence": "high",
                     "sky_cover": "clear"}),
            "misplaced_sky_cover",
        ),
    ],
)  # fmt: skip
def test_stage_a_accepts_only_the_strict_structured_schema(reply: str, code: str) -> None:
    assert reply == code


# Stage C: deterministic templates --------------------------------------------------------------


def obs(window: int, category: str, assessment: str, confidence: str = "high", **extra: Any):
    return Observation(f"v-{window}-{category}", category, assessment, confidence, window, **extra)


def texts(claims: Sequence[dict[str, Any]]) -> list[str]:
    return [c["text"] for c in claims]


def test_templates_render_hedges_and_provable_absence_only() -> None:
    observations = [
        obs(0, "cloud_cover", "visible", sky_cover="broken"),
        obs(1, "cloud_cover", "visible", sky_cover="broken"),
        obs(0, "rain", "possible", intensity="light"),
        obs(1, "rain", "visible", "low", intensity="moderate"),  # low confidence is hedged
        obs(0, "fog_visibility", "not_observed"),
        obs(1, "fog_visibility", "not_observed"),
        obs(0, "lightning", "not_observed"),
        obs(1, "snowfall", "not_assessable"),
    ]
    assert texts(render.visual_claims(observations, 2, "skycam_east")) == [
        "The sampled east-sky frames show broken clouds in both reviewed hour windows.",
        "The sampled east-sky frames possibly show light rain in 1 of 2 reviewed hour windows.",
        "The sampled east-sky frames possibly show moderate rain in 1 of 2 reviewed hour windows.",
        # Absence only where every reviewed window assessed it as not observed, scoped.
        "No fog was visible in the sampled east-sky frames in both reviewed hour windows.",
    ]
    assert render.window_text([observations[0], observations[2], observations[4]]) == (
        "Broken clouds; possibly light rain; no fog visible in the frames."
    )
    assert render.limitations(
        observations, 2, {"darkness": 1, "unknown_code": 1}, keogram=False, unreviewed=True,
        withheld=["Maximum wind gust"],
    ) == [
        "Some hours could not be reviewed visually.",
        "No keogram was available; hours were described from frames.",
        "Station data was incomplete for: Maximum wind gust.",
        "Falling snow could not be assessed in 1 of 2 reviewed hour windows.",
        "Darkness limited what the frames show.",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("confidence", "codes", "data", "expected"),
    [
        ("high", {}, None, True),
        ("medium", {"sparse_sampling": 1}, None, True),
        # A low-confidence "not observed" proves nothing.
        ("low", {}, None, False),
        # Nor does one made in darkness or infrared imaging.
        ("high", {"darkness": 1}, None, False),
        ("high", {"infrared": 2}, None, False),
        # A positive measured total would contradict it; the measured sentence speaks instead.
        ("high", {}, packet(precipitation_sum=4.2), False),
    ],
)
def test_visual_absence_needs_confidence_daylight_and_no_measured_rain(
    confidence: str, codes: dict[str, int], data: Packet | None, expected: bool
) -> None:
    observations = [obs(w, "rain", "not_observed", confidence) for w in range(3)]
    claims = texts(
        render.visual_claims(observations, 3, "skycam_west", codes=codes, packet=data or packet())
    )
    absence = "No rain was visible in the sampled west-sky frames in all 3 reviewed hour windows."
    assert (absence in claims) is expected


def test_measured_text_never_prints_a_positive_total_as_zero_and_qualifies_partial() -> None:
    def lines(data: Packet, word: str) -> list[str]:
        return [t for t in texts(render.measured_claims(data)) if word in t]

    assert texts(render.measured_claims(packet()))[:3] == [
        "Measured temperature ranged from 3.2°C (37.8°F) to 14.8°C (58.6°F).",
        "The mean measured wind was 5.1 m/s (18.4 km/h, 11.4 mph).",
        "The strongest measured wind gust was 12.4 m/s (44.6 km/h, 27.7 mph).",
    ]
    assert lines(packet(), "rain") == ["Measured rain total: 0.0 mm (0.00 in)."]
    assert lines(packet(coverage={"precipitation_sum": 0.8}), "rain") == []
    partial = {"precipitation_sum": 0.6}
    assert lines(packet(coverage=partial, precipitation_sum=0.04), "rain") == [
        "Measured rain total: less than 0.1 mm (less than 0.01 in) over partial station coverage."
    ]
    assert lines(packet(precipitation_sum=0.06), "rain") == [
        "Measured rain total: 0.1 mm (less than 0.01 in)."
    ]
    assert lines(packet(coverage=partial, precipitation_sum=2.3), "rain") == [
        "Measured rain total: 2.3 mm (0.09 in) over partial station coverage."
    ]
    # Temperature and lightning are qualified the same way, and one strike is singular.
    thin = packet(coverage={"temperature_min": 0.7, "lightning_count_sum": 0.7},
                  lightning_count_sum=1.0)  # fmt: skip
    assert lines(thin, "temperature") == [
        "Measured temperature ranged from 3.2°C (37.8°F) to 14.8°C (58.6°F) "
        "over partial station coverage."
    ]
    assert lines(thin, "lightning") == [
        "The station counted 1 strike of lightning over partial station coverage."
    ]
    assert lines(packet(lightning_count_sum=3.0), "lightning") == [
        "The station counted 3 strikes of lightning."
    ]
    for value in (0.001, 0.04, 0.049, 0.05, 0.06, 0.2):
        for line in lines(packet(precipitation_sum=value), "rain"):
            assert " 0.0 mm" not in line and "(0.00 in)" not in line


BAD_GRAMMAR = (
    "1 reviewed hour windows",
    "all 1 ",
    "all 2 ",
    "1 strikes",
    "show a clear",
    "show an overcast",
    "  ",
    " .",
)


def test_every_public_string_comes_from_the_templates_and_reads_correctly() -> None:
    """Exhaustive over the enums and window counts: fixed phrases only, and no bad grammar."""
    allowed_nouns = {*render.SKY.values(), *render.FOG.values(), *render.PLAIN.values()}
    allowed_nouns |= {
        f"{i} {n}" for i in ("light", "moderate", "heavy") for n in ("rain", "falling snow")
    }
    extras: list[dict[str, Any]] = [{}]
    extras += [{"intensity": i} for i in ("light", "moderate", "heavy")]
    extras += [{"sky_cover": c} for c in render.SKY]
    for category in validate.CATEGORIES:
        for assessment in ("visible", "possible", "not_observed", "not_assessable"):
            for confidence in ("low", "medium", "high"):
                for extra in extras:
                    for reviewed in (1, 2, 3):
                        for count in range(1, reviewed + 1):
                            items = [
                                obs(w, category, assessment, confidence, **extra)
                                for w in range(count)
                            ]
                            lines = texts(render.visual_claims(items, reviewed, "frontyard_1"))
                            lines += render.limitations(
                                items, reviewed, {}, keogram=True, unreviewed=False, withheld=[]
                            )
                            note = render.window_text(items[:1])
                            lines += [note] if note else []
                            for line in lines:
                                assert line.endswith(".") and line[0].isupper(), line
                                assert not any(bad in line for bad in BAD_GRAMMAR), line
                            if render.shown(items[0]):
                                assert render.phrase(items[0]) in allowed_nouns
    assert render.plural(1, "strike") == "1 strike" and render.plural(2, "strike") == "2 strikes"
    assert len(render.claims(packet(), [obs(w, "rain", "visible", intensity=i)
                                         for w in range(20) for i in ("light", "moderate")],
                             20, "skycam_west")) <= render.MAX_CLAIMS  # fmt: skip


def test_state_follows_review_coverage_and_the_spent_budget() -> None:
    unit = analyzer(Script([("visual", "valid")]))
    row = {"camera_id": "skycam_east"}
    seen = [obs(0, "cloud_cover", "visible", sky_cover="clear")]

    def state(reviewed: int, spent: bool = False, data: Packet | None = None) -> tuple[Any, ...]:
        result = unit._result(
            row,
            data or packet(),
            seen[:reviewed],
            {"windows_reviewed": reviewed, "windows_total": 3},
            {"windows": 2, "keogram": True, "limitations": {}, "call_budget_spent": spent},
            refresh="full",
        )
        return result.state, result.reason

    assert state(1) == ("degraded", "visual_partial")
    assert state(0) == ("degraded", "visual_invalid")
    assert state(1, spent=True) == ("degraded", "call_budget")
    assert state(0, data=build_packet("n", T0, END, [])) == ("failed", "no_evidence")
    seen.append(obs(1, "cloud_cover", "visible", sky_cover="clear"))
    assert state(2) == ("complete", None)


# 7. The slot never returns a connection that may still hold the lock -------------------------


class FakeConnection:
    """A raw AUTOCOMMIT connection: `answer(sql)` returns a value or an awaitable."""

    def __init__(self, answer: Callable[[str], Any]) -> None:
        self.answer = answer
        self.statements: list[str] = []
        self.invalidated = False

    async def execution_options(self, **_: Any) -> FakeConnection:
        return self

    async def execute(self, statement: Any) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        value = self.answer(sql)
        if asyncio.iscoroutine(value):
            value = await value
        return SimpleNamespace(scalar_one=lambda: value)

    async def invalidate(self) -> None:
        self.invalidated = True


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConnection]:
        yield self.connection


def slot_for(answer: Callable[[str], Any], grace: float = 0.0) -> tuple[CortexSlot, Any]:
    connection = FakeConnection(answer)
    return CortexSlot(FakeEngine(connection), grace=grace), connection  # type: ignore[arg-type]


def test_a_confirmed_unlock_returns_the_connection_and_busy_holds_nothing() -> None:
    slot, connection = slot_for(lambda sql: True)

    async def once() -> None:
        async with slot.hold(1):
            pass

    run(once())
    assert connection.statements == [LOCK, UNLOCK] and not connection.invalidated
    busy, idle = slot_for(lambda sql: False)

    async def wait() -> None:
        async with busy.hold(0):
            pass

    with pytest.raises(SlotBusy):
        run(wait())
    assert not idle.invalidated


def test_cancellation_in_the_grace_sleep_invalidates_the_session() -> None:
    slot, connection = slot_for(lambda sql: True, grace=60)

    async def scenario() -> None:
        async def holder() -> None:
            async with slot.hold(1) as uncertain:
                uncertain.value = True

        task = asyncio.create_task(holder())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert UNLOCK not in connection.statements and connection.invalidated


def test_cancellation_inside_try_lock_invalidates_the_session() -> None:
    never = asyncio.Event

    async def stuck() -> bool:
        await never().wait()
        return True

    slot, connection = slot_for(lambda sql: stuck() if sql == LOCK else True)

    async def scenario() -> None:
        async def holder() -> None:
            async with slot.hold(1):
                pass

        task = asyncio.create_task(holder())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert connection.invalidated


def test_a_failed_unlock_invalidates_the_session() -> None:
    def answer(sql: str) -> bool:
        if sql == UNLOCK:
            raise OperationalError("unlock", {}, Exception("gone"))
        return True

    slot, connection = slot_for(answer)

    async def once() -> None:
        async with slot.hold(1):
            pass

    with pytest.raises(OperationalError):
        run(once())
    assert connection.invalidated


# 8 and 10. Slot trouble defers; budgets survive retries; the breaker stops calls -------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def analyzer(
    script: Script, slot: Any = None, *, sink: RawSink | None = None, clock: Clock | None = None
) -> Analyzer:
    async def no_sleep(_: float) -> None:
        return None

    return Analyzer(
        sink or RawSink(),  # type: ignore[arg-type]
        script.cortex(),
        slot or CountingSlot(),
        StaticMedia(),
        StaticWeather(station(T0, END)),
        profile_id="embed-main-2560-v1",
        sleep=no_sleep,
        clock=clock or Clock(),
    )


def visual_call(unit: Analyzer, state: Run) -> Any:
    window = hour_windows(T0, END, 600)[0]
    frames = WindowMedia(window, tuple((k, tiny_jpeg()) for k in samples(window, 2)), None)
    row = {
        "starts_at": T0,
        "ends_at": END,
        "frames": 600,
        "camera_id": "skycam_east",
        "segment": "daytime",
        "video_state": "published",
    }
    ids: list[str] = []

    def build(note: str | None) -> dict[str, Any]:
        body, supplied = visual_request(row, frames, state.nonce, note)
        ids[:] = supplied
        return body

    def accept(reply: Any) -> Any:
        return validate.visual(
            reply.content, reply.finish_reason, supplied=ids, window=0, nonce=state.nonce
        )

    return run(unit.call(state, build, accept, stage="visual"))


class TroubledSlot:
    """`on_enter` or `on_exit` raises, as a busy slot or a lost database would."""

    def __init__(self, on_enter: Exception | None = None, on_exit: Exception | None = None):
        self.on_enter, self.on_exit = on_enter, on_exit

    @asynccontextmanager
    async def hold(self, wait: float) -> AsyncIterator[Uncertain]:
        if self.on_enter is not None:
            raise self.on_enter
        yield Uncertain()
        if self.on_exit is not None:
            raise self.on_exit


DB_DOWN = OperationalError("slot", {}, Exception("down"))


@pytest.mark.parametrize(
    ("slot", "reason"),
    [(TroubledSlot(SlotBusy("busy")), "slot_busy"), (TroubledSlot(DB_DOWN), "slot_unavailable")],
)
def test_a_busy_or_unreachable_slot_defers_without_calling(slot: Any, reason: str) -> None:
    script = Script([("visual", "valid")])
    with pytest.raises(CortexDown) as down:
        visual_call(analyzer(script, slot), Run(uuid4(), "n0nce000"))
    assert down.value.reason == reason and script.bodies == []


def test_a_failed_release_keeps_the_successful_reply() -> None:
    script = Script([("visual", "valid")])
    result = visual_call(analyzer(script, TroubledSlot(on_exit=DB_DOWN)), Run(uuid4(), "n0"))
    assert result is not None and len(script.bodies) == 1


def test_the_head_budget_bounds_calls_across_scheduler_retries() -> None:
    sink = RawSink(budget=3)
    unit = analyzer(Script([("visual", "malformed_json")]), sink=sink)
    # The first attempt spends two calls (try and repair); its retry has one left, then stops
    # (describe then publishes what its accepted windows support).
    publication = uuid4()
    assert visual_call(unit, Run(uuid4(), "n0nce000", publication_id=publication)) is None
    retry = Run(uuid4(), "n0nce001", publication_id=publication)
    with pytest.raises(BudgetSpent):
        visual_call(unit, retry)
    assert retry.calls == 1 and sink.spent == 3


def test_the_breaker_skips_calls_until_the_next_nightly_window() -> None:
    clock = Clock()
    script = Script([("down",)])
    unit = analyzer(script, clock=clock)
    with pytest.raises(CortexDown):
        visual_call(unit, Run(uuid4(), "n0nce000"))
    assert len(script.bodies) == 3 and unit.breaker() == "cortex_unavailable"
    clock.now = store.next_night(T0) - timedelta(seconds=1)
    with pytest.raises(CortexDown) as skipped:
        visual_call(unit, Run(uuid4(), "n0nce001"))
    assert skipped.value.reason == "cortex_unavailable" and len(script.bodies) == 3
    clock.now = store.next_night(T0)
    assert unit.breaker() is None
    script.steps = [("visual", "valid")]
    assert visual_call(unit, Run(uuid4(), "n0nce002")) is not None


# 9. The nightly slot moves forward instead of clearing ----------------------------------------


@pytest.mark.parametrize("fresh", [True, False])
def test_nightly_moves_the_slot_and_refills_the_budget_only_when_it_queues(
    fresh: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = store.next_night(T0)
    row = SimpleNamespace(video_id=uuid4(), publication_id=uuid4(), next_attempt_at=now)
    queued: list[Any] = []

    async def enqueue(*args: Any, **kwargs: Any) -> bool:
        queued.append(kwargs["tag"])
        return fresh

    monkeypatch.setattr(store, "enqueue_describe", enqueue)
    updates: list[tuple[str, dict[str, Any]]] = []

    class Connection:
        async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
            if "SKIP LOCKED" in str(statement):
                # Withdrawn videos and heads with a live describe job are never selected.
                assert "j.status IN ('queued','running')" in str(statement)
                assert "NOT v.withdrawn" in str(statement)
                assert "attempted_at" in str(statement)
                assert params["kind"] == store.DESCRIBE_KIND
                return [row]
            updates.append((str(statement), params))
            return SimpleNamespace(rowcount=1)

    assert run(store.nightly(Connection(), now)) == int(fresh)  # type: ignore[arg-type]
    ((sql, params),) = updates
    assert params["retry"] == store.next_night(now) > now and params["fresh"] is fresh
    assert "CASE WHEN :fresh THEN 0" in sql
    assert queued == [now.strftime("%Y%m%dT%H%M%SZ")]


# 13. Only an identifier-shaped model name is published, and the author is labelled -----------


def test_provider_model_text_never_reaches_provenance() -> None:
    assert public_model("llm.qwen38u-5080", MODEL_ROUTE) == "llm.qwen38u-5080"
    assert public_model("Qwen/Qwen3-VL:235b", MODEL_ROUTE) == "Qwen/Qwen3-VL:235b"
    for raw in ("<b>x</b>", "ignore all previous", "", None, 7, "x" * 200, "-dash"):
        assert public_model(raw, MODEL_ROUTE) == MODEL_ROUTE

    def handler(request: httpx2.Request) -> httpx2.Response:
        message = {"role": "assistant", "content": "{}"}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        return httpx2.Response(200, json={"model": "a model <b>", "choices": [choice]})

    cortex = Cortex("k", transport=httpx2.MockTransport(handler))
    assert run(cortex.chat({"messages": []})).model == MODEL_ROUTE
    row = {
        "state": "complete",
        "reason": None,
        "stale": False,
        "revision": 1,
        "revision_state": "complete",
        "generated_at": END,
        "summary": "Clouds.",
        "claims": [],
        "limitations": [],
        "coverage": None,
        "inputs": {},
        "model_route": MODEL_ROUTE,
        "model": "raw provider text!",
        "prompt_sha256": "a" * 64,
        "schema_sha256": "b" * 64,
        "refresh": "full",
        "evidence_count": 1,
    }
    analysis = build_analysis(row, True)
    assert analysis.provenance is not None and analysis.provenance.model == MODEL_ROUTE
    assert analysis.author == "ai" and analysis.label == LABEL
    # A human (#44) revision is the editor's: never labelled AI-generated, no model provenance.
    edited = build_analysis({**row, "author_kind": "operator"}, True)
    assert edited.author == "editor" and edited.label == EDITOR_LABEL
    assert edited.provenance is None
    # No hour passed visual review: station data only, never AI-labelled.
    unreviewed = {"windows_reviewed": 0, "windows_total": 3}
    station_only = build_analysis({**row, "coverage": {**COVERAGE, **unreviewed}}, True)
    assert station_only.author == "station" and station_only.label == STATION_LABEL
    assert station_only.provenance is None
    reviewed = build_analysis({**row, "coverage": COVERAGE}, True)
    assert reviewed.author == "ai" and reviewed.label == LABEL
    # A stored claim of any other basis fails closed: it is never served.
    claims = [{"text": "Clouds.", "basis": "visual"}, {"text": "Odd.", "basis": "inference"}]
    assert [c.text for c in build_analysis({**row, "claims": claims}, True).claims] == ["Clouds."]


COVERAGE = {
    "algorithm_revision": "hour-windows-v1",
    "reviewed_seconds": 3600.0,
    "expected_seconds": 7200.0,
    "fraction": 0.5,
    "windows_reviewed": 1,
    "windows_total": 2,
    "gaps": [],
}


# 1. Undecodable or absurdly nested replies are rejected, never a crash ------------------------


def test_lone_surrogates_and_deep_nesting_are_malformed_not_fatal() -> None:
    for content in ('{"schema_version": 2, "x": "\ud800"}', "[" * 100_000, "{" * 5000 + "}"):
        with pytest.raises(Rejected) as rejected:
            validate.parse(content, "stop")
        assert rejected.value.code in ("malformed_json", "schema")
    with pytest.raises(Rejected) as surrogate:
        validate.parse("\ud800{}", "stop")
    assert surrogate.value.code == "malformed_json"

    def nested(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b'{"choices": ' + b"[" * 100_000)

    with pytest.raises(CortexError) as invalid:
        run(Cortex("k", transport=httpx2.MockTransport(nested)).chat({"messages": []}))
    assert invalid.value.kind == "invalid"
    # Inside a run, both are a rejected try plus one repair, then the window is skipped.
    script = Script([])
    script.cortex = lambda: Cortex("k", transport=httpx2.MockTransport(nested))  # type: ignore[method-assign]
    unit = analyzer(script)
    state = Run(uuid4(), "n0nce000")
    assert visual_call(unit, state) is None and state.calls == 2


# 14. A bad keogram means frames only, never a failed video -------------------------------------


def png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), (10, 20, 30)).save(buffer, "PNG")
    return buffer.getvalue()


WINDOWS = hour_windows(T0, END, 600)


@pytest.mark.parametrize(
    "data", [b"not an image", png(5000, 1000), b"GIF89a" + b"\0" * 64, png(8, 8)[:40]]
)
def test_an_unusable_keogram_falls_back_to_frames_only(data: bytes) -> None:
    assert keogram_windows(data, WINDOWS, WINDOWS) == [None, None]
    assert all(keogram_windows(png(1440, 32), WINDOWS, WINDOWS))


class Span:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def chunks(self, size: int) -> AsyncIterator[bytes]:
        yield self.data

    async def aclose(self) -> None:
        return None


class Store:
    def __init__(self, objects: dict[str, bytes], errors: dict[str, StoreError] | None = None):
        self.objects, self.errors = objects, errors or {}
        self.opened: list[str] = []

    async def open_span(self, key: str, first: int, last: int, size: int) -> Span:
        self.opened.append(key)
        if key in self.errors:
            raise self.errors[key]
        return Span(self.objects[key])


def stored(key: str, data: bytes, *, sha: str | None = None, size: int | None = None) -> Any:
    return StoredObject(key, sha or hashlib.sha256(data).hexdigest(), size or len(data))


@pytest.mark.parametrize("problem", ["content_address", "oversized"])
def test_garage_media_describes_from_frames_when_the_keogram_is_bad(
    problem: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        media, "sample_frames", lambda path, indices, directory: [tiny_jpeg()] * len(indices)
    )
    keogram = png(1440, 32)
    objects = {"web": b"web-bytes", "keogram": keogram}
    bad = (
        stored("keogram", keogram, sha="0" * 64)
        if problem == "content_address"
        else stored("keogram", keogram, size=media.MAX_KEOGRAM_BYTES + 1)
    )
    reader = Store(objects)
    prepared = run(
        GarageMedia(reader, tmp_path).prepare(Source(stored("web", b"web-bytes"), bad), WINDOWS)
    )
    assert len(prepared) == 2 and all(p.keogram is None and p.frames for p in prepared)
    assert reader.opened == (["web", "keogram"] if problem == "content_address" else ["web"])


# 4. A transient store error retries; only a definite answer fails the video -------------------


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (StoreError("span"), MediaUnavailable),  # transport
        (StoreError("span", 503), MediaUnavailable),
        (StoreError("span read"), MediaUnavailable),
        (StoreError("span", 404), MediaError),  # definitely missing
        (StoreError("span", 200), MediaError),  # served, but not the committed object
    ],
)
def test_store_errors_on_the_rendition_retry_unless_definite(
    error: StoreError, outcome: type[Exception], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(media, "sample_frames", lambda *_: [])
    reader = Store({"web": b"web-bytes"}, {"web": error})
    with pytest.raises(outcome):
        run(GarageMedia(reader, tmp_path).prepare(Source(stored("web", b"web"), None), WINDOWS))


def test_a_missing_keogram_means_frames_only_but_a_store_blip_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        media, "sample_frames", lambda path, indices, directory: [tiny_jpeg()] * len(indices)
    )
    keogram = png(1440, 32)
    objects = {"web": b"web-bytes", "keogram": keogram}
    source = Source(stored("web", b"web-bytes"), stored("keogram", keogram))
    gone = Store(objects, {"keogram": StoreError("span", 404)})
    prepared = run(GarageMedia(gone, tmp_path).prepare(source, WINDOWS))
    assert all(p.keogram is None and p.frames for p in prepared)
    blip = Store(objects, {"keogram": StoreError("span", 502)})
    with pytest.raises(MediaUnavailable):
        run(GarageMedia(blip, tmp_path).prepare(source, WINDOWS))


# 9. A stale job cannot spend a newer publication's budget --------------------------------------


def test_spending_is_bound_to_the_publication() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class Connection:
        async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
            seen.append((str(statement), params))
            return SimpleNamespace(rowcount=0)

    video, publication = uuid4(), uuid4()
    assert not run(store.spend_call(Connection(), video, publication, 80))  # type: ignore[arg-type]
    ((sql, params),) = seen
    assert "publication_id=:publication" in sql and params["publication"] == publication
