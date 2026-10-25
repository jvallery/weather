# SPDX-License-Identifier: MIT
"""#23 review guards without a database: one focused test per validation bypass class, the
inference slot's lock release, call deferral and budgets, the Cortex breaker, the nightly slot,
the published model name and the optional keogram."""

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
from weather.ai import analyze, media, store, validate
from weather.ai.analyze import Analyzer, CortexDown, Run, visual_request
from weather.ai.client import MODEL_ROUTE, Cortex
from weather.ai.effective import public_model
from weather.ai.evidence import Packet, build_packet, measured_claims
from weather.ai.media import GarageMedia, Source, StoredObject, WindowMedia, keogram_windows
from weather.ai.slot import LOCK, UNLOCK, CortexSlot, SlotBusy, Uncertain
from weather.ai.validate import Observation, Rejected
from weather.ai.windows import hour_windows, samples
from weather.api.timelapses import build_analysis

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


def observation(name: str, category: str, assessment: str, text: str = "Clouds.") -> Observation:
    return Observation(f"v-{name}", category, assessment, "high", text, 0)


OBSERVATIONS = {
    o.id.removeprefix("v-"): o
    for o in (
        observation("cloud", "cloud_cover", "visible", "Broken clouds."),
        observation("rain", "rain", "visible", "Rain streaks."),
        observation("rain_possible", "rain", "possible", "Streaks may be rain."),
        observation("rain_none", "rain", "not_observed", "No rain is visible."),
        observation("rain_dark", "rain", "not_assessable", "Rain cannot be assessed."),
        observation("fog", "fog_visibility", "visible", "Fog hides the hills."),
    )
}


def narrate(
    text: str,
    basis: str = "visual",
    cite: Sequence[str | tuple[str, str]] = ("cloud",),
    *,
    data: Packet | None = None,
    limitations: Sequence[str] = (),
) -> str | None:
    """The rejection code of a one-claim Stage C reply, or None when it validates."""
    data = data or packet()
    ids = []
    for item in cite:
        if isinstance(item, tuple):
            metric = data.find(*item)
            assert metric is not None
            ids.append(metric.id)
        else:
            ids.append(OBSERVATIONS[item].id)
    content = json.dumps(
        {
            "schema_version": 1,
            "summary_claims": [{"text": text, "basis": basis, "evidence_ids": ids}],
            "events": [],
            "limitations": list(limitations),
        },
        ensure_ascii=False,
    )
    try:
        validate.narrative(content, "stop", packet=data, observations=OBSERVATIONS.values())
    except Rejected as rejected:
        return rejected.code
    return None


def stage_a(
    description: str, assessment: str = "visible", *, limitations: Sequence[str] = ()
) -> str | None:
    """The rejection code of a one-window Stage A reply, or None when it validates."""
    content = json.dumps(
        {
            "schema_version": 1,
            "evidence_ids": ["f-1"],
            "observations": [
                {
                    "observation_id": "a",
                    "category": "rain",
                    "assessment": assessment,
                    "description": description,
                    "confidence": "medium",
                    "evidence_ids": ["f-1"],
                },
                {
                    "observation_id": "b",
                    "category": "fog_visibility",
                    "assessment": "not_observed",
                    "description": "Distant terrain stays clearly visible.",
                    "confidence": "medium",
                    "evidence_ids": ["f-1"],
                },
            ],
            "limitations": list(limitations),
        },
        ensure_ascii=False,
    )
    try:
        validate.visual(content, "stop", supplied=["f-1"], window=0, nonce="n0nce000")
    except Rejected as rejected:
        return rejected.code
    return None


# 1. A claim's weather words must match what it cites ------------------------------------------


@pytest.mark.parametrize(
    ("text", "cite", "code"),
    [
        ("Rain fell through the afternoon.", ["rain"], None),
        ("Light rain possibly fell.", ["rain_possible"], None),
        ("Rain fell through the afternoon.", ["cloud"], "unsupported_weather"),
        ("Snow showers crossed the hills.", ["rain"], "unsupported_weather"),
        ("Fog settled over the valley.", ["rain"], "unsupported_weather"),
        ("Thunder and lightning under dark clouds.", ["cloud"], "unsupported_weather"),
        ("Drizzle and mist under grey clouds.", ["cloud"], "unsupported_weather"),
        ("Hail and sleet under dark clouds.", ["cloud"], "unsupported_weather"),
        ("Frost coated the ground.", ["cloud"], "unsupported_weather"),
        # not_observed and not_assessable never back a positive claim, hedged or not.
        ("Rain fell.", ["rain_none"], "unsupported_weather"),
        ("Rain possibly fell.", ["rain_dark"], "unsupported_weather"),
        # A possible observation needs its hedge next to the weather word.
        ("Rain fell.", ["rain_possible"], "uncertain_as_fact"),
        (
            "Rain fell all afternoon while the clouds possibly thickened.",
            ["rain_possible", "cloud"],
            "uncertain_as_fact",
        ),
        ("Rain fell; clouds may have thickened.", ["rain_possible", "cloud"], "uncertain_as_fact"),
        # Whole words: a rainbow is not rain.
        ("A rainbow arched over broken clouds.", ["cloud"], None),
        # Saying it could not be judged is no claim either way.
        ("Rain could not be assessed after dark.", ["rain_dark"], None),
    ],
)
def test_weather_words_need_cited_visible_or_adjacently_hedged_evidence(
    text: str, cite: list[str], code: str | None
) -> None:
    assert narrate(text, "visual", cite) == code


# 2. Values are bound to their own subject ------------------------------------------------------


SWAP = packet(temperature_min=2.0, temperature_max=18.0)
BOTH = [("temperature", "min"), ("temperature", "max")]
WIND = [("wind_avg", "mean"), ("wind_gust", "max")]


@pytest.mark.parametrize(
    ("text", "cite", "code"),
    [
        ("The high was 18.0°C and the low 2.0°C.", BOTH, None),
        ("The high was 2.0°C and the low 18.0°C.", BOTH, "irrelevant_number"),
        ("Temperatures ranged from 2.0°C to 18.0°C.", BOTH, None),
        ("Temperatures ranged from 18.0°C to 2.0°C.", BOTH, "irrelevant_number"),
        ("Average wind was 5.1 m/s with gusts to 12.4 m/s.", WIND, None),
        ("Average wind was 12.4 m/s with gusts to 5.1 m/s.", WIND, "irrelevant_number"),
        ("Gusts reached 5.1 m/s.", WIND, "irrelevant_number"),
        # Whole words with optional plurals: "lower" is not "low", "highs" is "high".
        ("A lower reading of 2.0°C.", BOTH, "irrelevant_number"),
        ("Highs reached 18.0°C.", BOTH, None),
        ("Over the rainbow, 0.0 mm.", [("precipitation", "sum")], "irrelevant_number"),
    ],
)
def test_each_number_binds_to_the_nearest_subject_of_its_own_metric(
    text: str, cite: list[tuple[str, str]], code: str | None
) -> None:
    assert narrate(text, "instrument", cite, data=SWAP) == code


# 3. Numbers cannot slip past the scan ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The strongest gust was 12.4 m/s, about .5 m/s above the rest.",
        "The strongest gust was 12.4 m/s, x40 the calm.",
        "The strongest gust was 12.4 m/s for ½ the day.",
        "The strongest gust was 12.4 m/s for half the day.",
        *(
            f"The strongest gust was 12.4 m/s {tail}."
            for tail in (
                "at one point",
                "with zero calm",
                "in a single burst",
                "once",
                "twice",
                "a dozen times",
                "a hundred metres up",
                "a thousand times",
                "a million times",
            )
        ),
    ],
)
def test_unrendered_numbers_in_any_form_are_rejected(text: str) -> None:
    assert narrate(text, "instrument", [("wind_gust", "max")]) in (
        "ungrounded_number",
        "claim_number",
    )
    assert stage_a(text.replace("12.4 m/s", "strong"), "visible") == "visual_number"


# 4. Absence claims -----------------------------------------------------------------------------


PARTIAL_RAIN = packet(coverage={"precipitation_sum": 0.8})
RAIN_TOTAL = [("precipitation", "sum")]


@pytest.mark.parametrize(
    ("text", "basis", "cite", "data", "code"),
    [
        ("It was a rain-free day under broken clouds.", "visual", ["cloud"], None, "absence"),
        ("Skies stayed precipitation-free.", "visual", ["cloud"], None, "absence"),
        ("Not a drop fell under the clouds.", "visual", ["cloud"], None, "absence"),
        ("A dry day under broken clouds.", "visual", ["cloud"], None, "absence"),
        ("Broken clouds, and the afternoon stayed dry.", "visual", ["cloud"], None, "absence"),
        ("Clouds covered the frames, and there was no rain.", "visual", ["cloud"], None, "absence"),
        ("No lightning was seen.", "visual", ["cloud"], None, "absence"),
        # Only "not visible in the frames", adjacent to the precipitation word, is exempt.
        ("No rain was visible in the sampled frames.", "visual", ["cloud"], None, None),
        ("Rain was not visible in the frames.", "visual", ["cloud"], None, None),
        ("No rain was visible.", "visual", ["cloud"], None, "absence"),
        # A measured zero supports absence only at full coverage, and is itself one.
        ("No rain fell, and the station measured 0.0 mm of rain.", "instrument", RAIN_TOTAL,
         None, None),
        ("The station measured 0.0 mm of rain.", "instrument", RAIN_TOTAL, PARTIAL_RAIN,
         "absence"),
        ("Rainfall totalled 0.0 mm.", "instrument", RAIN_TOTAL, PARTIAL_RAIN, "absence"),
        ("No rain fell, and rainfall totalled 0.0 mm.", "instrument", RAIN_TOTAL, PARTIAL_RAIN,
         "absence"),
    ],
)  # fmt: skip
def test_absence_needs_a_cited_full_coverage_zero_or_the_imagery_scope(
    text: str, basis: str, cite: list[Any], data: Packet | None, code: str | None
) -> None:
    assert narrate(text, basis, cite, data=data) == (code and "unsupported_absence")


def test_the_fallback_summary_never_prints_a_partial_zero_rain_total() -> None:
    def rain_lines(data: Packet) -> list[str]:
        return [c["text"] for c in measured_claims(data) if "rain" in c["text"]]

    assert rain_lines(packet()) == ["Measured rain total: 0.0 mm (0.00 in)."]
    assert rain_lines(PARTIAL_RAIN) == []
    wet = packet(coverage={"precipitation_sum": 0.8}, precipitation_sum=2.3)
    (line,) = rain_lines(wet)
    assert line.endswith("over partial station coverage.")
    assert validate.numbers_grounded(line, wet.metrics) == 2


# 5. Stage A descriptions, limitations and what reaches public text ----------------------------


@pytest.mark.parametrize(
    ("description", "assessment", "code"),
    [
        ("Rain streaks cross the frames.", "visible", None),
        ("Streaks near the lens may be light rain.", "possible", None),
        ("Rain streaks cross the frames.", "possible", "visual_uncertain_as_fact"),
        ("Rain streaks cross the frames.", "not_observed", "visual_uncertain_as_fact"),
        ("Rain streaks cross the frames.", "not_assessable", "visual_uncertain_as_fact"),
        ("Darkness means rain cannot be assessed.", "not_assessable", None),
        ("No rain is visible.", "not_observed", None),
        ("The hour was rain-free.", "not_observed", "visual_absence"),
        ("The ground stayed dry all hour.", "not_observed", "visual_absence"),
    ],
)
def test_stage_a_descriptions_follow_the_absence_and_hedge_rules(
    description: str, assessment: str, code: str | None
) -> None:
    assert stage_a(description, assessment) == code


@pytest.mark.parametrize(
    ("limitation", "code"),
    [
        ("Night frames were dark.", None),
        ("Possible rain on the lens blurred some frames.", None),
        ("Rain on the lens blurred some frames.", "uncertain_as_fact"),
        ("There was no rain, so wetness was not assessed.", "absence"),
    ],
)
def test_limitations_follow_the_absence_and_hedge_rules(limitation: str, code: str | None) -> None:
    assert stage_a("Rain streaks cross the frames.", limitations=[limitation]) == (
        code and f"visual_{code}"
    )
    assert narrate("Broken clouds.", limitations=[limitation]) == (code and f"claim_{code}")


def test_only_visible_or_hedged_possible_observations_reach_search_text() -> None:
    row = {
        "video_id": uuid4(),
        "camera_id": "skycam_east",
        "segment": "daytime",
        "starts_at": T0,
        "ends_at": END,
        "frames": 600,
        "public_not_before": END,
        "video_revision": 1,
    }
    notes = [
        ("a", "visible", "Broken clouds."),
        ("b", "possible", "Streaks may be light rain."),
        ("c", "possible", "Heavy rain."),
        ("d", "not_observed", "No fog is visible."),
    ]
    result = analyze.Result(
        state="complete",
        reason=None,
        claims=[],
        limitations=[],
        observations=[
            {"id": i, "category": "rain", "assessment": a, "description": d, "window": 0}
            for i, a, d in notes
        ],
        coverage={"windows_reviewed": 1, "windows_total": 2},
        inputs={},
        packet=packet(),
        refresh="full",
        event_windows={i: (T0, T0 + timedelta(hours=1)) for i, _, _ in notes},
    )
    (window,) = analyze.video_text(row, 1, result, "Clouds.", None).windows
    assert window.observed_conditions == "Broken clouds. Streaks may be light rain."


# 6. People and vehicles, however spelled -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Girls played under broken clouds.",
        "Boys ran under broken clouds.",
        "Bikes stood under broken clouds.",
        "A bicycle leaned under broken clouds.",
        "Cyclists passed under broken clouds.",
        "Joggers passed under broken clouds.",
        "Drivers waited under broken clouds.",
        "Buses passed under broken clouds.",
        "Motorcycles passed under broken clouds.",
        "A sedan sat under broken clouds.",
        "A crowd gathered under broken clouds.",
        "Workers gathered under broken clouds.",
        "A toddler sat under broken clouds.",
        "A stroller sat under broken clouds.",
        "A box was delivered under broken clouds.",
        "Someone walked under broken clouds.",
        "Headlights glowed under broken clouds.",
        "Pickup trucks passed under broken clouds.",
    ],
)
def test_people_and_vehicle_words_match_with_plurals_and_synonyms(text: str) -> None:
    assert narrate(text) == "claim_personal"
    assert stage_a(text) == "visual_personal"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Ａ ｃａｒ sat under broken clouds.", "claim_personal"),  # full width, NFKC-folded
        ("A c​ar sat under broken clouds.", "claim_control"),  # zero-width space
        ("A c­ar sat under broken clouds.", "claim_control"),  # soft hyphen (Cf)
        ("A саr sat under broken clouds.", "claim_script"),  # Cyrillic look-alikes
        ("Broken clouds — then sun.", "claim_script"),  # only °, – and ’ beyond ASCII
        ("Broken clouds drifted past the café’s roof – then sun.", None),
    ],
)
def test_text_is_nfkc_folded_and_latin_only(text: str, code: str | None) -> None:
    assert narrate(text) == code


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
    # The first attempt spends two calls (try and repair); its retry has one left, then defers.
    assert visual_call(unit, Run(uuid4(), "n0nce000", metered=True)) is None
    retry = Run(uuid4(), "n0nce001", metered=True)
    with pytest.raises(CortexDown) as down:
        visual_call(unit, retry)
    assert down.value.reason == "call_budget" and retry.calls == 1 and sink.spent == 3


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


def test_nightly_moves_the_slot_to_the_next_night(monkeypatch: pytest.MonkeyPatch) -> None:
    now = store.next_night(T0)
    row = SimpleNamespace(video_id=uuid4(), publication_id=uuid4(), next_attempt_at=now)
    queued: list[Any] = []

    async def enqueue(*args: Any, **kwargs: Any) -> None:
        queued.append(kwargs["tag"])

    monkeypatch.setattr(store, "enqueue_describe", enqueue)

    class Connection:
        updates: list[tuple[str, dict[str, Any]]] = []

        async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
            if "SKIP LOCKED" in str(statement):
                return [row]
            self.updates.append((str(statement), params))
            return SimpleNamespace(rowcount=1)

    connection = Connection()
    assert run(store.nightly(connection, now)) == 1  # type: ignore[arg-type]
    ((sql, params),) = connection.updates
    assert params["retry"] == store.next_night(now) > now
    assert "attempt_calls=0" in sql and queued == [now.astimezone(store.LOCAL).date().isoformat()]


# 13. Only an identifier-shaped model name is recorded or published ----------------------------


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
    provenance = build_analysis(row, True).provenance
    assert provenance is not None and provenance.model == MODEL_ROUTE


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
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.opened: list[str] = []

    async def open_span(self, key: str, first: int, last: int, size: int) -> Span:
        self.opened.append(key)
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
