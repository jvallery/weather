# SPDX-License-Identifier: MIT
"""Focused pure tests: no sockets, full image decode or camera access."""

import dataclasses
import errno
import hashlib
import ipaddress
import json
import tarfile
from pathlib import Path

import pytest
from weather.capture.chunks import ChunkWriter, IOHealth, new_incarnation, read_chunk
from weather.capture.client import CaptureFailure, Digest
from weather.capture.collector import Collector, PendingLoss, Watermark, unsealed_inventory
from weather.capture.config import CaptureConfig
from weather.capture.jpeg import marker_geometry
from weather.capture.timing import Backoff, Schedule, Slot, request_timing, utc_text


def config(tmp_path: Path, **kwargs: object) -> CaptureConfig:
    values = dict(
        camera="skycam_east",
        staging=tmp_path,
        pod_uid="synthetic-pod",
        host=str(ipaddress.IPv4Address(0x7F000001)),
        port=8080,
        username="synthetic-user",
        password="synthetic-password",
        geometry=(32, 16),
    )
    return CaptureConfig(**(values | kwargs))


def slot(seq: int = 0) -> Slot:
    return Slot(
        f"synthetic-{seq}",
        seq * 1_000_000_000,
        1_790_000_000_000_000_000 + seq * 1_000_000_000,
        "clock",
    )


def attempt(seq: int = 0) -> dict[str, object]:
    return {
        "record_type": "capture_attempt",
        "attempt_id": slot(seq).slot_id,
        "slot_id": slot(seq).slot_id,
        "slot_at_utc": utc_text(slot(seq).scheduled_utc_ns),
        "clock_epoch": "clock",
        "camera": "skycam_east",
        "status": "captured",
        "failure_class": None,
        "member": None,
        **request_timing(10_000_000_000, 11_000_000_000, 0, 1_000_000_000, None, None),
    }


def test_config_private_values_do_not_serialize(tmp_path: Path) -> None:
    value = config(tmp_path)
    public = repr(value) + json.dumps(value.public_settings()) + value.config_hash
    assert (
        value.username not in public and value.password not in public and value.host not in public
    )
    assert value.config_hash == dataclasses.replace(value, password="different-secret").config_hash
    with pytest.raises(ValueError, match="literal"):
        dataclasses.replace(value, host="camera.invalid")
    for field, value in [("cadence_ms", 999), ("total_seconds", 6), ("chunk_seconds", 301)]:
        with pytest.raises(ValueError):
            config(tmp_path, **{field: value})


def test_schedule_skips_slow_responses_and_clock_steps_never_reuse_ids() -> None:
    schedule = Schedule(0, 0, 1000, 0)
    first = schedule.take()
    assert schedule.overdue_count(2_500_000_000) == 2
    skipped = [schedule.take(), schedule.take()]
    assert schedule.next_mono_ns == 3_000_000_000
    assert schedule.observe_clock(12_500_000_000, 2_500_000_000)
    forward = schedule.take()
    assert schedule.observe_clock(500_000_000, 3_500_000_000)
    backward = schedule.take()
    assert len({x.slot_id for x in [first, *skipped, forward, backward]}) == 5
    assert len({first.clock_epoch, forward.clock_epoch, backward.clock_epoch}) == 3


def test_backoff_and_pressure_hysteresis() -> None:
    backoff = Backoff()
    assert [backoff.failure() for _ in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert backoff.failure(True) >= 60
    backoff.success()
    assert backoff.failure() == 1
    pressure = Watermark()
    assert pressure.update(0.90)
    assert pressure.update(0.80)
    assert pressure.update(0.75)
    assert not pressure.update(0.749)


def test_timing_unknown_is_not_eligible_and_wall_step_is_not_bounded() -> None:
    unknown = request_timing(10_000_000_000, 11_000_000_000, 0, 1_000_000_000, None, None)
    assert unknown["capture_upper_bound"] is None and unknown["uncertainty_ms"] is None
    bounded = request_timing(10_000_000_000, 11_000_000_000, 0, 1_000_000_000, 100, "cal-v1")
    assert bounded["source_time_basis"] == "calibrated_request_midpoint"
    assert bounded["uncertainty_ms"] == 600
    stepped = request_timing(10_000_000_000, 1_000_000_000, 0, 1_000_000_000, 100, "cal-v1")
    assert stepped["clock_discontinuity"] and stepped["capture_upper_bound"] is None


def test_digest_calculation_and_header_injection_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("weather.capture.client.secrets.token_hex", lambda count: "0a4f113b")
    digest = Digest("Mufasa", "Circle Of Life")
    digest.challenge(
        'Digest realm="testrealm@host.com", qop="auth,auth-int", nonce="abc", opaque="xyz"'
    )
    header = digest.header()
    assert header is not None and "nc=00000001" in header and 'cnonce="0a4f113b"' in header
    ha1 = hashlib.md5(b"Mufasa:testrealm@host.com:Circle Of Life").hexdigest()
    ha2 = hashlib.md5(b"GET:/cgi-bin/snapshot.cgi?channel=1").hexdigest()
    expected = hashlib.md5(f"{ha1}:abc:00000001:0a4f113b:auth:{ha2}".encode()).hexdigest()
    assert f'response="{expected}"' in header
    assert "nc=00000002" in (digest.header() or "")
    with pytest.raises(CaptureFailure):
        digest.challenge('Digest realm="unsafe\r\nX-Test: injected", nonce="abc"')


def test_markers_geometry_and_truncation_without_decoding() -> None:
    data = b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x10\x00\x20\x00\xff\xd9"
    assert marker_geometry(data) == (32, 16)
    with pytest.raises(CaptureFailure, match="jpeg_markers"):
        marker_geometry(data[:-2])


def test_atomic_chunk_native_bytes_and_concurrent_names(tmp_path: Path) -> None:
    cfg, body = config(tmp_path), b"synthetic original bytes, not a decoded image"
    paths = []
    for _ in range(2):
        writer = ChunkWriter(cfg, new_incarnation(), 0, slot(), IOHealth())
        writer.append(attempt(), body)
        paths.append(writer.seal())
    assert paths[0] != paths[1]
    for path in paths:
        records = read_chunk(path)
        assert records[1]["member"]["sha256"] == hashlib.sha256(body).hexdigest()
        raw = path.read_bytes()
        assert cfg.host.encode() not in raw and cfg.password.encode() not in raw
        with tarfile.open(path) as archive:
            assert [x.name for x in archive] == ["frames/synthetic-0.jpg", "manifest.jsonl"]
            stream = archive.extractfile("frames/synthetic-0.jpg")
            assert stream is not None and stream.read() == body


def test_reader_refuses_corruption_links_and_traversal(tmp_path: Path) -> None:
    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth())
    writer.append(attempt(), b"original")
    path = writer.seal()
    corrupt = tmp_path / "corrupt.tar"
    raw = bytearray(path.read_bytes())
    raw[512] ^= 1
    corrupt.write_bytes(raw)
    with pytest.raises(ValueError, match="hash"):
        read_chunk(corrupt)
    for name, kind in [("../escape", tarfile.REGTYPE), ("frames/linked.jpg", tarfile.SYMTYPE)]:
        bad = tmp_path / "unsafe.tar"
        with tarfile.open(bad, "w", format=tarfile.USTAR_FORMAT) as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            archive.addfile(member)
        with pytest.raises(ValueError):
            read_chunk(bad)


def test_eio_never_publishes_partial_and_pending_loss_is_bounded(tmp_path: Path) -> None:
    def failed_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "synthetic failure")

    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth(), failed_fsync)
    with pytest.raises(OSError):
        writer.append(attempt(), b"original")
    writer.abandon()
    assert not list((tmp_path / "sealed").rglob("*.tar"))
    assert writer.path.exists()
    pending = PendingLoss()
    for i in range(1000):
        pending.add({**attempt(i), "status": "request_failed", "failure_class": "transport"})
    assert pending.record is not None and pending.record["attempt_count"] == 1000
    assert len(json.dumps(pending.record)) < 1000


def test_collector_persists_failed_slots_in_next_successful_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = Collector(config(tmp_path))
    collector._persist(slot(), attempt(), b"first")
    assert collector.writer is not None
    original = collector.writer.append

    def fail(record: dict[str, object], body: bytes | None = None) -> None:
        raise OSError(errno.ESTALE, "synthetic mount failure")

    monkeypatch.setattr(collector.writer, "append", fail)
    collector._persist(slot(1), attempt(1), b"second")
    assert collector.writer is None and collector.pending.record is not None
    assert collector.pending.record["attempt_count"] == 2
    collector._persist(slot(2), attempt(2), b"third")
    collector._seal()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1
    rows = read_chunk(paths[0])
    assert rows[1]["failure_class"] == "persist_failed" and rows[1]["attempt_count"] == 2
    assert collector.pending.record is None
    del original


def test_unsealed_inventory_is_uncertain_and_never_opens_foreign_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth())
    writer.append(attempt(), b"potentially still in flight")
    monkeypatch.setattr(
        Path, "open", lambda *args, **kwargs: pytest.fail("must not open another file")
    )
    records = unsealed_inventory(tmp_path, "skycam_east")
    assert records and records[0]["classification"] == "unsealed_uncertain"
    assert "failure_class" not in records[0]
    writer.abandon()


def test_blocked_filesystem_fails_liveness_without_waiting() -> None:
    health = IOHealth()
    health.started_ns = 1
    assert health.healthy(30_000_000_001)
    assert not health.healthy(30_000_000_002)


def test_hard_kill_leaves_no_seal_and_later_manifest_keeps_uncertainty(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    script = """
import ipaddress,os,sys
from pathlib import Path
from weather.capture.chunks import ChunkWriter,IOHealth,new_incarnation
from weather.capture.config import CaptureConfig
from weather.capture.timing import Slot
root=Path(sys.argv[1])
c=CaptureConfig(camera="skycam_east",staging=root,pod_uid="killed-pod",
 host=str(ipaddress.IPv4Address(0x7F000001)),port=8080,username="fake-user",password="fake-pass")
s=Slot("killed-slot",0,1790000000000000000,"clock")
w=ChunkWriter(c,new_incarnation(),0,s,IOHealth())
w.append({"record_type":"capture_attempt","attempt_id":"killed-slot","slot_id":"killed-slot",
 "status":"skipped","failure_class":"backoff","member":None,
 "slot_at_utc":"2026-10-24T00:00:00Z","clock_epoch":"clock","camera":"skycam_east"},None)
os._exit(17)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], env=env)
    assert result.returncode == 17
    assert not list((tmp_path / "sealed").rglob("*.tar"))
    collector = Collector(config(tmp_path))
    collector.uncertain = unsealed_inventory(tmp_path, "skycam_east")
    collector._persist(slot(1), attempt(1), b"later original")
    collector._seal()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1
    rows = read_chunk(paths[0])
    assert rows[1]["classification"] == "unsealed_uncertain"
    assert rows[1]["individual_details_lost"] and "failure_class" not in rows[1]


def test_read_only_paths_outside_staging_are_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    original_open = io.open

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if isinstance(file, (str, Path)) and any(x in mode for x in "wax+"):
            if not Path(file).is_relative_to(tmp_path):
                raise PermissionError("Synthetic read-only root filesystem")
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth())
    writer.append(attempt(), b"original")
    assert read_chunk(writer.seal())[1]["status"] == "captured"


def test_code_first_capture_contract_fixtures_and_generated_schema() -> None:
    from weather.capture.records import ADAPTER, validate_record

    root = Path(__file__).resolve().parents[3]
    fixtures = json.loads((root / "docs/contracts/capture-fixtures.json").read_text())["fixtures"]
    for case in fixtures:
        if case["valid"]:
            validate_record(case["value"])
        else:
            with pytest.raises(ValueError, match="Invalid private capture"):
                validate_record(case["value"])
    schema = json.loads((root / "docs/contracts/capture.schema.json").read_text())
    for key, value in ADAPTER.json_schema().items():
        assert schema[key] == value


def test_exposure_upper_bound_rounds_outward_without_float_timestamp_loss() -> None:
    value = request_timing(10_000_000_001, 11_000_000_001, 0, 1_000_000_000, 0, "cal-v1")
    assert value["capture_upper_bound"] == "1970-01-01T00:00:11.000001Z"
    assert value["source_lower_bound"] == "1970-01-01T00:00:10.000000Z"
    assert value["uncertainty_ms"] == 500.001


def test_post_rename_fsync_error_is_uncertain_without_mutating_sealed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weather.capture.chunks as chunks

    collector = Collector(config(tmp_path))
    collector._persist(slot(), attempt(), b"original")
    original_fsync_dir = chunks._fsync_dir

    def fail(path: Path) -> None:
        raise OSError(errno.EIO, "synthetic directory sync failure")

    monkeypatch.setattr(chunks, "_fsync_dir", fail)
    with pytest.raises(OSError):
        collector._seal()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1 and read_chunk(paths[0])[1]["status"] == "captured"
    assert collector.pending.record is None
    assert collector.uncertain[0]["classification"] == "seal_uncertain"
    before = paths[0].read_bytes()
    monkeypatch.setattr(chunks, "_fsync_dir", original_fsync_dir)
    collector._persist(slot(1), attempt(1), b"later original")
    collector._seal()
    assert paths[0].read_bytes() == before
    assert collector.metrics.seal_uncertain._value.get() == 1


def test_mounted_secret_fields_and_camera_phase_are_not_arguments(tmp_path: Path) -> None:
    config_dir, secret_dir = tmp_path / "config", tmp_path / "secrets"
    config_dir.mkdir()
    secret_dir.mkdir()
    (config_dir / "capture.json").write_text(json.dumps({"staging": str(tmp_path)}))
    (config_dir / "POD_UID").write_text("synthetic-pod")
    for name, value in {
        "CAMERA_HOST": str(ipaddress.IPv4Address(0x7F000001)),
        "CAMERA_PORT": "8080",
        "CAMERA_USER": "synthetic-user",
        "CAMERA_PASSWORD": " synthetic-password ",
    }.items():
        (secret_dir / name).write_text(value)
    loaded = CaptureConfig.load("skycam_west", config_dir, secret_dir)
    assert loaded.phase_ms == 333 and loaded.password == " synthetic-password "
    assert "synthetic-password" not in repr(loaded)
