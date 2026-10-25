# SPDX-License-Identifier: MIT
"""Hourly stills without a database: selection goldens, DST slots, sanitized derivatives and
the public serializer (#38)."""

import asyncio
import io
import json
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from weather.api import build_app
from weather.api.registry import PublicError
from weather.api.stills import StillQuery, assemble, parse_query, slots
from weather.pipeline.hourly import HourlyArchive
from weather.pipeline.intake import Candidate, Expected
from weather.stills.policy import POLICY, choose
from weather.stills.sanitize import SIZES, derive, metadata
from weather.timemodel import hour_slots

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/time-model-vectors.json").read_text())
SLOT = int(datetime(2026, 10, 23, 18, 0, tzinfo=UTC).timestamp())
ANCHOR = SLOT + 1800
FORBIDDEN = re.compile(
    r"weather-originals|bucket|object_key|sha256|original|chunk|member|attempt|[0-9a-f]{64}"
)


def capture(time: Fraction | int, attempt: str = "a", reject: str | None = None) -> Candidate:
    t = Fraction(time)
    return Candidate(
        slot=t,
        time=t,
        upper=t + Fraction(1, 10),
        freshness="plausible",
        uncertainty_ms=50.0,
        attempt_id=attempt,
        chunk=Path(f"/staging/sealed/skycam_east/{attempt}.tar"),
        member=f"{attempt}.jpg",
        sha256="0" * 64,
        size=10,
        reject=reject,
    )


def ok(candidate: Candidate) -> tuple[bytes, bytes]:
    return candidate.attempt_id.encode(), b"rgb"


# Selection goldens ---------------------------------------------------------------------------


def test_the_capture_nearest_the_half_hour_wins_and_within_60_s_is_on_target() -> None:
    choice = choose(
        [capture(ANCHOR - 40, "early"), capture(ANCHOR + 7, "near"), capture(SLOT, "start")],
        SLOT,
        ok,
    )
    assert (choice.state, choice.winner and choice.winner.attempt_id) == ("on_target", "near")
    assert choice.offset == 7 and choice.offset_us == 7_000_000 and choice.eligible == 3
    assert choice.jpeg == b"near" and choice.reason is None
    # Exactly 60 s either side is on target; a microsecond more is off target.
    for delta, state in ((60, "on_target"), (-60, "on_target")):
        assert choose([capture(ANCHOR + delta)], SLOT, ok).state == state
    late = choose([capture(ANCHOR + 60 + Fraction(1, 10**6))], SLOT, ok)
    assert late.state == "off_target" and late.offset_us == 60_000_001
    assert POLICY == "nearest-half-hour/v1"


def test_off_target_keeps_the_same_hour_and_its_offset() -> None:
    choice = choose([capture(SLOT, "first"), capture(SLOT + 3599, "last")], SLOT, ok)
    assert choice.state == "off_target" and choice.winner is not None
    assert choice.winner.attempt_id == "last" and choice.offset == 1799


def test_ties_go_to_the_earlier_source_time_then_the_capture_id() -> None:
    earlier = choose([capture(ANCHOR + 10, "b"), capture(ANCHOR - 10, "z")], SLOT, ok)
    assert earlier.winner is not None and earlier.winner.attempt_id == "z"
    same = choose([capture(ANCHOR, "b"), capture(ANCHOR, "a")], SLOT, ok)
    assert same.winner is not None and same.winner.attempt_id == "a"


def test_selection_ignores_arrival_order_and_never_borrows_another_hour() -> None:
    hour = [capture(ANCHOR + s, f"c{s}") for s in (-900, -300, 120, 1700)]
    # Captures of the neighbouring hours are nearer the anchor than nothing, but never chosen.
    neighbours = [capture(SLOT - 1, "previous"), capture(SLOT + 3600, "next")]
    picks = {
        choose(list(order), SLOT, ok).winner.attempt_id  # type: ignore[union-attr]
        for order in (hour + neighbours, neighbours + hour[::-1], hour[2:] + hour[:2])
    }
    assert picks == {"c120"}
    empty = choose(neighbours, SLOT, ok)
    assert (empty.state, empty.reason, empty.eligible) == ("missing", "no_capture", 0)


def test_rejected_and_undecodable_captures_fall_back_or_leave_the_slot_missing() -> None:
    candidates = [
        capture(ANCHOR, "geometry", reject="geometry"),
        capture(ANCHOR + 1, "timing", reject="unknown_timing"),
        capture(ANCHOR + 2, "corrupt"),
        capture(ANCHOR + 90, "good"),
    ]

    def decode(candidate: Candidate) -> tuple[bytes, bytes] | str:
        return "decode" if candidate.attempt_id == "corrupt" else ok(candidate)

    choice = choose(candidates, SLOT, decode)
    assert choice.winner is not None and choice.winner.attempt_id == "good"
    assert choice.state == "off_target" and choice.offset == 90
    assert choice.rejections == {"decode": 1, "geometry": 1, "unknown_timing": 1}
    missing = choose(candidates[:3], SLOT, decode)
    assert (missing.state, missing.reason, missing.winner) == (
        "missing",
        "no_eligible_capture",
        None,
    )
    assert missing.rejections == {"decode": 1, "geometry": 1, "unknown_timing": 1}
    with pytest.raises(ValueError):
        choose([], SLOT + 1, ok)


def test_due_slots_wait_for_sealing_and_stop_at_the_lookback() -> None:
    archive = HourlyArchive(
        camera="skycam_east",
        engine=None,  # type: ignore[arg-type]
        publisher=None,  # type: ignore[arg-type]
        index=None,  # type: ignore[arg-type]
        expected=Expected("skycam_east", 2000, 0, 32, 16),
    )
    hour = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
    # Just after the hour its chunks may still be sealing; a few minutes later it is due.
    assert hour - timedelta(hours=1) not in archive.due(hour + timedelta(seconds=30))
    due = archive.due(hour + timedelta(minutes=5))
    assert due[-1] == hour - timedelta(hours=1) and len(due) == 12
    assert due == sorted(due) and due[0] == hour - timedelta(hours=12)


# DST: 23 and 25 slots ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", VECTORS["civil_calendar_days"], ids=lambda case: case["local_date"]
)
def test_hour_slots_match_the_calendar_vectors(case: dict[str, Any]) -> None:
    day = date.fromisoformat(case["local_date"])
    found = hour_slots(day)
    assert len(found) == case["slot_count"]
    for slot, expected in zip(found, case["slots"], strict=True):
        assert slot.start == datetime.fromisoformat(expected["start"])
        assert (slot.local_label, slot.fold) == (expected["local_label"], expected["fold"])
        assert slot.utc_offset_minutes == int(expected["local_label"][-6:-3]) * 60
    listed = slots(StillQuery(day, day))
    assert [s.start for s in listed] == [s.start for s in found]


def test_the_fall_day_has_two_distinct_01_00_slots_and_spring_has_no_02_00() -> None:
    fall = slots(StillQuery(date(2026, 11, 1), date(2026, 11, 1), hour=1))
    assert [(s.utc_offset_minutes, s.fold) for s in fall] == [(-360, 0), (-420, 1)]
    assert fall[1].start - fall[0].start == timedelta(hours=1)
    assert slots(StillQuery(date(2026, 3, 8), date(2026, 3, 8), hour=2)) == []


# Sanitized derivatives -----------------------------------------------------------------------


def camera_jpeg(width: int, height: int) -> tuple[bytes, bytes]:
    """A camera-like original carrying EXIF with GPS, XMP, ICC and a comment."""
    rgb = np.random.default_rng(3).integers(0, 255, (height, width, 3), dtype=np.uint8)
    image = Image.fromarray(rgb, "RGB")
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"
    exif[0x8825] = {1: "N", 2: (39.0, 44.0, 21.0), 3: "W", 4: (104.0, 59.0, 25.0)}
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        exif=exif.tobytes(),
        comment=b"private comment",
        icc_profile=b"\x00" * 128,
        xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/">http://ns.adobe.com/xap/1.0/</x:xmpmeta>',
    )
    return buffer.getvalue(), rgb.tobytes()


def test_derivatives_carry_no_exif_xmp_iptc_icc_comment_or_gps() -> None:
    original, rgb = camera_jpeg(2400, 1100)
    found = set(metadata(original))
    assert {"exif", "comment", "icc"} <= found
    derivatives = derive(rgb, 2400, 1100)
    assert [(d.role, d.extension, d.width) for d in derivatives] == [
        (role, extension, width)
        for width, jpeg, webp in SIZES
        for role, extension in ((jpeg, "jpg"), (webp, "webp"))
    ]
    for item in derivatives:
        assert metadata(item.data) == []
        assert b"Exif" not in item.data and b"private comment" not in item.data
        assert b"ns.adobe.com" not in item.data
        opened = Image.open(io.BytesIO(item.data))
        assert opened.size == (item.width, item.height)
        assert not opened.getexif() and "icc_profile" not in opened.info
        assert "comment" not in opened.info and "xmp" not in opened.info
    # Full view at 1920 wide, aspect kept, never upscaled; the same bytes on a rerun.
    assert (derivatives[0].width, derivatives[0].height) == (1920, 880)
    assert [d.data for d in derive(rgb, 2400, 1100)] == [d.data for d in derivatives]
    small = derive(bytes(600 * 300 * 3), 600, 300)
    assert [d.width for d in small] == [600, 600, 600, 600, 480, 480]


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def chunk(fourcc: bytes, payload: bytes) -> bytes:
    padded = payload + (b"\x00" if len(payload) % 2 else b"")
    return fourcc + len(payload).to_bytes(4, "little") + padded


def riff(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


@pytest.mark.parametrize(
    ("marker", "payload", "found"),
    [
        (0xE1, b"Exif\x00\x00MM\x00*" + b"GPS" * 4, "exif"),
        (0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>", "xmp"),
        (0xED, b"Photoshop 3.0\x008BIM\x04\x04", "iptc"),
        (0xE2, b"ICC_PROFILE\x00\x01\x01" + b"\x00" * 16, "icc"),
        (0xEB, b"JP\x00\x00", "app"),
        (0xFE, b"camera address", "comment"),
        (0xE0, b"JFXX\x00\x10", "app0"),
    ],
)
def test_the_jpeg_scanner_finds_each_metadata_segment(
    marker: int, payload: bytes, found: str
) -> None:
    clean = io.BytesIO()
    Image.new("RGB", (16, 8), (10, 20, 30)).save(clean, format="JPEG")
    data = clean.getvalue()
    assert metadata(data) == []
    # Spliced after the JFIF header, as a camera writes it; the scan still walks the scan data.
    jfif_end = 4 + int.from_bytes(data[4:6], "big")
    assert metadata(data[:jfif_end] + segment(marker, payload) + data[jfif_end:]) == [found]
    assert "trailing" in metadata(data + b"tail")
    assert "structure" in metadata(data[:-2])


def test_the_webp_scanner_finds_metadata_chunks_and_flags() -> None:
    clean = io.BytesIO()
    Image.new("RGB", (16, 8), (10, 20, 30)).save(clean, format="WEBP")
    data = clean.getvalue()
    assert metadata(data) == []
    image = data[12:]  # the VP8 chunk
    vp8x = chunk(
        b"VP8X", bytes([0, 0, 0, 0]) + (15).to_bytes(3, "little") + (7).to_bytes(3, "little")
    )
    assert metadata(riff(vp8x, image)) == []
    for fourcc, found in (
        (b"EXIF", "exif"),
        (b"XMP ", "xmp"),
        (b"ICCP", "icc"),
        (b"META", "chunk"),
    ):
        assert metadata(riff(vp8x, image, chunk(fourcc, b"GPS 39.7"))) == [found]
    for flag in (0x20, 0x08, 0x04):
        flagged = chunk(b"VP8X", bytes([flag, 0, 0, 0]) + bytes(6))
        assert metadata(riff(flagged, image)) == ["flags"]
    # Pillow's own EXIF/XMP WebP output is caught too.
    tagged = io.BytesIO()
    Image.new("RGB", (16, 8)).save(tagged, format="WEBP", exif=b"Exif\x00\x00II*\x00", xmp=b"<x/>")
    assert {"exif", "xmp"} <= set(metadata(tagged.getvalue()))
    assert metadata(b"GIF89a") == ["format"] and metadata(data[:-3]) == ["structure"]


# The public API ------------------------------------------------------------------------------


class Source:
    def __init__(self, rows: list[dict[str, Any]], assets: dict[UUID, dict[str, Any]]) -> None:
        self.rows, self._assets = rows, assets
        self.queries: list[StillQuery] = []

    async def stills(self, query: StillQuery) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self.rows

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        return {i: self._assets[i] for i in ids if i in self._assets}


def selected_row(start: datetime, **changes: Any) -> tuple[dict[str, Any], dict[UUID, Any]]:
    ids = [uuid4(), uuid4()]
    assets = {
        ids[0]: {
            "id": ids[0],
            "class": "still",
            "content_type": "image/jpeg",
            "bytes": 9,
            "width": 480,
            "height": 218,
        },
        ids[1]: {
            "id": ids[1],
            "class": "still",
            "content_type": "image/webp",
            "bytes": 8,
            "width": 1920,
            "height": 870,
        },
    }
    source = start + timedelta(minutes=30, seconds=12, microseconds=400000)
    row = {
        "camera_id": "skycam_east",
        "slot_starts_at": start,
        "local_date": date(2025, 11, 2),
        "revision": 1,
        "policy": POLICY,
        "state": "on_target",
        "reason": None,
        "source_ts": source,
        "capture_upper": source + timedelta(milliseconds=100),
        "offset_us": 12_400_000,
        "cycle_date": date(2025, 11, 1),
        "segment_kind": "overnight",
        "asset_ids": ids,
        **changes,
    }
    return row, assets


def test_stills_list_every_slot_with_pending_missing_and_no_private_field() -> None:
    first = datetime(2025, 11, 2, 6, 0, tzinfo=UTC)
    row, assets = selected_row(first + timedelta(hours=1))
    app = build_app()
    app.state.stills = Source([row], assets)
    client = TestClient(app)
    response = client.get("/api/v1/stills", params={"date": "2025-11-02", "camera": "skycam_east"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    text = response.text
    assert not FORBIDDEN.search(text.replace(POLICY, ""))
    items = response.json()["items"]
    assert len(items) == 25
    one = next(i for i in items if i["slot_id"] == "2025-11-02T07:00:00Z")
    assert one["calendar_date"] == "2025-11-02" and one["cycle_date"] == "2025-11-01"
    assert one["local_label"] == "2025-11-02T01:00:00-06:00" and one["fold"] == 0
    assert one["offset_seconds"] == 12.4 and one["state"] == "on_target"
    assert [image["width"] for image in one["images"]] == [1920, 480]
    assert all(image["url"] == f"/media/{image['asset_id']}" for image in one["images"])
    repeated = next(i for i in items if i["slot_id"] == "2025-11-02T08:00:00Z")
    assert repeated["local_label"] == "2025-11-02T01:00:00-07:00" and repeated["fold"] == 1
    # A slot without a visible row: missing once the selection window has passed.
    assert repeated["state"] == "missing" and repeated["reason"] == "not_selected"
    assert repeated["images"] == [] and repeated["source_interval"] is None
    both = client.get("/api/v1/stills", params={"date": "2025-11-02"}).json()["items"]
    assert len(both) == 75


def test_recent_unselected_slots_are_pending() -> None:
    now = datetime(2025, 11, 2, 12, 5, tzinfo=UTC)
    query = StillQuery(date(2025, 11, 2), date(2025, 11, 2), "skycam_west", 5)
    items = asyncio.run(assemble(Source([], {}), query, now))
    assert [(i.slot_id, i.state, i.reason) for i in items] == [
        ("2025-11-02T12:00:00Z", "pending", None)
    ]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"date": "2026-11-01", "from": "2026-10-01", "to": "2026-10-02"},
        {"from": "2026-10-01", "to": "2026-10-03", "hour": "1"},
        {"from": "2026-10-01", "to": "2026-10-03", "camera": "skycam_east"},
        {"from": "2026-10-01", "to": "2026-11-01", "camera": "skycam_east", "hour": "1"},
        {"date": "2026-11-31"},
        {"date": "2026-11-01", "hour": "24"},
        {"date": "2026-11-01", "camera": "doorbell"},
        {"date": "2026-11-01", "original": "1"},
        {"date": "1999-12-31"},
        {"date": "2101-01-01"},
        {"date": "9999-12-31"},
    ],
)
def test_invalid_or_unbounded_queries_are_rejected(params: dict[str, str]) -> None:
    with pytest.raises(PublicError):
        parse_query(params)


def test_a_compare_query_is_one_camera_hour_over_at_most_31_days() -> None:
    query = parse_query(
        {"from": "2026-10-02", "to": "2026-11-01", "camera": "skycam_east", "hour": "1"}
    )
    assert (query.first, query.last, query.hour) == (date(2026, 10, 2), date(2026, 11, 1), 1)
    # 31 days with one fall-back day: 32 slots, well inside the 100-item bound.
    assert len(slots(query)) == 32


def test_extreme_dates_are_a_problem_400_not_a_500() -> None:
    app = build_app()
    app.state.stills = Source([], {})
    response = TestClient(app).get("/api/v1/stills", params={"date": "9999-12-31"})
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")


def test_hourly_decode_retries_timeouts_and_stderr_but_rejects_real_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from weather.pipeline import ffmpeg

    def run(code: int, out: bytes, err: bytes) -> Any:
        return lambda _data: subprocess.CompletedProcess([], code, out, err)

    monkeypatch.setattr(ffmpeg, "_decode", run(0, b"x" * 6, b""))
    assert ffmpeg.decode_capture(b"", 2, 1) == b"x" * 6
    monkeypatch.setattr(ffmpeg, "_decode", run(1, b"", b"Invalid data"))
    assert ffmpeg.decode_capture(b"", 2, 1) is None
    monkeypatch.setattr(ffmpeg, "_decode", run(0, b"x" * 3, b""))
    assert ffmpeg.decode_capture(b"", 2, 1) is None
    monkeypatch.setattr(ffmpeg, "_decode", run(0, b"x" * 6, b"warning"))
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.decode_capture(b"", 2, 1)

    def timeout(_data: bytes) -> Any:
        raise subprocess.TimeoutExpired("ffmpeg", 30)

    monkeypatch.setattr(ffmpeg, "_decode", timeout)
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.decode_capture(b"", 2, 1)
    # The renderer keeps its per-slot reject for the same cases.
    assert ffmpeg.decode_jpeg(b"", 2, 1) is None
