# SPDX-License-Identifier: MIT
"""docs/contracts/telemetry-vectors.json unit-suite vectors (parser, admission, spool)."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from weather.telemetry.parse import parse_packet
from weather.telemetry.receiver import AdmissionConfig, Datagram, Receiver
from weather.telemetry.spool import Receipt, Spool

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((ROOT / "docs/contracts/telemetry-vectors.json").read_text())
NOW = Decimal(1700000100)


def vectors(suite: str) -> list[Any]:
    return [pytest.param(v, id=v["id"]) for v in CONTRACT["vectors"] if v["suite"] == suite]


def raw_input(data: dict[str, Any]) -> bytes:
    if "packet" in data:
        return json.dumps(data["packet"]).encode()
    if "utf8" in data:
        return str(data["utf8"]).encode()
    return bytes.fromhex(data["bytes_hex"])


def admitted(raw: bytes, tmp_path: Path) -> bool:
    spool = Spool(tmp_path / "admission", 1 << 20)
    try:
        receiver = Receiver(
            AdmissionConfig(7, "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"})), spool
        )
        item = Datagram(raw, 7, "192.0.2.1", 50222, 1700000001000000000)
        return receiver.accept(item) is not None
    finally:
        spool.close()


def parsed(vector: dict[str, Any], tmp_path: Path) -> None:
    raw, expect = raw_input(vector["input"]), vector["expect"]
    packet = parse_packet(raw, NOW)
    if "parse_state" in expect:
        assert packet.state == expect["parse_state"]
        assert packet.reason == expect.get("reason")
    if "rows" in expect:
        assert len(packet.rows) == expect["rows"]
    if "valid_rows" in expect:
        assert sum(row.quality["valid"] for row in packet.rows) == expect["valid_rows"]
        quarantined = sum(row.quarantine_reason is not None for row in packet.rows)
        assert quarantined == expect["quarantined_rows"]
    if "admitted" in expect:
        assert admitted(raw, tmp_path) is expect["admitted"]
    if "rain_mm" in expect:
        row = packet.rows[0]
        assert row.values["precip_accumulated"] == expect["rain_mm"]
        assert row.support("precip_accumulated") == tuple(expect["rain_support_epochs"])
        assert row.support("wind_avg") == tuple(expect["wind_avg_support_epochs"])
        assert row.support("air_temperature") is None


def spool_boundaries(vector: dict[str, Any], tmp_path: Path) -> None:
    data, expect = vector["input"], vector["expect"]
    root = tmp_path / "spool"
    spool = Spool(root, 1 << 20)
    raws = [f"synthetic-{n}".encode() for n in range(data["committed_receipts"])]
    for raw in raws:
        assert spool.append(Receipt.create(raw, spool.incarnation, 1700000001000000000))
    # An insert whose transaction never commits must not appear after reopen.
    db = spool._conn_required()
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "INSERT INTO receipt(receipt_id,incarnation,received_ns,sha256,raw,charge) "
        "SELECT receipt_id,incarnation,received_ns,sha256,raw,charge FROM receipt LIMIT 1"
    )
    db.close()
    spool._conn = None
    spool.close()
    reopened = Spool(root, 1 << 20)
    try:
        replayed = [item.receipt.raw for item in reopened.batch()]
        assert len(replayed) == expect["replayed_receipts"] and replayed == raws
    finally:
        reopened.close()
    if expect["unreadable_generation_quarantined_intact"]:
        (root / "active" / "records.sqlite").write_bytes(b"SYNTHETIC CORRUPT SQLITE")
        for name in ("records.sqlite-wal", "records.sqlite-shm"):
            (root / "active" / name).unlink(missing_ok=True)
        recovered = Spool(root, 1 << 20)
        try:
            kept = [p.read_bytes() for p in (root / "quarantine").glob("*/records.sqlite")]
            assert kept == [b"SYNTHETIC CORRUPT SQLITE"]
            assert recovered.stats()["unreadable_generations"] == 1
            assert recovered.stats()["depth"] == 0
        finally:
            recovered.close()


def capacity(vector: dict[str, Any], tmp_path: Path) -> None:
    expect = vector["expect"]
    spool = Spool(tmp_path / "spool", 1 << 20, data_capacity_bytes=1000)
    try:
        receiver = Receiver(AdmissionConfig(7, "SYNTHETIC-HUB", frozenset({"SYNTHETIC-ST"})), spool)
        first = json.dumps(
            {"serial_number": "SYNTHETIC-ST", "hub_sn": "SYNTHETIC-HUB", "type": "rapid_wind",
             "ob": [1700000000, 2.3, 128], "pad": "x" * 400}
        ).encode()
        assert receiver.accept(Datagram(first, 7, "192.0.2.1", 50222, 1)) is not None
        before = spool.batch()
        for _ in range(vector["input"]["new_receipts"]):
            assert receiver.accept(Datagram(first, 7, "192.0.2.1", 50222, 2)) is None
        assert (spool.batch() != before) is expect["old_unacknowledged_receipts_overwritten"]
        assert receiver.counts["capacity_loss"] == expect["capacity_drop_count"]
        assert spool.stats()["pressure"] == expect["pressure"]
    finally:
        spool.close()


HANDLERS: dict[str, Callable[[dict[str, Any], Path], None]] = {
    "T04": parsed,
    "T05": parsed,
    "T06": parsed,
    "T07": parsed,
    "T14": parsed,
    "T16": spool_boundaries,
    "T17": capacity,
}


def test_every_vector_belongs_to_a_suite() -> None:
    suites = {vector["suite"] for vector in CONTRACT["vectors"]}
    assert suites <= set(CONTRACT["suites"])
    assert {v["id"] for v in CONTRACT["vectors"] if v["suite"] == "unit"} == set(HANDLERS)


@pytest.mark.parametrize("vector", vectors("unit"))
def test_unit_vector(vector: dict[str, Any], tmp_path: Path) -> None:
    HANDLERS[vector["id"]](vector, tmp_path)
