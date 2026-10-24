# SPDX-License-Identifier: MIT
"""/api/v1/timelapses without a database: filters, signed cursors, privacy snapshot."""

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from weather.api import build_app
from weather.api.timelapses import (
    Catalog,
    CatalogQuery,
    Timelapse,
    TimelapseCursor,
    TimelapsePage,
    build,
)

KEY = b"k" * 64
T0 = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
FORBIDDEN = re.compile(
    r"weather-originals|weather-media|bucket|object_key|config_hash|sha256|original|[0-9a-f]{64}"
)


def row(day: int, camera: str = "skycam_east", **changes: Any) -> dict[str, Any]:
    starts = T0 + timedelta(days=day)
    value = {
        "id": uuid4(),
        "camera_id": camera,
        "cycle_date": starts.date(),
        "segment": "daytime",
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=11),
        "frames": 4200,
        "revision": 1,
        "committed_at": starts + timedelta(hours=12),
        "withdrawn": False,
        "state": "published",
        "reason": None,
        "keogram_state": "published",
    }
    for role in ("native", "web", "mobile", "poster", "sidecar", "keogram"):
        value[f"{role}_asset_id"] = uuid4()
    value.update(changes)
    return value


def assets_for(rows: list[dict[str, Any]]) -> dict[UUID, dict[str, Any]]:
    classes = {
        "native": "video",
        "web": "video",
        "mobile": "video",
        "poster": "poster",
        "sidecar": "sidecar",
        "keogram": "keogram",
    }
    result = {}
    for item in rows:
        for role, asset_class in classes.items():
            asset_id = item[f"{role}_asset_id"]
            if asset_id is not None:
                result[asset_id] = {
                    "id": asset_id,
                    "class": asset_class,
                    "content_type": "application/json" if role == "sidecar" else "video/mp4",
                    "bytes": 100,
                    "width": None if role == "sidecar" else 1920,
                    "height": None if role == "sidecar" else 870,
                }
    return result


class FakeSource:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = sorted(rows, key=lambda r: (r["starts_at"], r["id"]), reverse=True)
        self.queries: list[CatalogQuery] = []

    async def videos(
        self, query: CatalogQuery, after: tuple[datetime, UUID] | None, video_id: UUID | None
    ) -> list[dict[str, Any]]:
        self.queries.append(query)
        selected = [
            r
            for r in self.rows
            if (video_id is None or r["id"] == video_id)
            and (query.first is None or query.first <= r["cycle_date"] <= (query.last or date.max))
            and (query.camera is None or r["camera_id"] == query.camera)
            and (after is None or (r["starts_at"], r["id"]) < after)
        ]
        return selected[: query.limit + 1]

    async def assets(self, ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        every = assets_for(self.rows)
        return {asset_id: every[asset_id] for asset_id in ids if asset_id in every}


def client(rows: list[dict[str, Any]]) -> tuple[TestClient, FakeSource]:
    source = FakeSource(rows)
    app = build_app()
    app.state.timelapses = Catalog(source, TimelapseCursor(KEY))
    return TestClient(app), source


def test_month_filter_pages_of_at_most_100_with_signed_cursors() -> None:
    rows = [row(day % 31, camera) for day in range(31) for camera in ("skycam_east", "skycam_west")]
    rows += [row(40)]  # November
    rows += [row(day, "frontyard_1") for day in range(31) for _ in range(2)]
    api, source = client(rows)
    first = api.get("/api/v1/timelapses", params={"month": "2026-10"})
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    page = first.json()
    assert len(page["items"]) == 100 and page["next_cursor"]
    assert source.queries[-1].first == date(2026, 10, 1)
    assert source.queries[-1].last == date(2026, 10, 31)
    second = api.get(
        "/api/v1/timelapses", params={"month": "2026-10", "cursor": page["next_cursor"]}
    ).json()
    ids = [item["video_id"] for item in page["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 124 and second["next_cursor"] is None
    assert all(item["cycle_date"].startswith("2026-10-") for item in page["items"])
    # A tampered cursor, or one re-used with other filters, is rejected.
    payload, mac = page["next_cursor"].split(".")
    for cursor, params in (
        (payload + "A." + mac, {"month": "2026-10"}),
        (payload + "." + mac[:-2] + "AA", {"month": "2026-10"}),
        (page["next_cursor"], {"month": "2026-09"}),
        (page["next_cursor"], {"month": "2026-10", "camera": "skycam_east"}),
    ):
        refused = api.get("/api/v1/timelapses", params={**params, "cursor": cursor})
        assert refused.status_code == 400 and refused.json()["code"] == "invalid_cursor"


def test_filter_validation_and_ranges() -> None:
    api, source = client([row(0)])
    ok = api.get("/api/v1/timelapses", params={"from": "2026-10-01", "to": "2026-10-07"})
    assert ok.status_code == 200 and source.queries[-1].last == date(2026, 10, 7)
    for params in (
        {"month": "2026-13"},
        {"month": "2026-10", "cycle_date": "2026-10-01"},
        {"from": "2026-10-01"},
        {"from": "2026-10-02", "to": "2026-10-01"},
        {"limit": "101"},
        {"camera": "garage"},
        {"bucket": "weather-originals"},
    ):
        response = api.get("/api/v1/timelapses", params=params)
        assert response.status_code == 400, params
        assert response.headers["content-type"] == "application/problem+json"
        assert set(response.json()) == {"code", "message", "suggested_resolution_seconds"}
    too_long = api.get("/api/v1/timelapses", params={"from": "2024-01-01", "to": "2025-01-01"})
    assert too_long.json()["code"] == "range_too_large"


def test_states_reasons_and_one_record() -> None:
    published = row(1)
    partial = row(
        2, state="partial", reason="low_coverage", keogram_asset_id=None, keogram_state="pending"
    )
    unavailable = row(
        3,
        state="unavailable",
        reason="camera_offline",
        native_asset_id=None,
        web_asset_id=None,
        mobile_asset_id=None,
        poster_asset_id=None,
    )
    withdrawn = row(
        4,
        state="withdrawn",
        reason="withdrawn",
        withdrawn=True,
        keogram_state="withdrawn",
        **{f"{r}_asset_id": None for r in ("native", "web", "mobile", "poster", "sidecar")},
        keogram_asset_id=None,
    )
    api, _ = client([published, partial, unavailable, withdrawn])
    items = {i["video_id"]: i for i in api.get("/api/v1/timelapses").json()["items"]}
    assert items[str(published["id"])]["native_download_url"] == (
        f"/downloads/{published['id']}/native"
    )
    assert len(items[str(partial["id"])]["renditions"]) == 3
    assert items[str(partial["id"])]["keogram_state"] == "pending"
    assert items[str(unavailable["id"])]["reason"] == "camera_offline"
    gone = items[str(withdrawn["id"])]
    assert gone["withdrawn"] is True and gone["state"] == "withdrawn"
    assert "/media/" not in json.dumps(gone) and gone["native_download_url"] is None
    one = api.get(f"/api/v1/timelapses/{published['id']}")
    assert one.status_code == 200 and one.json()["video_id"] == str(published["id"])
    assert api.get(f"/api/v1/timelapses/{uuid4()}").status_code == 404
    assert api.get("/api/v1/timelapses/not-an-id").status_code == 404


def test_missing_configuration_is_unavailable_not_an_error() -> None:
    app = build_app()
    response = TestClient(app).get("/api/v1/timelapses")
    assert response.status_code == 503 and response.json()["code"] == "unavailable"
    assert TestClient(app).get("/healthz").status_code == 200


def test_public_response_snapshot_has_no_private_storage_identity() -> None:
    item = row(1)
    every = assets_for([item])
    record = build(item, every)
    page = TimelapsePage(generated_at=T0, items=[record], next_cursor=None)
    for model in (record, page):
        text = json.dumps(model.model_dump(mode="json", by_alias=True))
        assert not FORBIDDEN.search(text), text
    for schema in (Timelapse.model_json_schema(by_alias=True), TimelapsePage.model_json_schema()):
        names = json.dumps(schema)
        for word in ("bucket", "object_key", "sha256", "config_hash", "original"):
            assert word not in names
    assert set(record.model_dump(by_alias=True)) == {
        "schema_version",
        "video_id",
        "camera",
        "cycle_date",
        "segment",
        "state",
        "reason",
        "withdrawn",
        "revision",
        "updated_at",
        "source_interval",
        "allocated_frames",
        "renditions",
        "poster",
        "sidecar",
        "native_download_url",
        "keogram_state",
        "keogram",
    }
