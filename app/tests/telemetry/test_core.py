# SPDX-License-Identifier: MIT
"""Bounded pure tests only: all identity/address values are explicitly synthetic."""

import json
import sqlite3
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from weather.telemetry.parse import FAMILIES, json_text, parse_packet
from weather.telemetry.receiver import AdmissionConfig, Datagram, Receiver
from weather.telemetry.spool import Receipt, Spool, SpoolError

NOW = Decimal(1700000100)
ST_ROW = [1700000000, 0.1, 0.2, 0.3, 90, 3, 1000, 20, 50, 100, 1, 50, 0, 0, 10, 2, 2.5, 1]


def packet(family: str, **data: object) -> bytes:
    return json.dumps({"serial_number": "SYNTHETIC-ST", "type": family, **data}).encode()


def receipt(spool: Spool, raw: bytes = b"synthetic") -> Receipt:
    return Receipt.create(raw, spool.incarnation, 1700000000000000001, 11, 7, "192.0.2.1", 50222)


def test_all_families_official_positions_units_and_unknown_fields() -> None:
    samples = {
        "obs_st": {"obs": [ST_ROW], "future_field": {"retained": True}},
        "rapid_wind": {"ob": [1700000000, 2.3, 128]},
        "evt_precip": {"evt": [1700000000]},
        "evt_strike": {"evt": [1700000000, 10, 100]},
        "obs_air": {"obs": [[1700000000, 1001, 21, 51, 3, 11, 2.6, 2]]},
        "obs_sky": {"obs": [[1700000000, 100, 1, 2, 0.1, 0.2, 0.3, 90, 2.5, 1, 50, 4, 2, 3]]},
        "device_status": {
            "timestamp": 1700000000,
            "firmware_revision": 171,
            "sensor_status": 0x10 | 0x40000,
        },
        "hub_status": {
            "timestamp": 1700000000,
            "firmware_revision": "171",
            "radio_stats": [1, 2, 3, 7, 4],
        },
    }
    assert set(samples) == FAMILIES
    parsed = {f: parse_packet(packet(f, **data), NOW).rows[0] for f, data in samples.items()}
    st = parsed["obs_st"]
    assert st.values["air_temperature"] == 20 and st.values["relative_humidity"] == 50
    assert st.values["precip_accumulated"] == 0 and st.values["lightning_strike_count"] == 2
    assert st.fields["station_pressure"].reported_unit == "mb"
    assert st.fields["station_pressure"].canonical_unit == "hPa"
    assert st.extra["envelope_fields"]["future_field"] == {"retained": True}
    assert parsed["obs_air"].values["relative_humidity"] == 51
    assert parsed["obs_air"].fields["battery"].reported_unit is None
    assert parsed["obs_sky"].values["local_day_rain_accumulation"] == 4
    assert parsed["obs_sky"].quality["fields"]["precip_accumulated"]["support"]["kind"] == "unknown"
    assert parsed["evt_strike"].fields["energy"].reported_unit is None
    assert not parsed["evt_strike"].quality["measured_lightning_authority_candidate"]
    assert parsed["evt_precip"].values == {}
    assert parsed["device_status"].fields["firmware_revision"].state == "valid"
    assert parsed["device_status"].extra["sensor_flags"] == ["temperature_failed"]
    assert parsed["device_status"].extra["unknown_sensor_bits"] == 0x40000
    assert parsed["hub_status"].values["radio_status"] == 7


def test_t04_sibling_rows_and_per_field_null_absent_invalid_zero() -> None:
    row = list(ST_ROW)
    row[7:9] = [None, 101]
    result = parse_packet(packet("obs_st", obs=[ST_ROW, [], row, [1700000000, 0]]), NOW)
    assert len(result.rows) == 4
    assert result.rows[1].quarantine_reason == "invalid_source_time"
    assert result.rows[2].fields["air_temperature"].state == "null"
    assert result.rows[2].fields["relative_humidity"].state == "invalid"
    assert result.rows[3].fields["wind_lull"].canonical == 0
    assert result.rows[3].fields["wind_avg"].state == "absent"
    assert result.rows[0].fields["relative_humidity"].state == "valid"


@pytest.mark.parametrize(
    ("raw", "state", "reason"),
    [
        (b'{"type":"rapid_wind","type":"obs_st"}', "quarantined", "duplicate_json_key"),
        (bytes.fromhex("fffe00"), "quarantined", "malformed_utf8"),
        (b'{"type":NaN}', "quarantined", "malformed_json"),
        (b'{"type":"synthetic_future_family","values":[1,null,3]}', "unsupported", None),
    ],
)
def test_t05_t06_t07_raw_errors(raw: bytes, state: str, reason: str | None) -> None:
    result = parse_packet(raw, NOW)
    assert result.state == state and result.reason == reason and not result.rows
    if state == "unsupported":
        assert result.envelope is not None and result.envelope["values"][1] is None


def test_t18_source_time_future_reset_and_t20_unchanged_values() -> None:
    rows = [
        parse_packet(packet("rapid_wind", ob=[stamp, 2.3, 128]), NOW).rows[0]
        for stamp in [1700000000, 1699999900, 1700000000, 1700000003, 1700000300]
    ]
    assert rows[0].source_ts == rows[2].source_ts
    assert rows[0].values == rows[3].values and rows[0].source_ts != rows[3].source_ts
    assert rows[4].future and not rows[4].quality["fields"]["wind_speed"]["eligible"]
    invalid = parse_packet(packet("rapid_wind", ob=[None, 1, 2]), NOW).rows[0]
    assert not invalid.quality["fields"]["wind_speed"]["eligible"]
    assert json_text(Decimal("0.123456789012345678901")) == "0.123456789012345678901"


def test_admission_boundary_quarantine_exact_raw_and_distinct_receipts(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        config = AdmissionConfig(7, "192.0.2.1", "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"}))
        receiver = Receiver(config, spool)
        raw = packet("rapid_wind", ob=[1700000000, 2.3, 128])
        datagram = Datagram(raw, 7, "192.0.2.1", 50222, 1700000000000000001, 11)
        for change, reason in [
            ({"interface_index": 8}, "wrong_interface"),
            ({"source_ip": "192.0.2.2"}, "wrong_source"),
            ({"raw": packet("rapid_wind", serial_number="SYNTHETIC-OTHER")}, "wrong_identity"),
            ({"truncated": True}, "truncated"),
        ]:
            assert receiver.accept(replace(datagram, **change)) is None
            assert receiver.counts["rejected_" + reason] == 1
        first, second = receiver.accept(datagram), receiver.accept(datagram)
        assert first is not None and second is not None and first.id != second.id
        malformed = bytes.fromhex("fffe00")
        quarantined = receiver.accept(replace(datagram, raw=malformed))
        assert quarantined is not None and quarantined.raw == malformed
        assert quarantined.quarantine_reason == "malformed_utf8"
        assert len(spool.batch()) == 3
        assert "192.0.2.1" not in repr(config) + repr(datagram) + repr(quarantined)
    finally:
        spool.close()


def test_t02_reopen_replays_same_uuid_metadata_and_bytes(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    item = receipt(spool)
    assert spool.append(item)
    batch = spool.batch()
    assert spool._conn_required().execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert spool._conn_required().execute("PRAGMA synchronous").fetchone()[0] == 2
    spool.close()
    reopened = Spool(root, 1 << 20)
    try:
        assert reopened.batch() == batch
        assert reopened.batch()[0].receipt == item
        assert reopened.stats()["volatile_loss_unknown"]
        reopened.acknowledge(batch)
        reopened.acknowledge(batch)
        assert reopened.stats()["depth"] == 0
    finally:
        reopened.close()


def test_t03_spool_preserves_same_uuid_alternate_payload(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        first = receipt(spool)
        second = Receipt.create(
            b"alternate",
            spool.incarnation,
            first.received_ns,
            12,
            7,
            "192.0.2.1",
            50222,
            receipt_id=first.id,
        )
        assert spool.append(first) and spool.append(second)
        assert [p.receipt.raw for p in spool.batch()] == [b"synthetic", b"alternate"]
    finally:
        spool.close()


def test_t17_capacity_five_drops_never_overwrite(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20, data_capacity_bytes=1000)
    try:
        item = receipt(spool, b"s" * 300)
        assert spool.append(item)
        before = spool.batch()
        assert all(not spool.append(receipt(spool, b"x" * 300)) for _ in range(5))
        assert spool.batch() == before
        losses = spool.stats()["losses"]
        assert losses == [
            {
                "reason": "capacity_loss",
                "first_ns": str(item.received_ns),
                "last_ns": str(item.received_ns),
                "count": 5,
            }
        ]
        assert spool.stats()["pressure"] == "warning"
    finally:
        spool.close()


def test_unreadable_spool_moved_intact_unknown_loss(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    spool.close()
    path = root / "active" / "records.sqlite"
    path.write_bytes(b"SYNTHETIC CORRUPT SQLITE")
    reopened = Spool(root, 1 << 20)
    try:
        preserved = list((root / "quarantine").glob("*/records.sqlite"))
        assert len(preserved) == 1 and preserved[0].read_bytes() == b"SYNTHETIC CORRUPT SQLITE"
        assert reopened.stats()["diagnostic_loss_unknown"]
        assert reopened.stats()["losses"][0]["count"] is None
    finally:
        reopened.close()


def test_spool_owner_and_invalid_capacity(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    try:
        with pytest.raises(SpoolError, match="active owner"):
            Spool(root, 1 << 20)
        with pytest.raises(SpoolError, match="allowance"):
            Spool(tmp_path / "zero", 1 << 20, data_capacity_bytes=0)
        with pytest.raises(SpoolError, match="checksum"):
            spool.append(replace(receipt(spool), sha256="0" * 64))
        assert isinstance(spool._conn_required(), sqlite3.Connection)
        assert isinstance(receipt(spool).id, uuid.UUID)
    finally:
        spool.close()


def test_coverage_ninety_second_gap_reduced_cadence_and_original_receive_time() -> None:
    from weather.telemetry.coverage import NS, Cadence, arrival_interval

    normal = Cadence(Decimal(60), "normal")
    gap = arrival_interval(0, 150 * NS, normal, normal)
    assert gap is not None and gap.reason == "arrival_gap"
    assert gap.start_ns == 60 * NS and gap.end_ns == 150 * NS
    assert gap.minutes() == [(60 * NS, 60 * NS, 120 * NS), (120 * NS, 120 * NS, 150 * NS)]
    slower = Cadence(Decimal(300), "reduced")
    reduced = arrival_interval(60 * NS, 360 * NS, normal, slower)
    assert reduced is not None and reduced.reason == "reduced_cadence"
    assert arrival_interval(150 * NS, 60 * NS, normal, normal) is None
    assert arrival_interval(0, 150 * NS, None, Cadence(None, "unknown")) is None


def test_t08_disputed_temperature_only_humidity_stays_usable() -> None:
    from weather.telemetry.reconcile import disputed_fields, qualify_conflict

    second = list(ST_ROW)
    second[7] = 23
    first_row = parse_packet(packet("obs_st", obs=[ST_ROW]), NOW).rows[0]
    second_row = parse_packet(packet("obs_st", obs=[second]), NOW).rows[0]
    fields = disputed_fields(first_row.quality, second_row.quality)
    assert fields == ["air_temperature"]
    qualified = qualify_conflict(first_row.quality, fields)
    assert not qualified["fields"]["air_temperature"]["eligible"]
    assert qualified["fields"]["relative_humidity"]["eligible"]
    assert first_row.values["air_temperature"] == 20
    assert first_row.quality["fields"]["air_temperature"]["eligible"]


def test_jsonb_unrepresentable_private_evidence_is_explicit_not_silently_coerced() -> None:
    from weather.telemetry.jsonb import compatible, private_storage

    for value in [Decimal("1e999999"), Decimal("1e-999999"), "\x00", "\ud800"]:
        assert not compatible(value)
        wrapped = private_storage(value)
        assert "_weather_exact_private_json_text" in wrapped
        assert compatible(wrapped)
    assert private_storage({"ordinary": Decimal(".1")}) == {"ordinary": Decimal(".1")}


def test_revision_ddl_has_no_accidental_sqlalchemy_value_bindings() -> None:
    from sqlalchemy import text
    from weather.telemetry.ddl_v2 import statements

    for statement in statements():
        assert not text(statement)._bindparams


def test_unbounded_numeric_expansion_does_not_poison_valid_siblings_or_zero() -> None:
    raw = b'{"type":"rapid_wind","ob":[1700000000,1e-999999,128]}'
    row = parse_packet(raw, NOW).rows[0]
    assert row.fields["wind_speed"].state == "invalid"
    assert row.fields["wind_speed"].reason == "numeric_storage_bound"
    assert row.fields["wind_direction"].state == "valid"
    zero = parse_packet(raw.replace(b"1e-999999", b"0e-999999"), NOW).rows[0]
    assert zero.fields["wind_speed"].state == "valid" and zero.values["wind_speed"] == 0
