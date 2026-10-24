# SPDX-License-Identifier: MIT
"""Bounded ASGI/fake-source checks; no socket, database or external service execution."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from weather.api.cursors import CursorCodec, Snapshot
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import AggregateQuery, Aggregates, Current, History, ObservationQuery
from weather.api.public_routes import matches_etag, weak_etag
from weather.api.public_schema import contract_app
from weather.api.public_service import CurrentReading, PublicWeatherService, SourcePage

FIXTURES = Path(__file__).parent / "fixtures/public_contracts"
# Deliberately synthetic per-test signing material, never a deployable key/default.
KEY = bytes(range(32))


class Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 11, 1, 8, 15, 5, tzinfo=UTC)
        self.steady = 0.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.steady

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.steady += seconds


class FakeSource:
    def __init__(self) -> None:
        self.reading = Current.model_validate_json((FIXTURES / "current.json").read_text())
        self.history = History.model_validate_json((FIXTURES / "history.json").read_text())
        self.history = History.model_validate(
            {**self.history.model_dump(), "resolution_seconds": 0}
        )
        self.calls = 0
        self.fail = False
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.snapshot_calls: list[Snapshot | None] = []
        self.page_watermark = "12"
        self.page_position: str | None = "synthetic_key_1"
        self.obs_st = True

    async def current(self) -> CurrentReading:
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise OSError("SYNTHETIC-PRIVATE-SQL-CONFIG")
        return CurrentReading(
            self.reading, self.reading.metrics[0].observed_at if self.obs_st else None
        )

    async def observations(
        self, query: ObservationQuery, snapshot: Snapshot | None
    ) -> SourcePage[History]:
        assert query.cursor is None
        self.snapshot_calls.append(snapshot)
        if self.fail:
            raise OSError("SYNTHETIC-PRIVATE-SQL-CONFIG")
        return SourcePage(self.history, self.page_watermark, self.page_position)

    async def aggregates(
        self, query: AggregateQuery, snapshot: Snapshot | None
    ) -> SourcePage[Aggregates]:
        assert query.cursor is None
        self.snapshot_calls.append(snapshot)
        return SourcePage(
            Aggregates(
                revision="synthetic_snapshot",
                generated_at=query.end,
                interval=query,
                resolution_seconds=query.resolution_seconds,
                statistic=query.statistic,
                items=(),
                next_cursor=None,
            ),
            self.page_watermark,
            None,
        )


def service(source: FakeSource, clock: Clock, **kwargs: object) -> PublicWeatherService:
    return PublicWeatherService(
        source,
        CursorCodec(KEY),
        maximum_raw_seconds=31 * 86400,
        clock=clock.now,
        monotonic=clock.monotonic,
        **kwargs,
    )  # type: ignore[arg-type]


def query(**updates: object) -> ObservationQuery:
    return ObservationQuery.model_validate(
        {
            "start": "2026-11-01T06:00:00Z",
            "end": "2026-11-01T10:00:00Z",
            "metrics": ["temperature"],
            **updates,
        }
    )


def test_five_second_cache_ages_from_source_even_without_new_reads() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        api = service(source, clock)
        initial = await api.current()
        clock.advance(4.999)
        aged = await api.current()
        assert source.calls == 1 and aged.metrics[0].observed_at == initial.metrics[0].observed_at
        assert aged.generated_at == clock.now()
        before = generate_latest(api.metrics.registry).decode()
        assert 'weather_metric_source_age_seconds{metric="temperature"} 9.999' in before
        clock.advance(0.001)
        await api.current()
        assert source.calls == 2
        clock.advance(20)
        # Scrape does not need another request, and never reports a refresh-based age of zero.
        assert (
            "weather_current_conditions_age_seconds 30.0"
            in generate_latest(api.metrics.registry).decode()
        )

    asyncio.run(run())


def test_singleflight_and_disconnected_caller_do_not_duplicate_or_cancel_source_read() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        source.entered, source.release = asyncio.Event(), asyncio.Event()
        api = service(source, clock)
        first = asyncio.create_task(api.current())
        await source.entered.wait()
        followers = [asyncio.create_task(api.current()) for _ in range(10)]
        await asyncio.sleep(0)
        first.cancel()
        with suppress(asyncio.CancelledError):
            await first
        source.release.set()
        results = await asyncio.gather(*followers)
        assert source.calls == 1 and len(results) == 10
        assert api.metrics.cache.labels("coalesced")._value.get() == 10
        await api.aclose()

    asyncio.run(run())


def test_source_failure_retains_times_and_values_then_goes_stale_without_false_zero() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        api = service(source, clock)
        initial = await api.current()
        source.fail = True
        clock.advance(5)
        cached = await api.current()
        assert cached.data_state == "cached" and cached.metrics[0].value == 0
        assert cached.metrics[0].freshness == "fresh"
        assert cached.metrics[0].observed_at == initial.metrics[0].observed_at
        clock.advance(100)
        stale = await api.current()
        assert stale.metrics[0].freshness == "stale" and stale.metrics[-1].value is None
        assert stale.metrics[0].observed_at == initial.metrics[0].observed_at
        assert stale.metrics[0].received_at == initial.metrics[0].received_at
        assert api.metrics.source_errors.labels("current")._value.get() == 2

    asyncio.run(run())


def test_no_cache_failure_is_503_throttled_and_never_returns_304() -> None:
    source, clock = FakeSource(), Clock()
    source.fail = True
    api = service(source, clock)
    with TestClient(contract_app(api)) as client:
        for _ in range(2):
            result = client.get("/api/v1/weather/current", headers={"If-None-Match": "*"})
            assert result.status_code == 503 and result.headers["cache-control"] == "no-store"
            assert "SYNTHETIC-PRIVATE" not in result.text
        assert source.calls == 1


def test_semantic_etag_weak_matching_generated_time_exclusion_and_expiry_binding() -> None:
    source, clock = FakeSource(), Clock()
    api = service(source, clock)
    with TestClient(contract_app(api)) as client:
        first = client.get("/api/v1/weather/current")
        tag = first.headers["etag"]
        assert tag.startswith('W/"') and "max-age=5" in first.headers["cache-control"]
        clock.advance(1)
        unchanged = client.get("/api/v1/weather/current", headers={"If-None-Match": tag[2:]})
        assert unchanged.status_code == 304 and unchanged.content == b""
        assert "content-length" not in unchanged.headers
        clock.advance(100)
        expired = client.get("/api/v1/weather/current", headers={"If-None-Match": tag})
        assert expired.status_code == 200 and expired.json()["metrics"][0]["freshness"] == "stale"
        assert expired.headers["etag"] != tag
        assert matches_etag('"other", ' + tag, tag)
        assert not matches_etag('"mismatch"', tag)
        assert weak_etag(source.reading) == tag
        assert (
            client.get("/api/v1/weather/observations", params={"metrics": "private"}).headers[
                "cache-control"
            ]
            == "no-store"
        )


def test_missing_obs_st_metric_is_absent_and_future_age_is_not_clamped() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        source.obs_st = False
        api = service(source, clock)
        await api.current()
        assert (
            "weather_current_conditions_age_seconds"
            not in generate_latest(api.metrics.registry).decode()
        )
        clock.wall -= timedelta(seconds=10)
        assert (
            'weather_metric_source_age_seconds{metric="temperature"} -5.0'
            in generate_latest(api.metrics.registry).decode()
        )

    asyncio.run(run())


def test_cursor_mac_filters_watermark_position_expiry_and_fixed_lifetime() -> None:
    clock = Clock()
    codec = CursorCodec(KEY, ttl_seconds=30)
    token = codec.encode(
        query(),
        Snapshot(watermark="9223372036854775807", position="synthetic_key"),
        now=clock.now(),
    )
    state = codec.decode(token, query(), now=clock.now())
    assert state.watermark == "9223372036854775807" and state.position == "synthetic_key"
    clock.advance(20)
    next_token = codec.encode(
        query(),
        Snapshot(watermark=state.watermark, position="next_key"),
        now=clock.now(),
        previous=state,
    )
    assert codec.decode(next_token, query(), now=clock.now()).expires_at == state.expires_at
    for bad in (token[:-1] + ("A" if token[-1] != "A" else "B"), "a" * 2049, "invalid.token"):
        with pytest.raises(PublicInputError, match="Invalid, expired"):
            codec.decode(bad, query(), now=clock.now())
    with pytest.raises(PublicInputError):
        codec.decode(token, query(metrics=["humidity"]), now=clock.now())
    with pytest.raises(PublicInputError):
        codec.decode(token, query(limit=1), now=clock.now())
    clock.advance(10)
    with pytest.raises(PublicInputError):
        codec.decode(token, query(), now=clock.now())
    with pytest.raises(ValueError):
        CursorCodec(b"too-short")
    with pytest.raises(ValueError):
        Snapshot(watermark="01", position="key")


def test_cursor_canonical_metric_order_and_cross_endpoint_replay_rejection() -> None:
    clock = Clock()
    codec = CursorCodec(KEY)
    request = query(metrics=["temperature", "humidity"])
    token = codec.encode(request, Snapshot(watermark="12", position="key"), now=clock.now())
    assert codec.decode(token, query(metrics=["humidity", "temperature"]), now=clock.now())
    aggregate = AggregateQuery.model_validate(
        {**request.model_dump(), "statistic": "mean", "resolution_seconds": 60}
    )
    with pytest.raises(PublicInputError):
        codec.decode(token, aggregate, now=clock.now())


def test_publication_page_boundary_and_private_source_failure_are_enforced() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        api = service(source, clock)
        first = await api.observations(query())
        assert first.next_cursor and first.resolution_seconds == 0
        continuation = query(cursor=first.next_cursor)
        source.page_position = None
        second = await api.observations(continuation)
        assert second.next_cursor is None
        assert source.snapshot_calls[-1] == Snapshot(watermark="12", position="synthetic_key_1")
        source.page_watermark = "13"
        with pytest.raises(PublicInputError, match="temporarily unavailable"):
            await api.observations(continuation)
        source.fail = True
        with pytest.raises(PublicInputError) as error:
            await api.observations(query())
        assert "SYNTHETIC-PRIVATE" not in str(error.value)

    asyncio.run(run())


def test_unconfigured_routes_and_query_limits_have_no_fake_source_or_key() -> None:
    with TestClient(contract_app()) as client:
        assert client.get("/api/v1/weather/current").status_code == 503
        assert client.get("/api/v1/weather/metrics").status_code == 200
        params = {
            "start": "2026-11-01T06:00:00Z",
            "end": "2026-11-01T10:00:00Z",
            "metrics": "temperature",
        }
        assert (
            client.get("/api/v1/weather/observations", params={**params, "limit": 5001}).status_code
            == 400
        )
        assert (
            client.get(
                "/api/v1/weather/aggregates",
                params={**params, "resolution_seconds": 60, "statistic": "unknown"},
            ).status_code
            == 400
        )


def test_source_timeout_is_bounded_and_does_not_expose_source_configuration() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        source.release = asyncio.Event()
        api = service(source, clock, source_timeout_seconds=0.01)
        with pytest.raises(PublicInputError, match="temporarily unavailable"):
            await api.current()
        assert source.calls == 1 and api.metrics.source_errors.labels("current")._value.get() == 1
        assert (
            "weather_current_conditions_age_seconds"
            not in generate_latest(api.metrics.registry).decode()
        )
        await api.aclose()

    asyncio.run(run())


def test_http_cursor_and_raw_window_rejections_precede_any_source_page_read() -> None:
    source, clock = FakeSource(), Clock()
    api = service(source, clock)
    with TestClient(contract_app(api)) as client:
        params = {
            "start": "2026-11-01T06:00:00Z",
            "end": "2026-11-01T10:00:00Z",
            "metrics": "temperature",
        }
        bad = client.get("/api/v1/weather/observations", params={**params, "cursor": "bad.token"})
        assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"
        over = client.get(
            "/api/v1/weather/observations", params={**params, "end": "2027-01-01T00:00:00Z"}
        )
        assert over.status_code == 400 and over.json()["suggested_resolution_seconds"] > 0
        assert over.headers["cache-control"] == "no-store"
        assert source.snapshot_calls == []
        first = client.get("/api/v1/weather/observations", params=params)
        assert first.status_code == 200 and first.json()["resolution_seconds"] == 0
        assert (
            first.json()["aggregation"] == "observed" and first.json()["points"][1]["value"] is None
        )
        source.page_position = None
        next_page = client.get(
            "/api/v1/weather/observations", params={**params, "cursor": first.json()["next_cursor"]}
        )
        assert next_page.status_code == 200
        assert source.snapshot_calls[-1] == Snapshot(watermark="12", position="synthetic_key_1")


def test_aggregate_source_receives_explicit_resolution_and_statistic_without_switch() -> None:
    source, clock = FakeSource(), Clock()
    api = service(source, clock)
    with TestClient(contract_app(api)) as client:
        params = {
            "start": "2026-11-01T06:00:00Z",
            "end": "2026-11-01T10:00:00Z",
            "metrics": "temperature",
            "statistic": "mean",
            "resolution_seconds": 3600,
        }
        response = client.get("/api/v1/weather/aggregates", params=params)
        assert response.status_code == 200
        assert (
            response.json()["statistic"] == "mean" and response.json()["resolution_seconds"] == 3600
        )
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(
                "/api/v1/weather/aggregates", params={**params, "statistic": "observed"}
            ).status_code
            == 400
        )


def test_source_contract_mismatch_and_nonprogress_fail_closed() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        api = service(source, clock)
        with pytest.raises(PublicInputError, match="temporarily unavailable"):
            await api.observations(query(limit=1))
        first = await api.observations(query())
        with pytest.raises(PublicInputError, match="temporarily unavailable"):
            await api.observations(query(cursor=first.next_cursor))
        assert api.metrics.source_errors.labels("observations")._value.get() == 2

    asyncio.run(run())


def test_cursor_policy_revision_invalidates_old_semantics_without_exposing_key() -> None:
    clock = Clock()
    codec = CursorCodec(KEY, policy_revision="synthetic_policy_v1")
    token = codec.encode(query(), Snapshot(watermark="12", position="key"), now=clock.now())
    changed = CursorCodec(KEY, policy_revision="synthetic_policy_v2")
    with pytest.raises(PublicInputError):
        changed.decode(token, query(), now=clock.now())
    assert repr(KEY) not in repr(codec)


def test_healthy_source_invalidation_replaces_old_numbers_instead_of_replaying_cache() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        api = service(source, clock)
        assert (await api.current()).metrics[0].value == 0
        payload = source.reading.model_dump()
        payload["data_state"] = "unavailable"
        payload["metrics"] = [
            {
                **item.model_dump(),
                "value": None,
                "quality": "missing",
                "freshness": "unavailable",
                "reason": "Source invalidated.",
            }
            for item in source.reading.metrics
        ]
        source.reading = Current.model_validate(payload)
        source.obs_st = False
        clock.advance(5)
        unavailable_data = await api.current()
        assert unavailable_data.data_state == "unavailable"
        assert all(item.value is None for item in unavailable_data.metrics)
        assert api.metrics.source_errors.labels("current")._value.get() == 0
        assert (
            "weather_current_conditions_age_seconds"
            not in generate_latest(api.metrics.registry).decode()
        )

    asyncio.run(run())


def test_derived_raw_selection_and_explicit_source_input_errors_stay_400() -> None:
    class RejectedSource(FakeSource):
        async def observations(
            self, query: ObservationQuery, snapshot: Snapshot | None
        ) -> SourcePage[History]:
            self.snapshot_calls.append(snapshot)
            raise PublicInputError("invalid_cursor", "Source continuation format is unsupported.")

    source, clock = RejectedSource(), Clock()
    api = service(source, clock)
    with TestClient(contract_app(api)) as client:
        params = {
            "start": "2026-11-01T06:00:00Z",
            "end": "2026-11-01T10:00:00Z",
            "metrics": "rain_today",
        }
        derived = client.get("/api/v1/weather/observations", params=params)
        assert derived.status_code == 400 and derived.json()["code"] == "unsupported_statistic"
        assert source.snapshot_calls == []
        rejected = client.get(
            "/api/v1/weather/observations", params={**params, "metrics": "temperature"}
        )
        assert rejected.status_code == 400 and rejected.json()["code"] == "invalid_cursor"
        assert api.metrics.source_errors.labels("observations")._value.get() == 0


def test_api_role_disables_raw_query_access_logging_without_starting_a_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import prometheus_client
    import uvicorn
    from weather import cli
    from weather.config import Settings

    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(sys, "argv", ["weather", "api"])
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings(public_origin="https://weather.vallery.net")
    )  # type: ignore[arg-type]
    monkeypatch.setattr(prometheus_client, "start_http_server", lambda *args: None)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    cli.main()
    assert captured["access_log"] is False


class StepSleep:
    def __init__(self) -> None:
        self.delays: asyncio.Queue[float] = asyncio.Queue()
        self.steps: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, delay: float) -> None:
        self.delays.put_nowait(delay)
        await self.steps.get()


def test_background_updates_obs_st_without_http_and_ages_original_time_on_db_failure() -> None:
    async def run() -> None:
        source, clock, sleeper = FakeSource(), Clock(), StepSleep()
        api = service(source, clock, sleep=sleeper)
        assert source.calls == 0  # Construction and metadata generation do not start I/O.
        contract_app(api).openapi()
        assert source.calls == 0
        await api.start()
        assert await sleeper.delays.get() == 5
        assert source.calls == 1
        payload = source.reading.model_dump()
        payload["generated_at"] = source.reading.generated_at + timedelta(seconds=5)
        payload["metrics"] = [
            {
                **item.model_dump(),
                **{
                    key: getattr(item, key) + timedelta(seconds=5)
                    for key in ("observed_at", "received_at", "valid_until")
                    if getattr(item, key) is not None
                },
            }
            for item in source.reading.metrics
        ]
        source.reading = Current.model_validate(payload)
        clock.advance(5)
        sleeper.steps.put_nowait(None)
        assert await sleeper.delays.get() == 5
        assert source.calls == 2
        assert (
            "weather_current_conditions_age_seconds 5.0"
            in generate_latest(api.metrics.registry).decode()
        )
        source.fail = True
        clock.advance(5)
        sleeper.steps.put_nowait(None)
        assert await sleeper.delays.get() == 5
        assert source.calls == 3
        assert (
            "weather_current_conditions_age_seconds 10.0"
            in generate_latest(api.metrics.registry).decode()
        )
        await api.aclose(timeout_seconds=0.1)
        assert api._background is not None and api._background.done()
        with pytest.raises(PublicInputError):
            await api.current()

    asyncio.run(run())


def test_empty_unavailable_placeholder_never_becomes_cached_measurements_on_failure() -> None:
    async def run() -> None:
        source, clock = FakeSource(), Clock()
        source.reading = Current(
            generated_at=clock.now(),
            station_alias="Synthetic station",
            data_state="unavailable",
            metrics=(),
        )
        source.obs_st = False
        api = service(source, clock)
        with pytest.raises(PublicInputError):
            await api.current()
        source.fail = True
        clock.advance(5)
        with pytest.raises(PublicInputError):
            await api.current()
        assert source.calls == 2 and api._cached is not None and not api._cached.metrics

    asyncio.run(run())


def test_shutdown_reports_deadline_even_if_source_delays_cancellation() -> None:
    class SlowCancel(FakeSource):
        async def current(self) -> CurrentReading:
            assert self.entered is not None and self.release is not None
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            return CurrentReading(self.reading)

    async def run() -> None:
        source, clock = SlowCancel(), Clock()
        source.entered, source.release = asyncio.Event(), asyncio.Event()
        api = service(source, clock)
        await api.start()
        await source.entered.wait()
        with pytest.raises(TimeoutError, match="shutdown exceeded"):
            await api.aclose(timeout_seconds=0.01)
        source.release.set()
        assert api._flight is not None
        await api._flight
        assert api._cached is None  # A late result cannot publish after close.

    asyncio.run(run())
