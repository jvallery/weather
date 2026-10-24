# SPDX-License-Identifier: MIT
"""Focused pure mapping/query/config checks: no sockets or database connection."""

import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from weather.api.cursors import Snapshot
from weather.api.metric_registry import PublicInputError
from weather.api.public_models import ObservationQuery
from weather.db import public_connection
from weather.db.public_connection import (
    PublicDatabaseBudget,
    PublicDatabaseConfigurationError,
    create_public_database_engine,
)
from weather.db.public_mapping import (
    FreshnessPolicy,
    PublicMappingError,
    current_dto,
    history_point,
    public_number,
)
from weather.db.public_source import (
    OBSERVATIONS_SQL,
    PublicReadUnavailable,
    PublicSqlSource,
    decode_position,
    encode_position,
    observation_parameters,
)

AT = datetime(2026, 10, 23, 12, tzinfo=UTC)


def row(metric: str = "temperature", **changes: Any) -> dict[str, Any]:
    result = {
        "station_alias": "public-station",
        "metric": metric,
        "unit": "degC",
        "id": UUID(int=7),
        "message_type": "obs_st",
        "observed_at": AT,
        "received_at": AT,
        "value_text": "0",
        "value_kind": "number",
        "field_quality": {
            "valid": True,
            "eligible": True,
            "state": "valid",
            "reason": "valid",
            "support": {"kind": "instantaneous", "qualified": False},
        },
        "quality": {"valid": True, "reason": "valid"},
    }
    result.update(changes)
    return result


def query(**changes: Any) -> ObservationQuery:
    return ObservationQuery.model_validate(
        {
            "start": AT - timedelta(days=1),
            "end": AT + timedelta(days=1),
            "metrics": ["temperature", "humidity"],
            "limit": 1,
            **changes,
        }
    )


def test_exact_integer_lightning_and_zero_never_pass_through_float() -> None:
    assert public_number("9007199254740993.0", "number", "lightning_count") == 9007199254740993
    assert type(public_number("0", "number", "lightning_count")) is int
    assert public_number("0.5", "number", "lightning_count") is None
    assert public_number("-1", "number", "lightning_count") is None
    assert public_number("true", "boolean", "temperature") is None
    assert public_number("NaN", "number", "temperature") is None
    assert public_number("0", "number", "temperature") == 0


def test_explicit_freshness_policy_preserves_original_times_and_never_uses_report_interval() -> (
    None
):
    original = row(report_interval=99999)
    policies = {"temperature": FreshnessPolicy(60, 90)}
    fresh = current_dto([original], "public-station", policies, AT + timedelta(seconds=20))
    temp = next(m for m in fresh.metrics if m.metric == "temperature")
    assert temp.value == 0 and temp.freshness == "fresh"
    assert temp.expected_interval_seconds == 60 and temp.valid_until == AT + timedelta(seconds=90)
    stale = current_dto([original], "public-station", policies, AT + timedelta(seconds=100))
    expired = next(m for m in stale.metrics if m.metric == "temperature")
    assert expired.value == 0 and expired.freshness == "stale"
    assert expired.observed_at == temp.observed_at == AT
    assert expired.received_at == temp.received_at == AT
    unknown = current_dto([original], "public-station", {}, AT + timedelta(seconds=20))
    unconfigured = next(m for m in unknown.metrics if m.metric == "temperature")
    assert unconfigured.freshness == "stale" and unconfigured.valid_until is None
    assert unconfigured.expected_interval_seconds is None
    assert next(m for m in unknown.metrics if m.metric == "rain_today").value is None


def test_invalid_and_future_values_are_healthy_nulls_with_safe_reasons() -> None:
    disputed = row(
        value_text=None,
        value_kind="null",
        field_quality={
            "valid": False,
            "eligible": False,
            "state": "invalid",
            "reason": "payload_conflict",
        },
    )
    dto = current_dto([disputed], "public-station", {}, AT)
    temp = next(m for m in dto.metrics if m.metric == "temperature")
    assert dto.data_state == "unavailable" and temp.value is None
    assert temp.observed_at == AT and "disagree" in temp.reason
    future = current_dto([row(observed_at=AT + timedelta(seconds=1))], "public-station", {}, AT)
    assert next(m for m in future.metrics if m.metric == "temperature").value is None
    untrusted = row(field_quality={"reason": "private arbitrary source text"})
    assert (
        "private arbitrary"
        not in current_dto([untrusted], "public-station", {}, AT).model_dump_json()
    )
    with pytest.raises(PublicMappingError):
        current_dto([row(unit="arbitrary unit")], "public-station", {}, AT)
    with pytest.raises(PublicMappingError):
        current_dto([row(station_alias="different-station")], "public-station", {}, AT)
    with pytest.raises(PublicMappingError):
        FreshnessPolicy(60, None)


def test_support_requires_explicit_qualification_and_evidence_survives_invalidation() -> None:
    original = row()
    point = history_point(original, AT)
    assert point.value == 0 and point.support_interval is None
    supported = row(
        field_quality={
            "valid": True,
            "eligible": True,
            "state": "valid",
            "reason": "valid",
            "support": {
                "kind": "reported_interval",
                "qualified": True,
                "reported_seconds": 60,
            },
        }
    )
    measured = history_point(supported, AT)
    assert measured.support_interval.start == AT - timedelta(seconds=60)
    invalid = history_point(
        row(
            value_text=None,
            value_kind="null",
            field_quality={
                "valid": False,
                "eligible": False,
                "state": "invalid",
                "reason": "payload_conflict",
            },
        ),
        AT,
    )
    assert invalid.value is None and invalid.evidence_id == point.evidence_id


def test_flattened_keyset_includes_metric_and_roundtrips_canonical_utc_and_uuid() -> None:
    q = query()
    temp, humidity = row(), row(metric="humidity")
    encoded = encode_position(temp)
    assert encoded != encode_position(humidity)
    assert decode_position(encoded, q) == (AT, "obs_st", UUID(int=7), "temperature")
    for invalid in (encoded + "=", "p1_bad", "private arbitrary source text", "x" * 513):
        with pytest.raises(PublicInputError) as failure:
            decode_position(invalid, q)
        assert failure.value.code == "invalid_cursor"
        assert invalid not in str(failure.value)


def test_queries_bind_every_filter_and_snapshot_as_typed_values() -> None:
    q = query()
    alias = "public'; SELECT secret; --"
    parameters = observation_parameters(q, None, alias)
    assert alias not in str(OBSERVATIONS_SQL) and parameters["station"] == alias
    assert parameters["watermark"] is None and parameters["fetch_limit"] == 2
    bindings = json.loads(parameters["bindings"])
    assert {item["field"] for item in bindings} == {"air_temperature", "relative_humidity"}
    snapshot = Snapshot(watermark="9007199254740993", position=encode_position(row()))
    continuation = observation_parameters(q, snapshot, alias)
    assert continuation["watermark"] == 9007199254740993
    assert continuation["after_id"] == UUID(int=7) and continuation["after_metric"] == "temperature"
    with pytest.raises(PublicInputError) as failure:
        observation_parameters(query(metrics=["rain_today"]), None, alias)
    assert failure.value.code == "unsupported_statistic"


def test_lazy_api_factory_requires_verified_tls_and_a_bounded_read_only_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "username").write_text("weather_api")
    (tmp_path / "password").write_text("fixture-only-not-a-real-credential")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    captured: dict[str, Any] = {}

    def tls(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        captured["tls"] = args, kwargs
        return context

    def factory(url: Any, **kwargs: Any) -> Any:
        captured["url"], captured["options"] = url, kwargs
        return object()

    monkeypatch.setattr(public_connection.ssl, "create_default_context", tls)
    monkeypatch.setattr(public_connection, "create_async_engine", factory)
    create_public_database_engine(tmp_path)
    options = captured["options"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert captured["tls"][1]["cafile"] == str(tmp_path / "ca.crt")
    assert captured["url"].host == "weather-pg-rw" and captured["url"].username == "weather_api"
    assert "fixture-only" not in str(captured["url"])
    assert options["hide_parameters"] and not options["echo"]
    assert options["pool_size"] == 4 and options["max_overflow"] == 0
    assert options["connect_args"]["server_settings"]["default_transaction_read_only"] == "on"
    (tmp_path / "username").write_text("weather_migrator")
    with pytest.raises(PublicDatabaseConfigurationError):
        create_public_database_engine(tmp_path)
    (tmp_path / "username").write_text("weather_api")

    def bad_ca(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        raise ssl.SSLError("private certificate details")

    monkeypatch.setattr(public_connection.ssl, "create_default_context", bad_ca)
    with pytest.raises(PublicDatabaseConfigurationError) as failed:
        create_public_database_engine(tmp_path)
    assert "private" not in str(failed.value)
    for values in ({"pool_size": 5}, {"statement_timeout_ms": 2001}, {"pool_size": True}):
        with pytest.raises(PublicDatabaseConfigurationError):
            PublicDatabaseBudget(**values)


def test_empty_source_page_retains_atomic_watermark_and_source_failures_are_sanitized() -> None:
    async def check() -> None:
        source = PublicSqlSource(
            object(), station_alias="public-station", maximum_raw_seconds=200000, clock=lambda: AT
        )

        async def rows(*args: Any) -> list[dict[str, Any]]:
            return [{"watermark": 12, "id": None}]

        source._rows = rows
        result = await source.observations(query(), None)
        assert result.watermark == "12" and result.next_position is None
        assert result.data.points == () and result.data.next_cursor is None

        async def failed(*args: Any) -> list[dict[str, Any]]:
            raise RuntimeError("private driver text")

        source._rows = failed
        with pytest.raises(PublicReadUnavailable) as failure:
            await source.current()
        assert "private" not in str(failure.value)

    asyncio.run(check())
