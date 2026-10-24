# SPDX-License-Identifier: MIT
"""Focused pure tests: no sockets, full image decode or camera access."""

import asyncio
import dataclasses
import errno
import hashlib
import io
import ipaddress
import json
import os
import random
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from weather.capture.chunks import ChunkWriter, IOHealth, new_incarnation, read_chunk
from weather.capture.client import CaptureFailure, Digest, parse_head
from weather.capture.collector import Collector, PendingLoss, Watermark, unsealed_inventory
from weather.capture.config import CaptureConfig
from weather.capture.jpeg import marker_geometry, validate_jpeg
from weather.capture.metrics import CaptureMetrics
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("chunk_seconds", True),
        ("cadence_ms", 1000.5),
        ("phase_ms", 0.5),
        ("read_seconds", True),
        ("https", "false"),
        ("geometry", (True, 16)),
        ("cache_allowance_ms", 0.5),
    ],
)
def test_capture_configuration_does_not_coerce_operational_types(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ValueError, match="types"):
        config(tmp_path, **{field: value})


def test_root_capture_cli_redacts_bad_arguments_without_api_configuration() -> None:
    value = "synthetic-private-argument"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "weather.cli",
            "capture",
            "collect",
            "--camera",
            "skycam_east",
            "--host",
            value,
        ],
        env={key: val for key, val in os.environ.items() if key != "WEATHER_PUBLIC_ORIGIN"},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert result.stderr == "Invalid capture arguments; connection secrets must be mounted\n"
    assert value not in result.stdout + result.stderr


def test_schedule_skips_slow_responses_and_clock_steps_never_reuse_ids() -> None:
    schedule = Schedule(0, 0, 1000, 0)
    first = schedule.take()
    # A slot is overdue only once it is more than half a period late.
    assert schedule.overdue_count(1_500_000_000) == 0
    assert schedule.overdue_count(1_500_000_001) == 1
    assert schedule.overdue_count(2_500_000_001) == 2
    skipped = [schedule.take(), schedule.take()]
    assert schedule.next_mono_ns == 3_000_000_000
    assert schedule.observe_clock(12_500_000_000, 2_500_000_000)
    forward = schedule.take()
    assert schedule.observe_clock(500_000_000, 3_500_000_000)
    backward = schedule.take()
    assert len({x.slot_id for x in [first, *skipped, forward, backward]}) == 5
    assert len({first.clock_epoch, forward.clock_epoch, backward.clock_epoch}) == 3


def test_two_schedules_emit_identical_grid_keys_despite_jitter_and_steps() -> None:
    rng = random.Random(7)
    hour = 1_790_002_800_000_000_000  # An exact UTC hour.

    def run(wall_start: int, mono_start: int, step_at: int) -> list[int]:
        schedule = Schedule(wall_start, mono_start, 1000, 333)
        offset, keys = wall_start - mono_start, []
        for i in range(40):
            if i == step_at:
                offset += 2_400_000_017  # Off-phase wall step: the deadline is re-anchored.
            mono = schedule.next_mono_ns
            schedule.observe_clock(mono + offset + rng.randint(-30_000, 30_000), mono)
            slot = schedule.take()
            assert (slot.scheduled_utc_ns - 333_000_000) % 1_000_000_000 == 0
            assert slot.scheduled_mono_ns + offset - slot.scheduled_utc_ns in range(-30_000, 30_001)
            keys.append(slot.scheduled_utc_ns)
        return keys

    first = run(hour - 10_000_000_000, 5, step_at=99)
    second = run(hour - 6_250_000_123, 987_654_321, step_at=20)
    assert set(second[:20]) <= set(first) and len(set(first)) == 40
    assert utc_text(hour + 333_000_000) in {utc_text(x) for x in first}
    # The key, not the jittered wall time, decides the chunk's hour and window.
    on_hour = Schedule(hour - 1_000_000_000, 0, 1000, 0)
    on_hour.take()
    on_hour.observe_clock(1_000_000_000 + hour - 1_000_000_000 - 30_000, 1_000_000_000)
    key = on_hour.take()
    assert key.scheduled_utc_ns == hour
    assert utc_text(key.scheduled_utc_ns).endswith(":00:00.000000Z")


def test_chunk_hour_and_window_follow_the_grid_key(tmp_path: Path) -> None:
    hour = 1_790_002_800_000_000_000
    key = Slot("grid-0", 0, hour, "clock")
    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, key, IOHealth())
    assert writer.hour == utc_text(hour)[:13].replace(":", "") + "Z"
    assert writer.window_start_ns == hour
    writer.abandon()


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


def test_upper_bound_needs_only_a_continuous_clock_and_calibration_refines_lower() -> None:
    unknown = request_timing(10_000_000_000, 11_000_000_000, 0, 1_000_000_000, None, None)
    assert unknown["capture_upper_bound"] == unknown["response_completed_at_utc"]
    assert unknown["source_lower_bound"] is None and unknown["uncertainty_ms"] is None
    assert unknown["freshness"] == "unknown"
    bounded = request_timing(10_000_000_000, 11_000_000_000, 0, 1_000_000_000, 100, "cal-v1")
    assert bounded["capture_upper_bound"] == unknown["capture_upper_bound"]
    assert bounded["source_time_basis"] == "calibrated_request_midpoint"
    assert bounded["uncertainty_ms"] == 600 and bounded["freshness"] == "plausible"
    for allowance, revision in [(None, None), (100, "cal-v1")]:
        stepped = request_timing(10_000_000_000, 1_000_000_000, 0, 10**9, allowance, revision)
        assert stepped["clock_discontinuity"] and stepped["capture_upper_bound"] is None
        assert stepped["source_lower_bound"] is None


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


def test_repeated_headers_are_combined_and_digest_is_selected() -> None:
    status, headers = parse_head(
        b"HTTP/1.1 401 Unauthorized\r\n"
        b'WWW-Authenticate: Basic realm="cam"\r\n'
        b'WWW-Authenticate: Digest realm="cam, digest", nonce="n1", qop="auth"\r\n'
        b"Set-Cookie: a=1\r\nSet-Cookie: b=2\r\nCache-Control: no-cache\r\n"
        b"Cache-Control: no-store\r\nContent-Length: 0\r\n\r\n"
    )
    assert status == 401 and headers["cache-control"] == "no-cache, no-store"
    digest = Digest("synthetic-user", "synthetic-password")
    digest.challenge(headers["www-authenticate"])
    header = digest.header() or ""
    assert 'realm="cam, digest"' in header and 'nonce="n1"' in header
    digest.challenge('Digest realm="r", nonce="n2", Basic realm="other"')
    assert 'realm="r"' in (digest.header() or "")
    with pytest.raises(CaptureFailure, match="authentication"):
        digest.challenge('Basic realm="cam"')
    for name in ["Content-Length: 1", "Transfer-Encoding: chunked"]:
        with pytest.raises(CaptureFailure, match="protocol"):
            parse_head(f"HTTP/1.1 200 OK\r\n{name}\r\n{name}\r\n\r\n".encode())


@pytest.mark.parametrize("status", ["600", "999", "099", "000", "2_0", "+20", "20", "2000"])
def test_out_of_range_status_is_a_protocol_failure(status: str) -> None:
    with pytest.raises(CaptureFailure, match="protocol") as error:
        parse_head(f"HTTP/1.1 {status} Odd\r\n\r\n".encode())
    assert error.value.status is None


def test_camera_protocol_failure_is_recorded_and_the_loop_keeps_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = Collector(config(tmp_path))

    async def bad_status() -> None:
        parse_head(b"HTTP/1.1 600 Odd\r\n\r\n")

    monkeypatch.setattr(collector.client, "fetch", bad_status)
    schedule = Schedule(time.time_ns(), time.monotonic_ns(), 1000, 0)

    async def two_slots() -> None:
        for _ in range(2):
            await collector._attempt(schedule.take(), schedule)

    asyncio.run(two_slots())
    collector._seal()
    rows = [row for path in (tmp_path / "sealed").rglob("*.tar") for row in read_chunk(path)]
    attempts = sorted(
        (row for row in rows if row["record_type"] == "capture_attempt"),
        key=lambda row: row["slot_at_utc"],
    )
    assert [row["failure_class"] for row in attempts] == ["protocol", "backoff"]
    assert attempts[0]["http_status"] is None


def test_cli_catches_only_configuration_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import weather.capture.cli as cli

    args = ["collect", "--camera", "skycam_east", "--config-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert capsys.readouterr().err == "Invalid mounted capture configuration\n"
    monkeypatch.setattr(cli.CaptureConfig, "load", lambda *a: config(tmp_path))

    def runtime_fault(value: CaptureConfig) -> None:
        raise ValueError("synthetic runtime fault")

    monkeypatch.setattr(cli, "collect", runtime_fault)
    with pytest.raises(ValueError, match="runtime fault"):
        cli.main(args)


def test_attempt_counter_is_one_family_labelled_by_camera_and_result() -> None:
    metrics = CaptureMetrics("skycam_east", IOHealth())
    metrics.result("protocol", 2)
    text = metrics.render().decode()
    assert 'weather_capture_attempts_total{camera="skycam_east",result="protocol"} 2.0' in text
    assert 'weather_capture_attempts_total{camera="skycam_east",result="captured"} 0.0' in text
    assert "weather_capture_attempts_protocol_total" not in text
    assert text.count("# TYPE weather_capture_attempts_total counter") == 1


def test_markers_geometry_and_truncation_without_decoding() -> None:
    data = b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x10\x00\x20\x00\xff\xd9"
    assert marker_geometry(data) == (32, 16)
    with pytest.raises(CaptureFailure, match="jpeg_markers"):
        marker_geometry(data[:-2])
    # No decode: the renderer rejects undecodable frames per slot.
    assert validate_jpeg(data[:-2] + b"\x12\x34\xff\xd9", (32, 16), 64) == (32, 16)
    with pytest.raises(CaptureFailure, match="geometry"):
        validate_jpeg(data, (16, 32), 64)
    with pytest.raises(CaptureFailure, match="oversized"):
        validate_jpeg(data, (32, 16), len(data) - 1)


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
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(path.read_bytes()[:700])
    with pytest.raises(ValueError):
        read_chunk(truncated)
    late = tmp_path / "late.tar"
    with tarfile.open(path) as source, tarfile.open(late, "w", format=tarfile.USTAR_FORMAT) as out:
        for member in source:
            out.addfile(member, source.extractfile(member))
        extra = tarfile.TarInfo("frames/after.jpg")
        extra.size = 1
        out.addfile(extra, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="final manifest"):
        read_chunk(late)


def test_fsync_happens_only_at_seal(tmp_path: Path) -> None:
    calls: list[int] = []
    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth(), calls.append)
    for seq in range(3):
        writer.append(attempt(seq), b"original")
    assert calls == []
    writer.seal()
    assert len(calls) == 1


def test_eio_never_publishes_partial_and_pending_loss_is_bounded(tmp_path: Path) -> None:
    def failed_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "synthetic failure")

    writer = ChunkWriter(config(tmp_path), new_incarnation(), 0, slot(), IOHealth(), failed_fsync)
    writer.append(attempt(), b"original")
    with pytest.raises(OSError):
        writer.seal()
    writer.abandon()
    assert not list((tmp_path / "sealed").rglob("*.tar"))
    # The writer removes its own abandoned tmp file; nothing salvages it.
    assert not writer.path.exists()
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
    collector.storage_retry_ns = 0  # The storage backoff has elapsed.
    collector._persist(slot(2), attempt(2), b"third")
    collector._seal()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1
    rows = read_chunk(paths[0])
    assert rows[1]["failure_class"] == "persist_failed" and rows[1]["attempt_count"] == 2
    assert collector.pending.record is None
    del original


def test_storage_failure_backs_off_and_unlinks_own_tmp_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(self: ChunkWriter, record: dict[str, object], body: bytes | None = None) -> None:
        raise OSError(errno.ENOSPC, "synthetic quota")

    collector = Collector(config(tmp_path))
    monkeypatch.setattr(ChunkWriter, "append", full)
    assert not collector._persist(slot(), attempt(), b"first")
    assert not list((tmp_path / "tmp").rglob("*.tar"))
    assert collector.storage_retry_ns > time.monotonic_ns() + 900_000_000

    def no_nfs(*args: object) -> None:
        pytest.fail("no NFS call during storage backoff")

    monkeypatch.setattr(collector, "_open_writer", no_nfs)
    monkeypatch.setattr(collector, "_occupancy", no_nfs)
    monkeypatch.setattr(collector.client, "fetch", no_nfs)
    assert not collector._persist(slot(1), attempt(1), b"second")
    schedule = Schedule(time.time_ns(), time.monotonic_ns(), 1000, 0)
    asyncio.run(collector._attempt(schedule.take(), schedule))
    monkeypatch.undo()
    collector.storage_retry_ns = 0
    monkeypatch.setattr(ChunkWriter, "append", full)
    assert not collector._persist(slot(2), attempt(2), b"third")
    assert collector.storage_retry_ns > time.monotonic_ns() + 1_900_000_000  # 1 s, then 2 s.
    monkeypatch.undo()
    collector.storage_retry_ns = 0
    assert collector._persist(slot(3), attempt(3), b"fourth")
    collector._seal()
    assert collector.storage_backoff.failures == 0
    assert not list((tmp_path / "tmp").rglob("*.tar"))
    rows = read_chunk(next((tmp_path / "sealed").rglob("*.tar")))
    assert rows[1]["record_type"] == "loss_range" and rows[1]["attempt_count"] == 4


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


SETTINGS = {
    "camera": "skycam_east",
    "geometry": [4096, 1856],
    "cadence_ms": 1000,
    "phase_ms": 0,
    "chunk_seconds": 60,
    "connect_seconds": 1.0,
    "read_seconds": 3.0,
    "total_seconds": 5.0,
    "max_bytes": 16777216,
    "cache_allowance_ms": None,
    "calibration_revision": None,
}
HEADER = {
    "record_type": "chunk_header",
    "schema_version": 2,
    "camera": "skycam_east",
    "pod_uid": "synthetic-pod",
    "incarnation": "synthetic-incarnation",
    "sequence": 0,
    "config_hash": hashlib.sha256(
        json.dumps(SETTINGS, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest(),
    "slot_window_start_utc": "2026-10-24T00:00:00Z",
    "slot_window_end_utc": "2026-10-24T00:01:00Z",
    "settings": SETTINGS,
}
CAPTURED = {
    "record_type": "capture_attempt",
    "attempt_id": "synthetic-attempt",
    "slot_id": "synthetic-slot",
    "slot_at_utc": "2026-10-24T00:00:00Z",
    "clock_epoch": "clock-a",
    "camera": "skycam_east",
    "status": "captured",
    "failure_class": None,
    "member": {
        "name": "frames/synthetic-attempt.jpg",
        "bytes": 100,
        "sha256": "b" * 64,
        "width": 4096,
        "height": 1856,
    },
    "duplicate_hash": False,
    "request_started_at_utc": "2026-10-24T00:00:00Z",
    "response_completed_at_utc": "2026-10-24T00:00:01Z",
    "monotonic_started_ns": "1000000000",
    "monotonic_completed_ns": "2000000000",
    "source_at_utc": "2026-10-24T00:00:00.500000Z",
    "source_time_basis": "request_midpoint",
    "source_lower_bound": None,
    "capture_upper_bound": "2026-10-24T00:00:01Z",
    "uncertainty_ms": None,
    "freshness": "unknown",
    "calibration_revision": None,
    "clock_discontinuity": False,
}


@pytest.mark.parametrize(
    "record,valid",
    [
        (HEADER, True),
        (CAPTURED, True),  # A finite upper bound needs no calibration.
        (CAPTURED | {"capture_upper_bound": None}, True),
        (
            {
                "record_type": "loss_range",
                "failure_class": "persist_failed",
                "first_slot_id": "first",
                "last_slot_id": "last",
                "first_slot_at_utc": "2026-10-24T00:00:00Z",
                "last_slot_at_utc": "2026-10-24T00:00:05Z",
                "attempt_count": 6,
                "individual_details_lost": True,
            },
            True,
        ),
        (HEADER | {"camera_host": "synthetic-private-endpoint"}, False),
        (CAPTURED | {"member": None}, False),
        (CAPTURED | {"clock_discontinuity": True}, False),
        (CAPTURED | {"source_lower_bound": "2026-10-24T00:00:00Z"}, False),
        (CAPTURED | {"http_status": 600}, False),
        (CAPTURED | {"monotonic_completed_ns": "1"}, False),
        (CAPTURED | {"member": CAPTURED["member"] | {"name": "../escape.jpg"}}, False),
    ],
)
def test_manifest_record_fixtures(record: dict[str, object], valid: bool) -> None:
    from weather.capture.records import validate_record

    if valid:
        validate_record(record)
    else:
        with pytest.raises(ValueError, match="Invalid private capture"):
            validate_record(record)


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
    collector._abandon()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1 and read_chunk(paths[0])[1]["status"] == "captured"
    assert collector.pending.record is None
    assert collector.uncertain[0]["classification"] == "seal_uncertain"
    before = paths[0].read_bytes()
    monkeypatch.setattr(chunks, "_fsync_dir", original_fsync_dir)
    collector.storage_retry_ns = 0
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


def test_argument_errors_never_echo_a_mistaken_secret(capsys: pytest.CaptureFixture[str]) -> None:
    from weather.capture.cli import main

    with pytest.raises(SystemExit) as error:
        main(["collect", "--camera", "skycam_east", "--password", "synthetic-private-value"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "synthetic-private-value" not in output.out + output.err


def test_shutdown_flushes_pending_loss_without_fabricating_another_capture(tmp_path: Path) -> None:
    collector = Collector(config(tmp_path))
    collector.pending.add(attempt())
    collector._finish()
    paths = list((tmp_path / "sealed").rglob("*.tar"))
    assert len(paths) == 1
    records = read_chunk(paths[0])
    assert [row["record_type"] for row in records] == ["chunk_header", "loss_range"]
    assert records[1]["attempt_count"] == 1 and collector.pending.record is None
