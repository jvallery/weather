# SPDX-License-Identifier: MIT
"""Event rules, write decisions, video links and the feed route without a database (#41)."""

import asyncio
import importlib.util
import socket
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from sqlalchemy import text
from weather.api import build_app
from weather.api.cursors import CursorCodec
from weather.api.events import MapEntry, PublishedSegment, build_event, video_links
from weather.api.models import Event, EventQuery, EventVideo
from weather.api.registry import PublicError
from weather.api.service import WeatherService
from weather.api.source import SourceUnavailable
from weather.events import job, rules
from weather.events.rules import Candidate, Observation
from weather.events.store import OVERRIDE_STALE, Current, decide
from weather.timemodel import weather_day

ROOT = Path(__file__).resolve().parents[2]
STATION = UUID(int=7)
VALID = {"valid": True, "reason": "valid", "invalid": []}
KEY = b"e" * 64


def obs(at: datetime, quality: dict[str, Any] | None = None, **values: Any) -> Observation:
    return Observation(uuid4(), STATION, at, values, quality or VALID)


def at(text_value: str) -> datetime:
    return datetime.fromisoformat(text_value.replace("Z", "+00:00"))


def current(candidate: Candidate, **changes: Any) -> Current:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "revision": 1,
        "visibility": "public",
        "withdrawal_reason": None,
        "author_kind": "rule",
        "evidence_hash": candidate.evidence_hash,
        "row": {},
    }
    fields.update(changes)
    return Current(**fields)


# Rules -----------------------------------------------------------------------------------------


def test_precipitation_onset_is_one_second_and_unknown_timing_stays_private() -> None:
    row = obs(at("2026-07-01T18:00:05Z"))
    onset = rules.precipitation_onset(row)
    assert onset is not None and onset.key == f"precipitation_onset:{row.id}"
    assert (onset.start, onset.end - onset.start) == (row.source_ts, timedelta(seconds=1))
    assert onset.precision == "second" and onset.evidence == (row.id,)
    # A future/invalid source time or a sub-second timestamp is not a defensible public interval.
    future = {"valid": False, "reason": "future_timestamp", "invalid": []}
    assert rules.precipitation_onset(obs(row.source_ts, future)) is None
    assert rules.precipitation_onset(obs(row.source_ts + timedelta(milliseconds=500))) is None


def test_retries_are_deterministic_and_same_second_events_stay_distinct() -> None:
    first, second = obs(at("2026-07-01T18:00:05Z")), obs(at("2026-07-01T18:00:05Z"))
    a, again = rules.precipitation_onset(first), rules.precipitation_onset(first)
    b = rules.precipitation_onset(second)
    assert a is not None and again is not None and b is not None
    assert a.evidence_hash == again.evidence_hash
    assert decide(again, current(a)) is None  # a retry or duplicate receipt is a no-op
    # Two canonical rows in the same second are two legitimate events, never merged.
    assert a.key != b.key and a.start == b.start


def test_gust_maximum_needs_a_positive_valid_gust() -> None:
    day = weather_day(date(2026, 7, 1))
    rows = [
        obs(day.start + timedelta(minutes=5), wind_gust=0),
        obs(day.start + timedelta(minutes=6), wind_gust=None),
        obs(day.start + timedelta(minutes=7), {**VALID, "invalid": ["wind_gust"]}, wind_gust=30),
        obs(day.start + timedelta(minutes=8), {**VALID, "valid": False}, wind_gust=40),
    ]
    assert rules.daily_gust(day, STATION, rows) is None
    assert rules.daily_gust(day, STATION, []) is None


def test_gust_tie_prefers_earlier_source_time_then_lower_id() -> None:
    day = weather_day(date(2026, 7, 1))
    late = obs(day.start + timedelta(hours=9), wind_gust=12.5)
    early = obs(day.start + timedelta(hours=3), wind_gust=12.5)
    lower = obs(day.start + timedelta(hours=1), wind_gust=7.0)
    winner = rules.daily_gust(day, STATION, [late, lower, early])
    assert winner is not None and winner.evidence == (early.id,)
    same = day.start + timedelta(hours=2)
    one = Observation(UUID(int=1), STATION, same, {"wind_gust": 9.0}, VALID)
    two = Observation(UUID(int=2), STATION, same, {"wind_gust": 9.0}, VALID)
    tied = rules.daily_gust(day, STATION, [two, one])
    assert tied is not None and tied.evidence == (one.id,)
    # The report minute ending at the source time is the support.
    assert (tied.start, tied.end) == (same - timedelta(minutes=1), same)


def test_late_higher_gust_revises_the_same_key() -> None:
    day = weather_day(date(2026, 7, 1))
    rows = [obs(day.start + timedelta(hours=4), wind_gust=10.0)]
    before = rules.daily_gust(day, STATION, rows)
    after = rules.daily_gust(
        day, STATION, [*rows, obs(day.start + timedelta(hours=5), wind_gust=11)]
    )
    assert before is not None and after is not None
    assert before.key == after.key == rules.gust_key(STATION, day.local_date)
    assert before.evidence_hash != after.evidence_hash
    assert decide(after, current(before)) == "public"
    assert after.measurement["gust_mps"] == 11.0


@pytest.mark.parametrize(("local", "hours"), [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_dst_days_have_their_true_length_and_coverage(local: date, hours: int) -> None:
    day = weather_day(local)
    assert day.end - day.start == timedelta(hours=hours)
    # One report each minute ending in (start, end]: full coverage of a 23/25-hour day.
    minutes = int((day.end - day.start).total_seconds() // 60)
    rows = [obs(day.start + timedelta(minutes=n + 1), wind_gust=1.0) for n in range(minutes)]
    gust = rules.daily_gust(day, STATION, rows)
    assert gust is not None
    assert gust.measurement["coverage"] == {
        "fraction": 1.0,
        "support_seconds": hours * 3600.0,
        "expected_seconds": hours * 3600.0,
    }
    # A report at the day's first instant belongs to the previous day (support ends there).
    assert rules.daily_gust(day, STATION, [obs(day.start, wind_gust=5.0)]) is None
    assert gust.local_dates() == [local]


def test_cross_midnight_interval_links_both_local_days() -> None:
    day = weather_day(date(2026, 10, 31))
    report = obs(day.end + timedelta(seconds=30), lightning_strike_count=2)
    event = rules.lightning_interval(report, "interval")
    assert event is not None and event.start < day.end < event.end
    assert event.local_dates() == [date(2026, 10, 31), date(2026, 11, 1)]


def test_lightning_authority_selects_exactly_one_family_with_integer_counts() -> None:
    strike = obs(at("2026-07-01T18:00:05Z"), distance=12, energy=300)
    report = obs(at("2026-07-01T18:01:00Z"), lightning_strike_count=3)
    assert rules.lightning_strike(strike, "event") is not None
    assert rules.lightning_interval(report, "event") is None
    assert rules.lightning_strike(strike, "interval") is None
    interval = rules.lightning_interval(report, "interval")
    assert interval is not None and interval.measurement["strikes"] == 3
    assert type(interval.measurement["strikes"]) is int
    assert (interval.start, interval.end) == (
        report.source_ts - timedelta(minutes=1),
        report.source_ts,
    )
    five = rules.lightning_interval(
        obs(report.source_ts, lightning_strike_count=2, report_interval=5), "interval"
    )
    assert five is not None and five.end - five.start == timedelta(minutes=5)
    for count in (0, 1.5, None):
        bad = obs(report.source_ts, lightning_strike_count=count)
        assert rules.lightning_interval(bad, "interval") is None
    assert rules.derive("lightning_interval", report, "event") is None
    assert rules.LIGHTNING_AUTHORITY in ("event", "interval")


def test_visual_events_keep_their_clip_window_and_automatic_events_need_evidence() -> None:
    start = at("2026-07-01T18:00:00Z")
    base: dict[str, Any] = {
        "key": "visual:clip",
        "family": "visual",
        "origin": "visual",
        "start": start,
        "end": start + timedelta(minutes=4),
        "rule_revision": "model/1",
        "evidence": (uuid4(),),
        "label": "Clouds clear",
    }
    clip = Candidate(**base, precision="clip")
    assert clip.evidence_basis == "visual" and clip.end - clip.start == timedelta(minutes=4)
    with pytest.raises(ValueError):
        Candidate(**{**base, "precision": "second"})
    with pytest.raises(ValueError):
        Candidate(**{**base, "evidence": ()}, precision="clip")
    with pytest.raises(ValueError):
        Candidate(**{**base, "evidence": (base["evidence"][0],) * 2}, precision="clip")
    with pytest.raises(ValueError):
        Candidate(**{**base, "basis": "instrument"}, precision="clip")
    with pytest.raises(ValueError):
        Candidate(**{**base, "end": start}, precision="clip")


def test_measured_rules_never_need_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise OSError("inference unavailable")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    day = weather_day(date(2026, 7, 1))
    assert rules.precipitation_onset(obs(day.start + timedelta(hours=1))) is not None
    assert rules.daily_gust(day, STATION, [obs(day.start + timedelta(hours=1), wind_gust=3)])


# Write decisions -------------------------------------------------------------------------------


def test_override_survives_unchanged_evidence_and_yields_to_changed_evidence() -> None:
    day = weather_day(date(2026, 7, 1))
    gust = rules.daily_gust(day, STATION, [obs(day.start + timedelta(hours=1), wind_gust=3)])
    assert gust is not None
    human = current(gust, author_kind="operator", visibility="private")
    assert decide(gust, human) is None
    higher = rules.daily_gust(day, STATION, [obs(day.start + timedelta(hours=2), wind_gust=9)])
    assert decide(higher, human) == "withdrawn"
    # Once stale, only an operator settles it; reruns write nothing.
    stale = current(gust, visibility="withdrawn", withdrawal_reason=OVERRIDE_STALE)
    assert decide(higher, stale) is None and decide(None, stale) is None


def test_withdrawal_is_immediate_and_idempotent() -> None:
    onset = rules.precipitation_onset(obs(at("2026-07-01T18:00:05Z")))
    assert onset is not None
    assert decide(None, current(onset)) == "withdrawn"
    withdrawn = current(onset, visibility="withdrawn", withdrawal_reason="evidence_invalid")
    assert decide(None, withdrawn) is None
    assert decide(onset, withdrawn) == "public"  # re-qualified evidence republishes
    assert decide(None, None) is None


def test_closed_days_wait_for_grace_and_cover_the_window() -> None:
    end = weather_day(date(2026, 11, 1)).end + job.GRACE
    days = job.closed_days(end - job.LOOKBACK, end)
    assert days == [date(2026, 10, 31), date(2026, 11, 1)]
    assert job.closed_days(end - job.LOOKBACK, end - timedelta(seconds=1)) == [date(2026, 10, 31)]


# Video links -----------------------------------------------------------------------------------


def segment(camera: str, kind: str, start: datetime, end: datetime) -> PublishedSegment:
    frames = 3600
    return PublishedSegment(
        camera=camera,  # type: ignore[arg-type]
        segment=kind,
        cycle_date=date(2026, 7, 1),
        video_id=f"{camera}-{kind}",
        start=start,
        end=end,
        fps=Fraction(60),
        time_map=(MapEntry(0, frames, start, end),),
    )


def test_cross_dusk_links_both_segments_without_double_counting() -> None:
    # Synthetic civil-twilight bounds; no site coordinates are needed for the overlap.
    dawn, dusk = at("2026-07-01T11:30:00Z"), at("2026-07-02T03:05:00Z")
    segments = [
        segment("skycam_east", "daytime", dawn, dusk),
        segment("skycam_east", "overnight", dusk, dawn + timedelta(days=1)),
    ]
    start, end = dusk - timedelta(minutes=10), dusk + timedelta(minutes=10)
    links = video_links(start, end, "skycam_east", segments)
    assert [link.segment for link in links] == ["daytime", "overnight"]
    total = timedelta()
    for link, video in zip(links, segments, strict=True):
        assert link.state == "available" and link.seek is not None
        source = link.seek.source_interval
        assert start <= source.start < source.end <= end
        assert video.start <= source.start and source.end <= video.end
        assert 0 <= link.seek.start_seconds <= link.seek.end_seconds <= 60
        total += source.end - source.start
    assert total == end - start  # nonoverlapping: the event is never counted twice


def test_station_wide_event_marks_a_camera_without_footage_unavailable() -> None:
    start = at("2026-07-01T18:00:00Z")
    segments = [
        segment("skycam_east", "daytime", start - timedelta(hours=6), start + timedelta(hours=6)),
        segment("skycam_west", "daytime", start - timedelta(hours=6), start + timedelta(hours=6)),
    ]
    links = video_links(start, start + timedelta(seconds=1), None, segments)
    states = {link.camera: link.state for link in links}
    assert states == {
        "skycam_east": "available",
        "skycam_west": "available",
        "frontyard_1": "unavailable",
    }
    missing = next(link for link in links if link.camera == "frontyard_1")
    assert missing.seek is None and missing.video_id is None
    assert all(
        link.state == "unavailable"
        for link in video_links(start, start + timedelta(seconds=1), None, [])
    )
    with pytest.raises(ValueError):
        EventVideo(
            camera="frontyard_1",
            state="unavailable",
            video_id="x",
            cycle_date=None,
            segment=None,
            seek=None,
        )


# Public model and feed route -------------------------------------------------------------------


def row(**changes: Any) -> dict[str, Any]:
    start = at("2026-07-01T18:00:05Z")
    values: dict[str, Any] = {
        "id": uuid4(),
        "revision": 1,
        "kind": "precipitation_onset",
        "origin": "measured",
        "basis": "instrument",
        "camera": None,
        "starts_at": start,
        "ends_at": start + timedelta(seconds=1),
        "time_precision": "second",
        "rule_revision": "precipitation_onset/1",
        "human_edited": False,
        "label": None,
        "measurement": {},
        "evidence_ids": [uuid4()],
        "updated_at": start,
        "local_dates": [date(2026, 7, 1)],
        "snapshot": "100:105:",
    }
    values.update(changes)
    return values


def test_event_serializes_origin_basis_and_rejects_dishonest_shapes() -> None:
    clip = build_event(
        row(
            kind="visual",
            origin="visual",
            basis="visual",
            time_precision="clip",
            label="Clouds clear",
            camera="skycam_west",
            ends_at=at("2026-07-01T18:04:05Z"),
        )
    )
    data = clip.model_dump(mode="json")
    assert (data["origin"], data["basis"], data["precision"]) == ("visual", "visual", "clip")
    assert data["source_interval"] == {
        "start": "2026-07-01T18:00:05Z",
        "end": "2026-07-01T18:04:05Z",
    }
    assert [video["camera"] for video in data["videos"]] == ["skycam_west"]
    assert build_event(row()).label == "Precipitation onset"
    for bad in (
        {"basis": "visual"},
        {"evidence_ids": []},
        {"origin": "manual", "basis": "instrument", "kind": "manual", "label": "Note"},
        {"local_dates": []},
        {"ends_at": at("2026-07-01T18:00:05Z")},
        {"kind": "synthesis", "origin": "synthesis", "basis": "inference", "label": "x"},
    ):
        with pytest.raises(ValueError):
            build_event(row(**bad))
    manual = build_event(
        row(origin="manual", kind="manual", label="Hail", human_edited=True, evidence_ids=[])
    )
    assert isinstance(manual, Event) and manual.human_edited


class FakeReader:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.down = False

    async def events(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if self.down:
            raise SourceUnavailable()
        self.calls.append(parameters)
        after = (parameters["after_start"], parameters["after_id"])
        rows = sorted(self.rows, key=lambda item: (item["starts_at"], item["id"]))
        if parameters["event_id"]:
            rows = [item for item in rows if item["id"] == parameters["event_id"]]
        rows = [item for item in rows if (item["starts_at"], item["id"]) > after]
        return rows[: parameters["rows"]]

    async def ping(self) -> bool:
        return True


def weather(reader: FakeReader, now: datetime) -> WeatherService:
    return WeatherService(
        reader,  # type: ignore[arg-type]
        CursorCodec(KEY),
        registry=CollectorRegistry(),
        clock=lambda: now,
    )


def test_feed_pages_with_a_bound_cursor_and_hides_missing_events() -> None:
    now = at("2026-07-02T00:00:00Z")
    reader = FakeReader()
    reader.rows = [
        row(
            starts_at=at("2026-07-01T18:00:05Z") + timedelta(minutes=n),
            ends_at=at("2026-07-01T18:00:06Z") + timedelta(minutes=n),
        )
        for n in range(3)
    ]
    client = TestClient(build_app(weather(reader, now)))
    first = client.get("/api/v1/events", params={"limit": 2, "day": "2026-07-01"})
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    body = first.json()
    assert len(body["items"]) == 2 and body["next_cursor"]
    assert reader.calls[-1]["snapshot"] is None and reader.calls[-1]["day"] == date(2026, 7, 1)
    second = client.get(
        "/api/v1/events", params={"limit": 2, "day": "2026-07-01", "cursor": body["next_cursor"]}
    ).json()
    assert reader.calls[-1]["snapshot"] == "100:105:"
    ids = [item["id"] for item in body["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 3 and second["next_cursor"] is None
    # The cursor is bound to its filters.
    changed = client.get(
        "/api/v1/events", params={"limit": 2, "day": "2026-07-02", "cursor": body["next_cursor"]}
    )
    assert changed.status_code == 400 and changed.json()["code"] == "invalid_cursor"
    assert client.get("/api/v1/events", params={"limit": 101}).status_code == 400
    assert client.get("/api/v1/events", params={"camera": "porch"}).status_code == 400
    found = client.get(f"/api/v1/events/{reader.rows[0]['id']}")
    assert found.status_code == 200 and found.json()["id"] == str(reader.rows[0]["id"])
    missing = client.get(f"/api/v1/events/{uuid4()}")
    assert missing.status_code == 404 and missing.json() == {
        "code": "not_found",
        "message": "No such public event.",
        "suggested_resolution_seconds": None,
    }
    reader.down = True
    assert client.get("/api/v1/events").status_code == 503


def test_event_cursor_rejects_tampering_and_oversized_snapshots() -> None:
    codec, now = CursorCodec(KEY), at("2026-07-02T00:00:00Z")
    query = EventQuery(limit=10)
    token = codec.encode_events(
        query, snapshot="5:9:6,7", after_start=now, after_id=UUID(int=3), now=now
    )
    position = codec.decode_events(token, query, now=now)
    assert (position.snapshot, position.after_id) == ("5:9:6,7", UUID(int=3))
    with pytest.raises(PublicError):
        codec.decode_events(token[:-2] + "AA", query, now=now)
    with pytest.raises(PublicError):
        codec.decode_events(token, EventQuery(limit=10, order="desc"), now=now)
    with pytest.raises(PublicError):
        codec.decode_events(token, query, now=now + timedelta(days=2))
    with pytest.raises(PublicError):
        codec.encode_events(
            query,
            snapshot="1:2:" + ",".join(["1"] * 65),
            after_start=now,
            after_id=UUID(int=1),
            now=now,
        )


def test_migration_is_inline_and_carries_its_checks() -> None:
    spec = importlib.util.spec_from_file_location(
        "migration_0005", ROOT / "app/migrations/versions/0005_events.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ddl = "\n".join(module.statements())
    assert "CHECK (starts_at < ends_at)" in ddl
    assert "origin='measured' AND basis='instrument'" in ddl
    assert "WITH (security_barrier=true)" in ddl
    assert "import weather" not in (ROOT / "app/migrations/versions/0005_events.py").read_text()
    for statement in module.statements():
        assert not text(statement)._bindparams
    assert module.down_revision == "0004_search"


def test_scheduler_routes_the_event_topic_before_any_write() -> None:
    from weather.events.store import TOPIC
    from weather.scheduler.runner import installed_registry

    registry = installed_registry()
    assert TOPIC in registry.topics() and {"events", "weather_event_changed"} <= registry.kinds()


def test_enqueue_key_and_deadline_are_fixed_per_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def capture(connection: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(job.jobs, "enqueue", capture)
    for second in (0, 59, 299):
        asyncio.run(job.enqueue(None, at("2026-07-01T18:00:00Z") + timedelta(seconds=second)))  # type: ignore[arg-type]
    assert len({(c["key"], c["deadline"], str(c["payload"])) for c in calls}) == 1
    assert calls[0]["key"] == "events:2026-07-01T18:00Z"
    assert calls[0]["not_before"] == at("2026-07-01T18:05:00Z")
    assert calls[0]["not_before"].tzinfo == UTC


def test_web_fixture_matches_the_public_event_model() -> None:
    import json

    from weather.api.models import Events

    fixture = json.loads((ROOT / "web/app/fixtures/events.json").read_text())
    events = Events.model_validate(fixture)
    assert {item.origin for item in events.items} == {"measured", "visual", "manual"}
    assert Events.model_validate(events.model_dump(mode="json")) == events
