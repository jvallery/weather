# SPDX-License-Identifier: MIT
"""Mounted-key and argument privacy contracts; every identity/address is synthetic."""

import json
import sys
from pathlib import Path

import pytest
from weather.telemetry.config import load_config


@pytest.fixture
def mounted(tmp_path: Path) -> Path:
    (tmp_path / "receiver.json").write_text(json.dumps({"interface_name": "net1"}))
    (tmp_path / "TEMPEST_HUB_SERIAL").write_text("SYNTHETIC-HUB")
    (tmp_path / "TEMPEST_DEVICE_SERIALS").write_text(json.dumps(["SYNTHETIC-ST"]))
    return tmp_path


def test_exact_issue5_keys_are_loaded_without_private_public_config(mounted: Path) -> None:
    config = load_config(mounted)
    assert config.hub_source_ip is None
    (mounted / "TEMPEST_HUB_SOURCE").write_text("192.0.2.1")
    config = load_config(mounted)
    assert config.hub_source_ip == "192.0.2.1"
    assert config.hub_identity == "SYNTHETIC-HUB"
    assert config.device_identities == frozenset({"SYNTHETIC-ST"})
    assert config.capacity_bytes == 1 << 30
    assert "192.0.2.1" not in repr(config) and "SYNTHETIC" not in repr(config)


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("TEMPEST_HUB_SOURCE", b"192.0.2.1\n"),
        ("TEMPEST_HUB_SERIAL", b"SYNTHETIC-HUB\n"),
        ("TEMPEST_HUB_SERIAL", b"x" * 129),
        ("TEMPEST_HUB_SERIAL", bytes.fromhex("fffe")),
        ("TEMPEST_DEVICE_SERIALS", b'"SYNTHETIC-ST"'),
        ("TEMPEST_DEVICE_SERIALS", b'["SYNTHETIC-ST","SYNTHETIC-ST"]'),
        ("TEMPEST_DEVICE_SERIALS", b'["SYNTHETIC-ST",true]'),
        ("TEMPEST_DEVICE_SERIALS", b" " * 8193),
        ("receiver.json", b'{"interface_name":"net1","interface_name":"net2"}'),
        ("receiver.json", b'{"interface_name":"net1","hub_identity":"SYNTHETIC-HUB"}'),
    ],
)
def test_malformed_or_misplaced_configuration_fails_without_values(
    mounted: Path, name: str, raw: bytes
) -> None:
    (mounted / name).write_bytes(raw)
    with pytest.raises(ValueError) as error:
        load_config(mounted)
    assert str(error.value) == "Invalid mounted private receiver configuration"


def test_missing_secret_serial_fails_without_values(mounted: Path) -> None:
    (mounted / "TEMPEST_HUB_SERIAL").unlink()
    with pytest.raises(ValueError, match="^Invalid mounted private receiver configuration$"):
        load_config(mounted)


def test_tempest_role_dispatch_is_lazy_and_does_not_start_a_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from weather import cli
    from weather.telemetry import runtime

    calls: list[bool] = []
    monkeypatch.setattr(runtime, "main", lambda: calls.append(True))
    monkeypatch.setattr(sys, "argv", ["weather", "tempest-receiver"])
    cli.main()
    assert calls == [True]


def test_tempest_role_rejects_private_arguments_without_echo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from weather import cli

    monkeypatch.setattr(
        sys, "argv", ["weather", "tempest-receiver", "--hub-source", "SYNTHETIC-PRIVATE"]
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "tempest-receiver accepts mounted configuration only\n"


def test_reproject_role_requires_an_aware_since_and_dispatches_lazily(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from weather import cli
    from weather.telemetry import runtime

    seen: list[datetime] = []

    async def fake(since: datetime) -> tuple[int, int, int]:
        seen.append(since)
        return 3, 2, 1

    monkeypatch.setattr(runtime, "run_reproject", fake)
    argv = ["weather", "tempest-receiver", "reproject", "--since", "2026-10-01T00:00Z"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert seen == [datetime(2026, 10, 1, tzinfo=UTC)]
    assert capsys.readouterr().out == "reprojected receipts=3 new_rows=2 unprojected_rows=1\n"
    monkeypatch.setattr(sys, "argv", [*argv[:-1], "2026-10-01T00:00"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and len(seen) == 1
