# SPDX-License-Identifier: MIT
"""AI descriptions (#23) without a database: windows, contracts, validation of recorded
structured replies, request bodies, call budgets, events and manifests.

Cortex is never called: every reply is a recorded-format fixture (fixtures/ai-recorded.json).
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from ai_support import (
    FIXTURE,
    CountingSlot,
    RawSink,
    Script,
    StaticMedia,
    StaticWeather,
    context_of,
    station,
    tiny_jpeg,
)
from PIL import Image as PILImage
from weather.ai import analyze, render, store, validate
from weather.ai.analyze import Analyzer, CortexDown, Run, visual_events, visual_request
from weather.ai.client import (
    MAX_IMAGE_BYTES,
    Cortex,
    CortexError,
    Image,
    check_base_url,
    request_body,
)
from weather.ai.evidence import build_packet, renderings
from weather.ai.media import WindowMedia, keogram_columns, sample_frames
from weather.ai.render import measured_claims
from weather.ai.validate import Rejected
from weather.ai.windows import frame_ranges, hour_windows, samples
from weather.config import ConfigurationError, load_settings

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/analysis-window-vectors.json").read_text())
T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# Analysis windows --------------------------------------------------------------------------


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_analysis_windows(case: dict[str, Any]) -> None:
    duration = Fraction(case["source_duration_seconds"])
    frames = case["output_frames"]
    expected = case["windows"]
    cuts = [Fraction(w["source_start_seconds"]) for w in expected[1:]]
    ranges = frame_ranges(duration, frames, cuts)
    assert ranges == [(w["first_frame"], w["end_frame_exclusive"]) for w in expected]
    # Half-open windows tile [0, N) exactly once; an empty window borrows nothing.
    owned = [k for first, end in ranges for k in range(first, end)]
    assert owned == list(range(frames))
    # The same windows from aware instants split at UTC hours (the vectors start on an hour).
    start = T0
    end = start + timedelta(seconds=case["source_duration_seconds"])
    windows = hour_windows(start, end, frames)
    assert [(w.first_frame, w.end_frame) for w in windows] == ranges
    for window, vector in zip(windows, expected, strict=True):
        assert window.frame_count == vector["selected_frame_count"]
        assert window.pts == (
            Fraction(vector["original_pts_start"]),
            Fraction(vector["original_pts_end"]),
        )
        picked = samples(window)
        assert len(picked) == min(8, window.frame_count) == len(set(picked))
        assert all(window.first_frame <= k < window.end_frame for k in picked)
        assert window.available == (window.frame_count > 0)


def test_windows_follow_utc_hours_from_an_off_hour_dawn() -> None:
    dawn = datetime(2026, 6, 1, 11, 23, 45, tzinfo=UTC)
    windows = hour_windows(dawn, dawn + timedelta(hours=14, minutes=30), 4350)
    assert windows[0].start == dawn and windows[0].end == datetime(2026, 6, 1, 12, tzinfo=UTC)
    assert all(w.start.minute == 0 for w in windows[1:])
    assert windows[-1].end == dawn + timedelta(hours=14, minutes=30)
    assert sum(w.frame_count for w in windows) == 4350
    with pytest.raises(ValueError):
        hour_windows(dawn, dawn, 10)


# Shipped contracts --------------------------------------------------------------------------


def test_the_prompt_is_hashed_and_the_schema_matches_its_fixtures() -> None:
    contracts = ROOT / "app/weather/ai/contracts"
    # Only the Stage A prompt and schema ship (C18): there is no synthesis prompt.
    assert sorted(p.name for p in contracts.iterdir()) == [
        "visual-analysis.schema.json",
        "visual-system.txt",
    ]
    prompt = (contracts / "visual-system.txt").read_bytes()
    assert analyze.PROMPT_SHA256 == hashlib.sha256(
        prompt + b"\0" + render.RENDER_REVISION.encode()
    ).hexdigest()
    assert "description" not in json.dumps(validate.schema()["properties"])
    cases = json.loads((ROOT / "docs/contracts/visual-analysis-fixtures.json").read_text())
    for case in cases["cases"]:
        assert validate.shape_valid(case["value"]) is case["valid"], case["name"]
    assert set(validate.CATEGORIES) == set(
        validate.schema()["properties"]["observations"]["items"]["properties"][
            "category"
        ]["enum"]
    )


# Recorded replies ---------------------------------------------------------------------------


def window(index: int = 0) -> Any:
    return hour_windows(T0, T0 + timedelta(hours=2), 600)[index]


def head_row(**changes: Any) -> dict[str, Any]:
    value = {
        "video_id": uuid4(),
        "publication_id": uuid4(),
        "media_sha256": "a" * 64,
        "state": "pending",
        "revision": 0,
        "camera_id": "skycam_east",
        "cycle_date": T0.date(),
        "segment": "daytime",
        "starts_at": T0,
        "ends_at": T0 + timedelta(hours=2),
        "frames": 600,
        "withdrawn": False,
        "video_state": "published",
        "video_revision": 1,
        "public_not_before": T0 + timedelta(hours=2, seconds=630),
    }
    value.update(changes)
    return value


def media(keogram: bool = True) -> WindowMedia:
    w = window()
    return WindowMedia(
        w, tuple((k, tiny_jpeg()) for k in samples(w, 3)), tiny_jpeg(9) if keogram else None
    )


def visual_reply(name: str) -> tuple[list[str], Any]:
    body, supplied = visual_request(head_row(), media(), "n0nce000", None)
    cortex = Script([("visual", name)]).cortex()
    reply = run(cortex.chat(body))
    return supplied, reply


PACKET = build_packet("n0nce000", T0, T0 + timedelta(hours=2), station(T0, T0 + timedelta(hours=2)))


def test_recorded_visual_reply_validates_and_is_rebound() -> None:
    supplied, reply = visual_reply("valid")
    result = validate.visual(
        reply.content, reply.finish_reason, supplied=supplied, window=0, nonce="n0nce000"
    )
    assert [o.id for o in result.observations] == [f"v-n0nce000-0-{i}" for i in range(3)]
    assert [(o.category, o.assessment, o.intensity, o.sky_cover) for o in result.observations] == [
        ("cloud_cover", "visible", None, "broken"),
        ("rain", "possible", "light", None),
        ("fog_visibility", "not_observed", None, None),
    ]
    assert result.limitations == ("sparse_sampling",) and reply.model == FIXTURE["model"]
    fenced = f"```json\n{reply.content}\n```"
    assert validate.visual(fenced, "stop", supplied=supplied, window=0, nonce="n0nce000") == result


@pytest.mark.parametrize(
    "name", [n for n, c in FIXTURE["visual"].items() if "code" in c], ids=lambda n: f"visual-{n}"
)
def test_recorded_visual_negatives_are_never_accepted(name: str) -> None:
    supplied, reply = visual_reply(name)
    with pytest.raises(Rejected) as rejected:
        validate.visual(
            reply.content, reply.finish_reason, supplied=supplied, window=0, nonce="n0nce000"
        )
    assert rejected.value.code == FIXTURE["visual"][name]["code"]


# Stage B packet -----------------------------------------------------------------------------


def test_packet_renders_exactly_withholds_thin_coverage_and_hashes_without_the_nonce() -> None:
    assert renderings("temperature", 21.35) == (("21.4", "°C"), ("70.4", "°F"))
    assert renderings("precipitation", 3.2) == (("3.2", "mm"), ("0.13", "in"))
    assert renderings("temperature", -0.04) == (("0.0", "°C"), ("31.9", "°F"))
    start, end = T0, T0 + timedelta(hours=2)
    one = build_packet("aaaa", start, end, station(start, end))
    two = build_packet("bbbb", start, end, station(start, end))
    assert one.evidence_hash == two.evidence_hash and one.metrics[0].id != two.metrics[0].id
    assert (
        one.evidence_hash
        != build_packet("aaaa", start, end, station(start, end, wind_gust_max=13.0)).evidence_hash
    )
    thin = station(start, end)
    thin[5] = thin[5].model_copy(
        update={"coverage": thin[5].coverage.model_copy(update={"fraction": 0.3})}
    )
    packet = build_packet("c", start, end, thin)
    assert packet.find("wind_gust", "max") is None and packet.withheld == ("Maximum wind gust",)
    texts = [c["text"] for c in measured_claims(one)]
    assert texts[0] == "Measured temperature ranged from 3.2°C (37.8°F) to 14.8°C (58.6°F)."


# Config and request bodies -------------------------------------------------------------------


def test_only_the_cortex_route_is_an_inference_endpoint() -> None:
    base = {"WEATHER_PUBLIC_ORIGIN": "https://weather.vallery.net"}
    assert load_settings({**base, "WEATHER_CORTEX_BASE_URL": "https://cortex.vallery.net/v1"})
    for url in (
        "https://api.openai.com/v1",
        "https://cortex.vallery.net/v2",
        "https://cortex.vallery.net.evil.example/v1",
        "http://cortex.vallery.net/v1",
        "https://user:pass@cortex.vallery.net/v1",
    ):
        with pytest.raises(ConfigurationError):
            load_settings({**base, "WEATHER_CORTEX_BASE_URL": url})
        with pytest.raises(ValueError):
            check_base_url(url)
        with pytest.raises(ValueError):
            Cortex("k", base_url=url)
    with pytest.raises(ValueError):
        check_base_url(None)


def test_stage_a_bodies_carry_no_measurements_and_cap_images() -> None:
    body, supplied = visual_request(head_row(), media(), "n0nce000", "schema")
    assert body["model"] == "llm.primary.longctx.mm" and body["temperature"] <= 0.1
    context = context_of(body)
    assert set(context) == {
        "task",
        "camera",
        "segment",
        "source_interval",
        "evidence",
        "caveats",
        "schema",
        "previous_reply_rejected",
    }
    user = json.dumps(body["messages"][1])
    for word in ("instrument", "metric", "°C", "m/s", "mm", "humidity", "gust", "12.4", "3.2"):
        assert word not in user, word
    images = [p for p in body["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(images) == len(supplied) == 4  # three frames and the keogram columns
    assert context["previous_reply_rejected"] == "schema"  # a code, never the model's text
    frames_only, ids = visual_request(head_row(), media(keogram=False), "n0nce000", None)
    assert all(not i.startswith("k-") for i in ids)
    assert "No keogram is available for this interval." in context_of(frames_only)["caveats"]
    big = Image("image/jpeg", b"x" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(CortexError):
        request_body("s", "t", [("a", big)], max_tokens=1)
    near = Image("image/jpeg", b"x" * MAX_IMAGE_BYTES)
    with pytest.raises(CortexError):
        request_body("s", "t", [(str(i), near) for i in range(5)], max_tokens=1)


# Call budget and the Cortex-down path ---------------------------------------------------------


def analyzer(script: Script, slot: Any = None, sleeps: list[float] | None = None) -> Analyzer:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return Analyzer(
        RawSink(),  # type: ignore[arg-type]
        script.cortex(),
        slot or CountingSlot(),  # type: ignore[arg-type]
        StaticMedia(),
        StaticWeather(station(T0, T0 + timedelta(hours=2))),
        profile_id="embed-main-2560-v1",
        sleep=sleep,
    )


def visual_call(unit: Analyzer, run_: Run) -> Any:
    ids: list[str] = []

    def build(note: str | None) -> dict[str, Any]:
        body, supplied = visual_request(head_row(), media(), run_.nonce, note)
        ids[:] = supplied
        return body

    def accept(reply: Any) -> Any:
        return validate.visual(
            reply.content, reply.finish_reason, supplied=ids, window=0, nonce=run_.nonce
        )

    return run(unit.call(run_, build, accept, stage="visual"))


@pytest.mark.parametrize(
    ("steps", "calls", "outcome"),
    [
        ([("down",)], 3, "cortex_unavailable"),
        ([("timeout",)], 3, "cortex_unavailable"),
        ([("status", 503)], 3, "cortex_unavailable"),
        ([("status", 429, {"retry-after": "7"})], 3, "cortex_busy"),
        ([("status", 401)], 1, "cortex_auth"),
        ([("status", 413)], 1, None),
        ([("visual", "malformed_json")], 2, None),
        ([("down",), ("down",), ("visual", "truncated"), ("visual", "free_text")], 4, None),
        ([("visual", "free_text"), ("down",), ("down",), ("down",)], 4, "cortex_unavailable"),
        ([("down",), ("visual", "valid")], 2, "accepted"),
        ([("visual", "free_text_field"), ("visual", "valid")], 2, "accepted"),
    ],
)
def test_at_most_four_calls_per_window_and_auth_or_media_stop(
    steps: list[Any], calls: int, outcome: str | None
) -> None:
    script = Script(steps)
    sleeps: list[float] = []
    unit = analyzer(script, sleeps=sleeps)
    state = Run(uuid4(), "n0nce000")
    if outcome and outcome.startswith("cortex_"):
        with pytest.raises(CortexDown) as down:
            visual_call(unit, state)
        assert down.value.reason == outcome
    else:
        result = visual_call(unit, state)
        assert (result is not None) == (outcome == "accepted")
    assert len(script.bodies) == state.calls == calls <= analyze.CALLS_PER_WINDOW
    if steps[0][0] == "status" and steps[0][1] == 429:
        assert sleeps == [7.0, 7.0]  # Retry-After honoured, bounded
    # Each attempt left a private raw record: an outcome code, the body only for replies.
    raw = unit.engine.rows  # type: ignore[attr-defined]
    assert len(raw) == calls and all(r["outcome"] for r in raw)


def test_a_timed_out_call_keeps_the_slot_for_the_grace_period() -> None:
    flags: list[bool] = []

    class Recording(CountingSlot):
        def hold(self, wait: float) -> Any:
            outer = super().hold(wait)

            class Wrapper:
                async def __aenter__(self) -> Any:
                    self.flag = await outer.__aenter__()
                    return self.flag

                async def __aexit__(self, *exc: Any) -> Any:
                    flags.append(self.flag.value)
                    return await outer.__aexit__(*exc)

            return Wrapper()

    unit = analyzer(Script([("timeout",), ("visual", "valid")]), slot=Recording())
    assert visual_call(unit, Run(uuid4(), "n0nce000")) is not None
    assert flags == [True, False]


def test_reply_bodies_never_reach_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    unit = analyzer(Script([("visual", "free_text"), ("visual", "valid")]))
    visual_call(unit, Run(uuid4(), "n0nce000"))
    assert "person" not in caplog.text and "broken" not in caplog.text
    assert "synthetic-key" not in caplog.text
    raw = unit.engine.rows  # type: ignore[attr-defined]
    # The free text stays in the private 24 h raw table only; it never reaches a result.
    assert "A person walks" in raw[0]["body"] and raw[0]["outcome"] == "schema"
    assert "broken" in raw[-1]["body"] and raw[-1]["outcome"] == "accepted"


# Events, search projection and scheduling ------------------------------------------------------


def test_visual_events_only_for_clearly_visible_weather_with_fixed_labels() -> None:
    row = head_row()
    visual_id = uuid4()
    observations = [
        {
            "id": "a",
            "category": "rain",
            "assessment": "visible",
            "confidence": "high",
            "description": "ignored text",
        },
        {
            "id": "b",
            "category": "rain",
            "assessment": "possible",
            "confidence": "high",
            "description": "x",
        },
        {
            "id": "c",
            "category": "fog_visibility",
            "assessment": "visible",
            "confidence": "low",
            "description": "x",
        },
        {
            "id": "d",
            "category": "cloud_cover",
            "assessment": "visible",
            "confidence": "high",
            "description": "x",
        },
    ]
    windows = {o["id"]: (T0, T0 + timedelta(hours=1)) for o in observations}
    (event,) = visual_events(row, observations, windows, visual_id)
    assert event.label == "Rain visible on camera" and event.origin == "visual"
    assert event.precision == "clip" and event.evidence == (visual_id,)
    assert event.key == f"visual:{row['video_id']}:2026-06-01T12:00:00Z:rain"


def test_the_search_projection_is_a_valid_document_set() -> None:
    from weather.search.documents import documents

    row = head_row()
    result = analyze.Result(
        state="complete",
        reason=None,
        claims=[{"text": "Clouds.", "basis": "visual", "evidence_ids": ["v"]}],
        limitations=[],
        observations=[
            {
                "id": "v",
                "category": "cloud_cover",
                "assessment": "visible",
                "confidence": "high",
                "sky_cover": "broken",
                "window": 1,
            }
        ],
        coverage={"windows_reviewed": 2, "windows_total": 2},
        inputs={"keogram": True},
        packet=PACKET,
        refresh="full",
        event_windows={"v": (T0 + timedelta(hours=1), T0 + timedelta(hours=2))},
    )
    text = analyze.video_text(row, 1, result, "Clouds.", None)
    rows = documents(text)
    assert [r["kind"] for r in rows] == ["video", "window"]
    assert rows[1]["media_start_seconds"] == 5.0 and rows[1]["media_end_seconds"] == 10.0
    assert "AI-generated · sampled-frame analysis" in rows[0]["text"]
    assert text.analysis_mode == "sampled_frames" and text.description_revision == 1
    # Window search text is template output, never model text (C18).
    assert text.windows[0].observed_conditions == "Broken clouds."


def test_nightly_retry_is_the_next_local_two_am_across_dst() -> None:
    assert store.next_night(datetime(2026, 6, 1, 7, 0, tzinfo=UTC)) == datetime(
        2026, 6, 1, 8, 0, tzinfo=UTC
    )  # 01:00 MDT -> 02:00 MDT
    assert store.next_night(datetime(2026, 6, 1, 8, 0, tzinfo=UTC)) == datetime(
        2026, 6, 2, 8, 0, tzinfo=UTC
    )
    assert store.next_night(datetime(2026, 12, 1, 20, 0, tzinfo=UTC)) == datetime(
        2026, 12, 2, 9, 0, tzinfo=UTC
    )  # MST


# Media --------------------------------------------------------------------------------------


def test_keogram_columns_cover_the_window_and_widen_for_reading() -> None:
    buffer = io.BytesIO()
    picture = PILImage.new("RGB", (1440, 64), (0, 0, 0))
    for x in range(1440):
        picture.putpixel((x, 0), (x % 256, 0, 0))
    picture.save(buffer, "PNG")
    crop = keogram_columns(buffer.getvalue(), 0.5, 0.75)
    with PILImage.open(io.BytesIO(crop.data)) as image:
        assert image.size == (360, 64) and image.getpixel((0, 0)) == (720 % 256, 0, 0)
    narrow = keogram_columns(buffer.getvalue(), 0.0, 0.01)
    with PILImage.open(io.BytesIO(narrow.data)) as image:
        assert image.width >= 256
    with pytest.raises(OSError):
        keogram_columns(b"not a png", 0, 1)


HAVE_FFMPEG = bool(os.environ.get("WEATHER_FFMPEG"))


@pytest.mark.skipif(
    not HAVE_FFMPEG and os.environ.get("WEATHER_CI_IN_CLUSTER") != "1",
    reason="FFmpeg runs in fleet CI (set WEATHER_FFMPEG to run locally)",
)
def test_one_ffmpeg_pass_returns_exactly_the_selected_frames(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    binary = os.environ.get("WEATHER_FFMPEG") or "ffmpeg"
    subprocess.run(
        [
            binary,
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=60",
            "-frames:v",
            "120",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(clip),
        ],
        check=True,
        timeout=120,
    )
    frames = tmp_path / "frames"
    frames.mkdir()
    images = sample_frames(clip, [0, 37, 119], frames)
    assert len(images) == 3 and all(i.mime == "image/jpeg" for i in images)
    assert not list(frames.iterdir())


# Manifests ----------------------------------------------------------------------------------


def test_intel_ships_disabled_with_reader_key_bounded_egress_and_one_call_smoke() -> None:
    root = yaml.safe_load((ROOT / "deploy/k8s/kustomization.yaml").read_text())
    assert "intel" not in root["resources"] and "intel" not in (root.get("components") or [])
    intel = ROOT / "deploy/k8s/intel"
    deployment = yaml.safe_load((intel / "intel.yaml").read_text())
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    pod = deployment["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert container["command"] == ["weather", "intel", "serve"]
    assert container["resources"]["limits"] and container["resources"]["requests"]
    secrets = {v.get("secret", {}).get("secretName") for v in pod["volumes"]} | {
        s["secret"]["name"]
        for v in pod["volumes"]
        for s in v.get("projected", {}).get("sources", [])
    }
    assert secrets - {None} == {
        "weather-cortex",
        "weather-media-s3-reader",
        "weather-pg-worker",
        "weather-pg-ca",
    }
    assert pod["automountServiceAccountToken"] is False
    policy = yaml.safe_load((intel / "network-policy.yaml").read_text())
    ports = {p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]}
    assert ports == {443, 8443}
    policy_doc, job = yaml.safe_load_all((intel / "smoke-job.yaml").read_text())
    for doc in (policy_doc, job):
        assert doc["metadata"]["labels"]["estate.vallery.net/disposable"] == "true"
    assert job["spec"]["activeDeadlineSeconds"] <= 300 and job["spec"]["backoffLimit"] == 0
    smoke = job["spec"]["template"]["spec"]
    assert smoke["containers"][0]["command"] == ["weather", "intel", "smoke"]
    assert [v["secret"]["secretName"] for v in smoke["volumes"]] == ["weather-cortex"]
