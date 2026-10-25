# SPDX-License-Identifier: MIT
"""The private editor (#44) without a database: identity, CSRF, input bounds, receipts paging,
the public listener's 404, the search projection and the routing manifests."""

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from pydantic import ValidationError
from weather.api import build_app
from weather.api.registry import PublicError
from weather.editorial import receipts
from weather.editorial.api import EditorialService, build_operator_app
from weather.editorial.models import (
    Changes,
    Receipt,
    ReceiptQuery,
    Receipts,
    Subject,
    SubjectPatch,
)
from weather.editorial.projection import video_text
from weather.search.documents import documents

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://weather.vallery.net"
SUBJECT = UUID("6f1c9d3e-0b9a-4f36-9a51-2a8c1d0e7b42")
IDENTITY = {"X-authentik-username": "jason", "X-authentik-meta-app": "weather-editor"}
WRITE = {**IDENTITY, "Origin": ORIGIN, "Content-Type": "application/json"}
KEY = b"k" * 32
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def subject(revision: int = 1) -> Subject:
    return Subject(
        subject_id=SUBJECT,
        kind="video",
        revision=revision,
        updated_at=T0,
        video=None,
        description=None,
        event=None,
        tags=[],
        favorite=False,
        note=None,
        audit=[],
    )


class FakeService(EditorialService):
    def __init__(self, allow: set[str] | None = None) -> None:
        self.allow = {"jason"} if allow is None else allow
        self.patches: list[tuple[UUID, SubjectPatch, str]] = []
        self.allowed_calls = 0

    async def allowed(self, username: str, application: str) -> bool:
        self.allowed_calls += 1
        return username in self.allow and application == "weather-editor"

    async def subject(self, subject_id: UUID) -> Subject:
        return subject()

    async def patch(self, subject_id: UUID, body: SubjectPatch, actor: str) -> Subject:
        self.patches.append((subject_id, body, actor))
        return subject(body.expected_revision + 1)

    async def receipts(self, query: ReceiptQuery) -> Receipts:
        receipts.check_window(query)
        return Receipts(generated_at=T0, items=[], next_cursor=None)


def operator(service: FakeService | None = None) -> tuple[TestClient, FakeService]:
    service = service or FakeService()
    app = build_operator_app(service, origin=ORIGIN, registry=CollectorRegistry())
    return TestClient(app), service


def body(**changes: Any) -> str:
    return json.dumps({"expected_revision": 1, "reason": "Checked the footage", "changes": changes})


def patch(api: TestClient, payload: str, headers: dict[str, str] = WRITE) -> Any:
    return api.patch(f"/api/v1/operator/subjects/{SUBJECT}", content=payload, headers=headers)


# Identity --------------------------------------------------------------------------------------


def test_only_the_allowlisted_identity_from_the_editor_application_is_admitted() -> None:
    api, service = operator()
    url = f"/api/v1/operator/subjects/{SUBJECT}"
    ok = api.get(url, headers=IDENTITY)
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store, private"
    assert api.get(url).status_code == 401
    assert api.get(url, headers={**IDENTITY, "X-authentik-username": ""}).status_code == 401
    other = api.get(url, headers={**IDENTITY, "X-authentik-username": "mallory"})
    assert other.status_code == 403 and other.json()["code"] == "forbidden"
    assert other.headers["cache-control"] == "no-store, private"
    wrong_app = api.get(url, headers={**IDENTITY, "X-authentik-meta-app": "options-tracker-edge"})
    assert wrong_app.status_code == 403
    # Two identities (a forged one beside Authentik's) are never resolved by picking one.
    doubled = api.get(
        url,
        headers=[
            ("X-authentik-username", "jason"),
            ("X-authentik-username", "mallory"),
            ("X-authentik-meta-app", "weather-editor"),
        ],
    )
    assert doubled.status_code == 401
    # The allowlist is consulted on every request: no positive authorization cache.
    calls = service.allowed_calls
    api.get(url, headers=IDENTITY)
    api.get(url, headers=IDENTITY)
    assert service.allowed_calls == calls + 2
    assert api.get("/api/v1/operator/session", headers=IDENTITY).json() == {"username": "jason"}


def test_removing_the_operator_takes_effect_on_the_next_request() -> None:
    api, service = operator()
    url = f"/api/v1/operator/subjects/{SUBJECT}"
    assert api.get(url, headers=IDENTITY).status_code == 200
    service.allow = set()
    assert api.get(url, headers=IDENTITY).status_code == 403


def test_without_the_editor_database_every_operator_route_is_unavailable() -> None:
    app = build_operator_app(None, origin=ORIGIN, registry=CollectorRegistry())
    response = TestClient(app).get(f"/api/v1/operator/subjects/{SUBJECT}", headers=IDENTITY)
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store, private"


def test_the_operator_listener_serves_nothing_outside_the_operator_prefix() -> None:
    api, _ = operator()
    for path in ("/healthz", "/api/v1/timelapses", "/editor", "/openapi.json", "/docs"):
        assert api.get(path, headers=IDENTITY).status_code == 404


def test_unknown_or_non_canonical_subject_ids_are_not_found() -> None:
    api, _ = operator()
    for value in ("x", SUBJECT.hex, str(SUBJECT).upper()):
        assert api.get(f"/api/v1/operator/subjects/{value}", headers=IDENTITY).status_code == 404


# CSRF -------------------------------------------------------------------------------------------


def test_mutations_need_the_exact_origin_a_same_origin_fetch_and_json() -> None:
    api, service = operator()
    assert patch(api, body(favorite=True)).status_code == 200
    for headers in (
        {**IDENTITY, "Content-Type": "application/json"},
        {**WRITE, "Origin": "https://evil.example"},
        {**WRITE, "Origin": ORIGIN + "/"},
        {**WRITE, "Origin": "http://weather.vallery.net"},
        {**WRITE, "Origin": "null"},
        {**WRITE, "Sec-Fetch-Site": "cross-site"},
        {**WRITE, "Sec-Fetch-Site": "same-site"},
    ):
        refused = patch(api, body(favorite=True), headers)
        assert refused.status_code == 403 and refused.json()["code"] == "origin_rejected"
    form = patch(
        api, "favorite=true", {**WRITE, "Content-Type": "application/x-www-form-urlencoded"}
    )
    assert form.status_code == 415
    assert (
        patch(api, body(favorite=True), {**WRITE, "Content-Type": "text/plain"}).status_code == 415
    )
    # A forged identity on a cross-origin request learns nothing either way.
    assert (
        patch(
            api,
            body(favorite=True),
            {**WRITE, "Origin": "https://x.example", "X-authentik-username": "mallory"},
        ).status_code
        == 403
    )
    assert len(service.patches) == 1


def test_a_non_allowlisted_identity_cannot_write_even_with_the_right_origin() -> None:
    api, service = operator(FakeService(allow=set()))
    assert patch(api, body(favorite=True)).status_code == 403
    assert service.patches == []


# Input bounds ---------------------------------------------------------------------------------


def test_unknown_actor_and_measured_fields_are_422() -> None:
    api, service = operator()
    payloads = [
        json.dumps(
            {
                "expected_revision": 1,
                "reason": "r",
                "changes": {"favorite": True},
                "actor": "someone-else",
            }
        ),
        body(favorite=True, author="x"),
        body(event={"visibility": "public", "measurement": {"wind_gust": 40}}),
        body(event={"label": "Gust", "evidence_ids": [str(uuid4())]}),
        body(wind_gust=40),
        body(description="Fine.", evidence_hash="0" * 64),
        json.dumps({"reason": "r", "changes": {"favorite": True}}),  # no expected_revision
        json.dumps({"expected_revision": 1, "changes": {"favorite": True}}),  # no reason
        json.dumps({"expected_revision": 1, "reason": " ", "changes": {"favorite": True}}),
        body(),  # no change at all
        body(tags=None),
        body(favorite=None),
        body(rebase=False),
        body(event=None),
        body(tags=[{"tag": "Snow!", "visibility": "public"}]),
        body(
            tags=[{"tag": "snow", "visibility": "public"}, {"tag": "snow", "visibility": "private"}]
        ),
        "not json",
    ]
    for payload in payloads:
        response = patch(api, payload)
        assert response.status_code == 422, payload
        assert response.json()["code"] == "invalid_change"
    assert service.patches == []


def test_descriptions_are_bounded_never_truncated_and_null_clears() -> None:
    api, service = operator()
    for bad in ("", "   \n", "x" * 8001, "bell\x07", "line separator"):
        assert patch(api, body(description=bad)).status_code == 422
    longest = "y" * 7999 + "."
    assert patch(api, body(description=longest)).status_code == 200
    assert service.patches[-1][1].changes.description == longest
    assert patch(api, body(description=None)).status_code == 200
    cleared = service.patches[-1][1].changes
    assert "description" in cleared.model_fields_set and cleared.description is None
    # The actor is the authenticated identity, never anything in the body.
    assert {actor for _, _, actor in service.patches} == {"jason"}


def test_omitted_fields_are_unchanged() -> None:
    only = Changes.model_validate({"favorite": True})
    assert only.model_fields_set == {"favorite"}
    assert only.model_dump(exclude_unset=True) == {"favorite": True}
    note = Changes.model_validate({"note": None})
    assert note.model_fields_set == {"note"}
    assert SubjectPatch.model_validate(
        {"expected_revision": 0, "reason": "first", "changes": {"tags": []}}
    ).changes.model_fields_set == {"tags"}
    with pytest.raises(ValidationError):
        Changes.model_validate({"note": "n" * 4001})


# Receipts --------------------------------------------------------------------------------------


def receipt(at: datetime, raw: bytes) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "received_at": at,
        "inserted_at": at,
        "raw_bytes": raw,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "parse_state": "parsed",
        "parse_reason": None,
        "parser_revision": "tempest-v1",
    }


class Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, value: Any = None) -> None:
        self.rows, self.value = rows or [], value

    def mappings(self) -> list[dict[str, Any]]:
        return self.rows

    def scalar_one(self) -> Any:
        return self.value


class ReceiptStore:
    """Answers receipts.page's two statements over an in-memory table."""

    def __init__(self, rows: list[dict[str, Any]], now: datetime) -> None:
        self.rows, self.now = rows, now

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Rows:
        if params is None:
            return Rows(value=self.now - timedelta(seconds=120))
        after = (params["after_time"], params["after_id"])
        found = sorted(
            (
                r
                for r in self.rows
                if params["start"] <= r["received_at"] < params["end"]
                and r["inserted_at"] <= params["snapshot"]
                and (r["received_at"], r["id"]) > after
            ),
            key=lambda r: (r["received_at"], r["id"]),
        )
        return Rows(found[: params["scan"]])


def run_page(store: ReceiptStore, query: ReceiptQuery) -> Receipts:
    return asyncio.run(receipts.page(store, KEY, query))  # type: ignore[arg-type]


def test_receipt_pages_neither_repeat_nor_skip_and_bytes_hash_to_the_stored_digest() -> None:
    families = ["obs_st", "rapid_wind", "evt_precip"]
    rows = [
        receipt(T0 + timedelta(seconds=n), json.dumps({"type": families[n % 3], "n": n}).encode())
        for n in range(23)
    ]
    rows.append(receipt(T0 + timedelta(seconds=5), b"\xff not json"))
    store = ReceiptStore(list(rows), T0 + timedelta(days=1))
    query = ReceiptQuery(start=T0, end=T0 + timedelta(hours=1), limit=5)
    seen: list[Receipt] = []
    page = run_page(store, query)
    pages = 1
    while True:
        seen.extend(page.items)
        if page.next_cursor is None:
            break
        # A late spool-drain row behind the cursor never shifts this export.
        store.rows.append({**receipt(T0, b'{"type":"obs_st"}'), "inserted_at": store.now})
        page = run_page(store, query.model_copy(update={"cursor": page.next_cursor}))
        pages += 1
    assert pages == 5
    assert [r.id for r in seen] == [
        r["id"] for r in sorted(rows, key=lambda r: (r["received_at"], r["id"]))
    ]
    for item in seen:
        raw = base64.b64decode(item.bytes_base64)
        assert hashlib.sha256(raw).hexdigest() == item.sha256 and len(raw) == item.bytes
    assert {r.family for r in seen} == {"obs_st", "rapid_wind", "evt_precip", "unknown"}
    # A family filter pages over the same keyset.
    rapid = run_page(
        store, ReceiptQuery(start=T0, end=T0 + timedelta(hours=1), family="rapid_wind")
    )
    assert {r.family for r in rapid.items} == {"rapid_wind"} and len(rapid.items) == 8


def test_receipt_windows_cursors_and_digests_are_checked() -> None:
    store = ReceiptStore([receipt(T0, b'{"type":"obs_st"}')], T0 + timedelta(days=1))
    with pytest.raises(PublicError) as wide:
        run_page(store, ReceiptQuery(start=T0, end=T0 + timedelta(hours=24, seconds=1)))
    assert wide.value.code == "range_too_large"
    with pytest.raises(PublicError):
        run_page(store, ReceiptQuery(start=T0, end=T0))
    first = ReceiptQuery(start=T0, end=T0 + timedelta(hours=1), limit=1)
    store.rows.append(receipt(T0 + timedelta(seconds=1), b'{"type":"obs_st"}'))
    token = run_page(store, first).next_cursor
    assert token is not None
    # A cursor is bound to its filters and cannot be forged.
    for altered in (
        first.model_copy(update={"cursor": token, "limit": 2}),
        first.model_copy(update={"cursor": token, "family": "obs_st"}),
        first.model_copy(update={"cursor": token[:-2] + "xx"}),
    ):
        with pytest.raises(PublicError) as bad:
            run_page(store, altered)
        assert bad.value.code == "invalid_cursor"
    store.rows[0]["raw_bytes"] = b'{"type":"rapid_wind"}'
    with pytest.raises(receipts.ReceiptIntegrityError):
        run_page(store, first)


def test_receipts_route_rejects_an_over_wide_window_with_400() -> None:
    api, _ = operator()
    url = "/api/v1/operator/telemetry/receipts"
    ok = api.get(
        url,
        params={"start": "2026-07-01T00:00:00Z", "end": "2026-07-01T06:00:00Z"},
        headers=IDENTITY,
    )
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store, private"
    wide = api.get(
        url,
        params={"start": "2026-07-01T00:00:00Z", "end": "2026-07-03T00:00:00Z"},
        headers=IDENTITY,
    )
    assert wide.status_code == 400 and wide.json()["code"] == "range_too_large"
    assert (
        api.get(url, params={"start": "2026-07-01", "end": "x"}, headers=IDENTITY).status_code
        == 400
    )
    assert (
        api.get(
            url,
            params={"start": "2026-07-01T00:00:00Z", "end": "2026-07-01T06:00:00Z", "limit": 101},
            headers=IDENTITY,
        ).status_code
        == 400
    )
    assert (
        api.get(
            url, params={"start": "2026-07-01T00:00:00Z", "end": "2026-07-01T06:00:00Z"}
        ).status_code
        == 401
    )


# The public listener -------------------------------------------------------------------------


def test_the_public_listener_404s_operator_paths_and_ignores_identity_headers() -> None:
    public = TestClient(build_app())
    for method, path in (
        ("GET", f"/api/v1/operator/subjects/{SUBJECT}"),
        ("PATCH", f"/api/v1/operator/subjects/{SUBJECT}"),
        ("GET", "/api/v1/operator/telemetry/receipts"),
        ("GET", "/api/v1/operator/session"),
        ("POST", "/api/v1/operator/events"),
    ):
        response = public.request(method, path, headers=WRITE, content=body(favorite=True))
        assert response.status_code == 404, path
        assert "jason" not in response.text


# The search projection -------------------------------------------------------------------------


def video_row() -> dict[str, Any]:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    return {
        "video_id": uuid4(),
        "camera_id": "skycam_east",
        "segment": "daytime",
        "starts_at": start,
        "ends_at": start + timedelta(hours=2),
        "public_not_before": start + timedelta(hours=3),
        "video_revision": 2,
        "frames": 600,
    }


def test_an_active_human_description_replaces_automated_text_and_windows_whole() -> None:
    row = video_row()
    observations = [
        {
            "id": "v1",
            "category": "rain",
            "assessment": "visible",
            "confidence": "high",
            "window": 0,
            "intensity": "light",
            "start": row["starts_at"].isoformat(),
        }
    ]
    automated = documents(
        video_text(
            row,
            description_revision=1,
            editorial_revision=0,
            poster=None,
            observations=observations,
            summary="Rain fell.",
            coverage={},
            inputs={},
        )
    )
    assert [d["kind"] for d in automated] == ["video", "window"]
    longest = "Snow " * 1599 + "fell."
    human = documents(
        video_text(
            row,
            description_revision=1,
            editorial_revision=4,
            poster=None,
            observations=observations,
            summary="Rain fell.",
            human=longest,
        )
    )
    assert [d["kind"] for d in human] == ["video"]
    assert "rain" in automated[1]["text"].lower()
    assert longest in human[0]["text"] and "rain" not in human[0]["text"].lower()
    assert human[0]["editorial_revision"] == 4 and human[0]["analysis_mode"] == "human_edited"
    assert len(human[0]["text"]) <= 9000


# Migration and routing manifests ---------------------------------------------------------------


def test_the_editorial_migration_is_expand_only_seeds_one_operator_and_grants_least_privilege() -> (
    None
):
    import importlib.util

    path = ROOT / "app/migrations/versions/0012_editorial.py"
    spec = importlib.util.spec_from_file_location("editorial_0012", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0011_analysis"
    statements = module.statements()
    seeds = [s for s in statements if s.startswith("INSERT INTO weather.editorial_operator")]
    assert len(seeds) == 1 and "'jason', 'weather-editor'" in seeds[0]
    joined = "\n".join(statements).upper()
    for destructive in (
        "DROP TABLE",
        "DROP COLUMN",
        "TRUNCATE",
        "DELETE FROM",
        "ALTER COLUMN TYPE",
    ):
        assert destructive not in joined
    grants = [s for s in statements if s.startswith("GRANT")]
    writes = [
        g for g in grants if g.endswith("weather_editor") and ("INSERT" in g or "UPDATE" in g)
    ]
    # No telemetry or catalog writes: only editorial, event, calendar, queue and search rows.
    for protected in (
        "telemetry_receipt",
        "obs_st",
        "rapid_wind",
        "weather.video",
        "publication",
        "media_asset",
        "analysis",
        "segment",
        "editorial_operator",
    ):
        assert not [g for g in writes if protected in g], protected
    assert not [g for g in grants if "weather_api" in g and "public_api.video_editorial" not in g]
    view = next(s for s in statements if "VIEW public_api.video_editorial" in s)
    for private in ("favorite", "note", "private_tags", "actor", "reason", "sha256", "evidence"):
        assert private not in view.split("FROM")[0]


def manifests(name: str) -> list[dict[str, Any]]:
    return list(yaml.safe_load_all((ROOT / "deploy/k8s" / name).read_text()))


def test_editor_routes_strip_forged_identity_then_require_authentik() -> None:
    routes = {
        r["match"]: r
        for doc in manifests("ingress.yaml") + manifests("editor-ingress.yaml")
        if doc["kind"] == "IngressRoute"
        for r in doc["spec"]["routes"]
    }
    operator_route = next(r for m, r in routes.items() if "/api/v1/operator" in m)
    editor_route = next(r for m, r in routes.items() if "/editor" in m)
    public_api = next(r for m, r in routes.items() if "PathPrefix(`/api/`)" in m)
    for route in (operator_route, editor_route):
        chain = [(m["name"], m.get("namespace")) for m in route["middlewares"]]
        assert chain[:2] == [
            ("apps-strip-auth-headers", "network-infra"),
            ("ak-forwardauth", "network-infra"),
        ]
        assert route["priority"] > public_api["priority"]
    assert operator_route["services"] == [{"name": "api-operator", "port": 8001}]
    assert editor_route["services"] == [{"name": "web", "port": 80}]
    # No other route reaches the operator listener.
    others = [r for r in routes.values() if r is not operator_route]
    assert all(s["name"] != "api-operator" for r in others for s in r["services"])


def test_only_traefik_reaches_the_operator_port_and_the_api_uses_the_editor_role_there() -> None:
    policies = {d["metadata"]["name"]: d["spec"] for d in manifests("network-policy.yaml")}
    rules = [
        r for r in policies["api-ingress"]["ingress"] if any(p["port"] == 8001 for p in r["ports"])
    ]
    assert len(rules) == 1 and rules[0]["from"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik-apps"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}},
        }
    ]
    web_egress = policies["web-egress"]["egress"]
    assert all(p["port"] != 8001 for rule in web_egress for p in rule.get("ports", []))
    documents_ = manifests("application.yaml")
    services = {d["metadata"]["name"]: d for d in documents_ if d["kind"] == "Service"}
    assert [p["port"] for p in services["api"]["spec"]["ports"]] == [80, 9000]
    assert [p["port"] for p in services["api-operator"]["spec"]["ports"]] == [8001]
    api = next(
        d for d in documents_ if d["kind"] == "Deployment" and d["metadata"]["name"] == "api"
    )
    volumes = {v["name"]: v for v in api["spec"]["template"]["spec"]["volumes"]}
    assert [s["secret"]["name"] for s in volumes["database-editor"]["projected"]["sources"]] == [
        "weather-pg-editor",
        "weather-pg-ca",
    ]
    assert [s["secret"]["name"] for s in volumes["database"]["projected"]["sources"]][0] == (
        "weather-pg-api"
    )
