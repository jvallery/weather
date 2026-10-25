# SPDX-License-Identifier: MIT
"""/api/v1/keograms without a database: seek targets, embargoed and failed states, filters."""

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from weather.api import build_app
from weather.api.keograms import BINS, seek_targets

DAWN = datetime(2026, 10, 31, 12, 30, tzinfo=UTC)
DUSK = DAWN + timedelta(hours=12, minutes=30)


def row(camera: str = "skycam_east", segment: str = "daytime", **changes: Any) -> dict[str, Any]:
    starts, ends = (DAWN, DUSK) if segment == "daytime" else (DUSK, DUSK + timedelta(hours=12))
    value: dict[str, Any] = {
        "id": uuid4(),
        "camera_id": camera,
        "cycle_date": date(2026, 10, 31),
        "segment": segment,
        "starts_at": starts,
        "ends_at": ends,
        "state": "partial",
        "reason": "gaps",
        "revision": 1,
        "committed_at": ends + timedelta(minutes=20),
        "image_asset_id": uuid4(),
        "thumbnail_asset_id": uuid4(),
        "sidecar_asset_id": uuid4(),
        "observed": 1380,
        "gaps": [[360, 420]],
        "lineage_sha256": "a" * 64,
        "region_version": "fixture-1",
        "video_id": uuid4(),
        "video_state": "published",
        "video_starts_at": starts,
        "video_ends_at": ends,
        "video_frames": 3750,
    }
    value.update(changes)
    return value


class FakeSource:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[date, str | None, str | None]] = []
        self.missing: set[UUID] = set()

    async def keograms(
        self, cycle_date: date, camera: str | None, segment: str | None
    ) -> list[dict[str, Any]]:
        self.calls.append((cycle_date, camera, segment))
        return [
            r
            for r in self.rows
            if r["cycle_date"] == cycle_date
            and camera in (None, r["camera_id"])
            and segment in (None, r["segment"])
        ]

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        result = {}
        for item in self.rows:
            for column, asset_class, mime in (
                ("image_asset_id", "keogram", "image/png"),
                ("thumbnail_asset_id", "keogram", "image/png"),
                ("sidecar_asset_id", "sidecar", "application/json"),
            ):
                if item[column] in ids and item[column] not in self.missing:
                    result[item[column]] = {
                        "id": item[column],
                        "class": asset_class,
                        "content_type": mime,
                        "bytes": 10,
                        "width": None if asset_class == "sidecar" else 1440,
                        "height": None if asset_class == "sidecar" else 512,
                    }
        return result


def client(rows: list[dict[str, Any]]) -> tuple[TestClient, FakeSource]:
    source = FakeSource(rows)
    app = build_app()
    app.state.keograms = source
    return TestClient(app), source


def micros(value: datetime) -> int:
    return round((value - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)


def test_every_seek_target_lies_inside_its_video_and_its_bin() -> None:
    rows = [row(), row(segment="overnight", video_frames=3450)]
    api, _ = client(rows)
    response = api.get("/api/v1/keograms", params={"cycle_date": "2026-10-31"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [item["segment"] for item in items] == ["daytime", "overnight"]
    for item, source in zip(items, rows, strict=True):
        assert item["bins"]["count"] == BINS
        step = Fraction(**item["bins"]["step_seconds"])
        start, end = micros(source["starts_at"]), micros(source["ends_at"])
        assert step * BINS * 1_000_000 == end - start
        seek = item["seek"]
        assert seek["video_id"] == str(source["video_id"])
        assert len(seek["output_seconds"]) == BINS
        frames = source["video_frames"]
        for index, seconds in enumerate(seek["output_seconds"]):
            frame = round(seconds * 60 - 0.5)
            assert 0 <= frame < frames
            # The frame's source interval holds the bin's midpoint, inside the video.
            low = start + Fraction(frame * (end - start), frames)
            high = start + Fraction((frame + 1) * (end - start), frames)
            midpoint = start + Fraction((2 * index + 1) * (end - start), 2 * BINS)
            assert start <= low <= midpoint < high <= end
        assert item["image"]["url"].startswith("/media/") and item["gaps"] == [[360, 420]]
        assert item["lineage_digest"] == "a" * 64


def test_seek_is_null_without_a_playable_video_and_assets_hide_until_shown() -> None:
    hidden = {
        "revision": None,
        "committed_at": None,
        "image_asset_id": None,
        "thumbnail_asset_id": None,
        "sidecar_asset_id": None,
        "observed": None,
        "gaps": None,
        "lineage_sha256": None,
        "region_version": None,
    }
    rows = [
        row("skycam_east", video_state="unavailable"),
        row("skycam_west", video_id=None, video_state=None),
        # Embargoed: the view reports pending and nulls every asset and metadata column.
        row("frontyard_1", state="pending", reason=None, **hidden),
        row("frontyard_1", "overnight", state="failed", reason="publish_failed", **hidden),
    ]
    api, _ = client(rows)
    items = {
        (i["camera"], i["segment"]): i
        for i in api.get("/api/v1/keograms", params={"cycle_date": "2026-10-31"}).json()["items"]
    }
    assert items[("skycam_east", "daytime")]["seek"] is None
    assert items[("skycam_west", "daytime")]["seek"] is None
    pending = items[("frontyard_1", "daytime")]
    assert pending["state"] == "pending" and pending["image"] is None
    assert pending["thumbnail"] is None and pending["lineage_digest"] is None
    assert pending["seek"] is not None  # the video is playable; only the image waits
    failed = items[("frontyard_1", "overnight")]
    assert (failed["state"], failed["reason"]) == ("failed", "publish_failed")
    assert failed["image"] is None and failed["gaps"] == []


def test_a_visible_state_without_its_images_degrades() -> None:
    gone = row()
    api, source = client([gone])
    source.missing.add(gone["thumbnail_asset_id"])  # withdrawn between the two reads
    item = api.get("/api/v1/keograms", params={"cycle_date": "2026-10-31"}).json()["items"][0]
    assert (item["state"], item["reason"]) == ("unavailable", "media_unavailable")
    assert item["image"] is None


def test_filters_are_bounded_and_validated() -> None:
    api, source = client([row()])
    ok = api.get(
        "/api/v1/keograms",
        params={"cycle_date": "2026-10-31", "camera": "skycam_east", "segment": "daytime"},
    )
    assert ok.status_code == 200 and source.calls[-1] == (
        date(2026, 10, 31),
        "skycam_east",
        "daytime",
    )
    for params in (
        {},
        {"cycle_date": "2026-13-01"},
        {"cycle_date": "2026-10-31", "camera": "north"},
        {"cycle_date": "2026-10-31", "segment": "dusk"},
        {"cycle_date": "2026-10-31", "limit": "5"},
    ):
        response = api.get("/api/v1/keograms", params=params)
        assert response.status_code == 400, params
        assert response.json()["code"] == "invalid_request"


def test_unequal_video_interval_maps_through_its_own_time_map() -> None:
    start, end = DAWN, DUSK
    shifted = seek_targets(start, end, start - timedelta(hours=1), end, 3600)
    assert shifted is not None and shifted[0] > 0
    assert seek_targets(start, end, start + timedelta(hours=1), end, 3600) is None
