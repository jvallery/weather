# SPDX-License-Identifier: MIT
"""Bounded pure tests only: all identity/address values are explicitly synthetic."""

import json
import socket
import sqlite3
import struct
import sys
import threading
import time
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from weather.telemetry.parse import FAMILIES, json_text, parse_packet
from weather.telemetry.receiver import (
    IP_PKTINFO,
    SO_RXQ_OVFL,
    AdmissionConfig,
    Datagram,
    Receiver,
    pktinfo_interface,
)
from weather.telemetry.spool import Receipt, Spool, SpoolError

NOW = Decimal(1700000100)
ST_ROW = [1700000000, 0.1, 0.2, 0.3, 90, 3, 1000, 20, 50, 100, 1, 50, 0, 0, 10, 2, 2.5, 1]
ADMISSION = AdmissionConfig(7, "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"}))


def packet(family: str, **data: object) -> bytes:
    return json.dumps(
        {"serial_number": "SYNTHETIC-ST", "hub_sn": "SYNTHETIC-HUB", "type": family, **data}
    ).encode()


def receipt(spool: Spool, raw: bytes = b"synthetic") -> Receipt:
    return Receipt.create(raw, spool.incarnation, 1700000000000000001)


def datagram(raw: bytes | None = None, **change: object) -> Datagram:
    value = Datagram(
        raw or packet("rapid_wind", ob=[1700000000, 2.3, 128]),
        7,
        "192.0.2.1",
        50222,
        1700000001000000000,
    )
    return replace(value, **change)  # type: ignore[arg-type]


def test_all_families_official_positions_and_units() -> None:
    samples = {
        "obs_st": {"obs": [ST_ROW], "future_field": {"retained": True}},
        "rapid_wind": {"ob": [1700000000, 2.3, 128]},
        "evt_precip": {"evt": [1700000000]},
        "evt_strike": {"evt": [1700000000, 10, 100]},
        "obs_air": {"obs": [[1700000000, 1001, 21, 51, 3, 11, 2.6, 2]]},
        "obs_sky": {"obs": [[1700000000, 100, 1, 2, 0.1, 0.2, 0.3, 90, 2.5, 1, 50, 4, 2, 3]]},
        "device_status": {"timestamp": 1700000000, "firmware_revision": 171, "sensor_status": 16},
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
    assert parsed["obs_air"].values["relative_humidity"] == 51
    assert parsed["obs_air"].fields["battery"].reported_unit is None
    assert parsed["obs_sky"].values["local_day_rain_accumulation"] == 4
    assert parsed["evt_strike"].fields["energy"].reported_unit is None
    assert parsed["evt_precip"].values == {}
    assert parsed["device_status"].fields["firmware_revision"].state == "valid"
    assert parsed["device_status"].values["sensor_status"] == 16
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


def test_quality_is_a_compact_status_with_invalid_field_names() -> None:
    row = list(ST_ROW)
    row[8] = 101
    parsed = parse_packet(packet("obs_st", obs=[row]), NOW).rows[0]
    assert parsed.quality == {"valid": True, "reason": "valid", "invalid": ["relative_humidity"]}
    assert parsed.values["relative_humidity"] is None
    assert len(json_text(parsed.quality)) < 100


def test_source_time_future_reset_and_unchanged_values() -> None:
    rows = [
        parse_packet(packet("rapid_wind", ob=[stamp, 2.3, 128]), NOW).rows[0]
        for stamp in [1700000000, 1699999900, 1700000000, 1700000003, 1700000300]
    ]
    assert rows[0].source_ts == rows[2].source_ts
    assert rows[0].values == rows[3].values and rows[0].source_ts != rows[3].source_ts
    assert rows[4].future and rows[4].quality["reason"] == "future_timestamp"
    assert not rows[4].quality["valid"]
    invalid = parse_packet(packet("rapid_wind", ob=[None, 1, 2]), NOW).rows[0]
    assert invalid.quarantine_reason == "invalid_source_time" and not invalid.quality["valid"]
    assert json_text(Decimal("0.123456789012345678901")) == "0.123456789012345678901"


def test_unbounded_numeric_and_unstorable_text_do_not_poison_siblings() -> None:
    raw = b'{"type":"rapid_wind","ob":[1700000000,1e-999999,128]}'
    row = parse_packet(raw, NOW).rows[0]
    assert row.fields["wind_speed"].state == "invalid"
    assert row.fields["wind_speed"].reason == "numeric_storage_bound"
    assert row.fields["wind_direction"].state == "valid"
    zero = parse_packet(raw.replace(b"1e-999999", b"0e-999999"), NOW).rows[0]
    assert zero.fields["wind_speed"].state == "valid" and zero.values["wind_speed"] == 0
    status = parse_packet(
        packet("hub_status", timestamp=1700000000, firmware_revision="1\u0000", uptime=5), NOW
    ).rows[0]
    assert status.fields["firmware_revision"].state == "invalid"
    assert status.values["uptime"] == 5


def test_admission_by_interface_and_identity_without_source_address(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        receiver = Receiver(ADMISSION, spool)
        for change, reason in [
            ({"interface_index": 8}, "wrong_interface"),
            ({"raw": packet("rapid_wind", serial_number="SYNTHETIC-OTHER")}, "wrong_identity"),
            ({"raw": packet("rapid_wind", hub_sn="SYNTHETIC-OTHER")}, "wrong_identity"),
            ({"raw": bytes.fromhex("fffe00")}, "malformed"),
            ({"raw": b'{"type":"rapid_wind","type":"obs_st"}'}, "malformed"),
            ({"raw": b" " * 4097}, "oversized"),
            ({"truncated": True}, "oversized"),
        ]:
            assert receiver.accept(datagram(**change)) is None
            assert receiver.counts["rejected_" + reason] >= 1
        # No source address is configured, so any sender on the interface is admitted.
        first = receiver.accept(datagram(source_ip="198.51.100.9"))
        second = receiver.accept(datagram())
        assert first is not None and second is not None and first.id != second.id
        assert first.raw == second.raw
        assert len(spool.batch()) == 2
        assert "192.0.2.1" not in repr(ADMISSION) + repr(datagram()) + repr(first)
    finally:
        spool.close()


def test_optional_source_address_and_port_still_filter(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        config = replace(ADMISSION, hub_source_ip="192.0.2.1", hub_source_port=50222)
        receiver = Receiver(config, spool)
        assert receiver.accept(datagram(source_ip="192.0.2.2")) is None
        assert receiver.accept(datagram(source_port=1)) is None
        assert receiver.counts["rejected_wrong_source"] == 2
        assert receiver.accept(datagram()) is not None
    finally:
        spool.close()


def test_missing_hub_is_raw_quarantine_and_unknown_hub_family_is_kept(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        receiver = Receiver(ADMISSION, spool)
        raw = json.dumps(
            {"type": "rapid_wind", "serial_number": "SYNTHETIC-ST", "ob": [1700000000, 1, 2]}
        ).encode()
        stored = receiver.accept(datagram(raw))
        assert stored is not None and stored.quarantine_reason == "missing_hub_identity"
        assert stored.raw == raw and receiver.counts["quarantined"] == 1
        future = json.dumps({"type": "synthetic_future", "serial_number": "SYNTHETIC-HUB"})
        assert receiver.accept(datagram(future.encode())) is not None
        assert spool.stats()["depth"] == 2
    finally:
        spool.close()


class SyntheticSocket:
    def __init__(self, raw: bytes, interface: int, flags: int = 0) -> None:
        self.raw, self.interface, self.flags = raw, interface, flags

    def recvmsg(self, size: int, ancillary_size: int) -> tuple[object, ...]:
        assert size == 4096 and ancillary_size > 0
        pktinfo = struct.pack("=I4s4s", self.interface, bytes(4), bytes(4))
        return (
            self.raw,
            [
                (socket.IPPROTO_IP, IP_PKTINFO, pktinfo),
                (socket.SOL_SOCKET, SO_RXQ_OVFL, struct.pack("=I", 3)),
            ],
            self.flags,
            ("192.0.2.1", 50222),
        )


def test_recvmsg_with_synthetic_pktinfo_and_overflow(tmp_path: Path) -> None:
    assert pktinfo_interface([]) == 0
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        receiver = Receiver(ADMISSION, spool)
        raw = packet("rapid_wind", ob=[1700000000, 2.3, 128])
        receiver.receive(SyntheticSocket(raw, 7))  # type: ignore[arg-type]
        receiver.receive(SyntheticSocket(raw, 9))  # type: ignore[arg-type]
        receiver.receive(SyntheticSocket(raw, 7, socket.MSG_TRUNC))  # type: ignore[arg-type]
        assert receiver.counts["spooled"] == 1
        assert receiver.counts["rejected_wrong_interface"] == 1
        assert receiver.counts["rejected_oversized"] == 1
        assert receiver.counts["kernel_overflow"] == 3
    finally:
        spool.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux socket options only")
def test_linux_recvmsg_binds_device_and_reads_pktinfo(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    spool = Spool(tmp_path / "spool", 1 << 20)
    receiver = Receiver(replace(ADMISSION, interface_index=socket.if_nametoindex("lo")), spool)
    stop, errors = threading.Event(), []

    def run() -> None:
        try:
            receiver.run(stop, "lo", port)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            deadline = time.monotonic() + 10
            while not receiver.counts["spooled"] and not errors and time.monotonic() < deadline:
                sender.sendto(packet("rapid_wind", ob=[1700000000, 2.3, 128]), ("127.0.0.1", port))
                time.sleep(0.05)
            sender.sendto(b" " * 5000, ("127.0.0.1", port))
            while not receiver.counts["rejected_oversized"] and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        stop.set()
        thread.join(3)
        spool.close()
    assert not errors, type(errors[0]).__name__ if errors else ""
    assert receiver.counts["spooled"] >= 1 and receiver.counts["rejected_oversized"] == 1
    assert receiver.counts["rejected_wrong_interface"] == 0


def test_reopen_replays_same_uuid_and_bytes(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    item = replace(receipt(spool), quarantine_reason="missing_hub_identity")
    assert spool.append(item)
    batch = spool.batch()
    assert spool._conn_required().execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert spool._conn_required().execute("PRAGMA synchronous").fetchone()[0] == 2
    spool.close()
    reopened = Spool(root, 1 << 20)
    try:
        assert reopened.batch() == batch
        assert reopened.batch()[0].receipt == item
        reopened.acknowledge(batch)
        reopened.acknowledge(batch)
        assert reopened.stats()["depth"] == 0 and reopened.stats()["pending_bytes"] == 0
    finally:
        reopened.close()


def test_one_bad_spool_row_is_quarantined_alone_and_draining_continues(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    try:
        items = [receipt(spool, raw) for raw in (b"synthetic-1", b"synthetic-2", b"synthetic-3")]
        assert all(spool.append(item) for item in items)
        spool._conn_required().execute("UPDATE receipt SET sha256='0' WHERE sequence=2")
        before = spool.stats()["physical_bytes"]
        batch = spool.batch()
        assert [p.receipt.raw for p in batch] == [b"synthetic-1", b"synthetic-3"]
        stats = spool.stats()
        assert stats["quarantined_rows"] == 1 and stats["depth"] == 2
        assert stats["unreadable_generations"] == 0
        assert stats["physical_bytes"] <= before
        assert [p.read_bytes() for p in (root / "quarantine").glob("row-*.bin")] == [
            b"synthetic-2"
        ]
        spool.acknowledge(batch)
        assert spool.stats()["depth"] == 0 and spool.batch() == []
    finally:
        spool.close()


def test_metrics_scrape_never_mutates_the_spool(tmp_path: Path) -> None:
    from typing import cast

    from prometheus_client import generate_latest
    from sqlalchemy.ext.asyncio import AsyncEngine
    from weather.telemetry.drain import Drain
    from weather.telemetry.metrics import Metrics

    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        receiver = Receiver(ADMISSION, spool)
        assert receiver.accept(datagram()) is not None
        spool._conn_required().execute("UPDATE receipt SET sha256='0'")
        metrics = Metrics(receiver, Drain(spool, cast(AsyncEngine, object())))
        body = generate_latest(metrics.registry)
        assert b"weather_telemetry_drain_lag_seconds" in body
        assert spool.stats()["depth"] == 1 and spool.stats()["quarantined_rows"] == 0
        assert not (tmp_path / "spool" / "quarantine").exists()
    finally:
        spool.close()


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
        with pytest.raises(SpoolError, match="metadata"):
            Receipt.create(b" " * 4097, spool.incarnation, 1)
        assert isinstance(spool._conn_required(), sqlite3.Connection)
        assert isinstance(receipt(spool).id, uuid.UUID)
    finally:
        spool.close()


def test_schema_ddl_has_no_accidental_sqlalchemy_value_bindings() -> None:
    from sqlalchemy import text
    from weather.db.ddl_v1 import statements
    from weather.db.models import metadata

    for statement in statements([table.name for table in metadata.sorted_tables]):
        assert not text(statement)._bindparams


def test_metrics_fixed_labels_and_config_errors_do_not_expose_private_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    from typing import cast

    from prometheus_client import generate_latest
    from sqlalchemy.ext.asyncio import AsyncEngine
    from weather.telemetry.config import load_config
    from weather.telemetry.drain import Drain
    from weather.telemetry.metrics import Metrics

    (tmp_path / "receiver.json").write_text(json.dumps({"interface_name": "net1"}))
    (tmp_path / "TEMPEST_HUB_SERIAL").write_text("SYNTHETIC-HUB")
    (tmp_path / "TEMPEST_DEVICE_SERIALS").write_text(json.dumps(["SYNTHETIC-ST"]))
    config = load_config(tmp_path)
    assert "SYNTHETIC" not in repr(config)
    spool = Spool(tmp_path / "spool", 1 << 20)
    try:
        admission = AdmissionConfig(7, config.hub_identity, config.device_identities)
        receiver = Receiver(admission, spool)
        receiver.accept(datagram())
        metrics = Metrics(receiver, Drain(spool, cast(AsyncEngine, object())))
        body = generate_latest(metrics.registry).decode()
        assert "weather_telemetry_received_total 1.0" in body
        assert 'family="rapid_wind"' in body and 'reason="oversized"' in body
        assert "192.0.2.1" not in body and "SYNTHETIC" not in body
        (tmp_path / "TEMPEST_HUB_SOURCE").write_text("PRIVATE-BAD")
        with pytest.raises(ValueError) as error:
            load_config(tmp_path)
        assert "PRIVATE-BAD" not in str(error.value)
        captured = capsys.readouterr()
        assert not captured.out and not captured.err and not caplog.text
    finally:
        spool.close()
