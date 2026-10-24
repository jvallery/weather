# SPDX-License-Identifier: MIT
"""Private receiver configuration is mounted; no identities/addresses in argv or repr."""

import ipaddress
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReceiverConfig:
    interface_name: str
    hub_source_ip: str = field(repr=False)
    hub_identity: str = field(repr=False)
    device_identities: frozenset[str] = field(repr=False)
    hub_source_port: int | None = field(default=None, repr=False)
    spool_directory: Path = Path("/var/lib/weather/telemetry")
    capacity_bytes: int = 1 << 30
    future_seconds: int = 120
    metrics_port: int = 9000


def load_config(directory: Path) -> ReceiverConfig:
    try:
        data: Any = json.loads(
            _read(directory / "receiver.json", 65536),
            object_pairs_hook=_unique,
            parse_constant=_invalid_constant,
        )
        allowed = {
            "interface_name",
            "hub_source_port",
            "spool_directory",
            "capacity_bytes",
            "future_seconds",
            "metrics_port",
        }
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError
        if not _atom(data["interface_name"]):
            raise ValueError
        source = _read(directory / "TEMPEST_HUB_SOURCE", 128)
        hub = _read(directory / "TEMPEST_HUB_SERIAL", 128)
        if not _atom(source) or ipaddress.ip_address(source).version != 4 or not _atom(hub):
            raise ValueError
        devices: Any = json.loads(
            _read(directory / "TEMPEST_DEVICE_SERIALS", 8192),
            parse_constant=_invalid_constant,
        )
        if (
            not isinstance(devices, list)
            or not 1 <= len(devices) <= 32
            or any(not _atom(value) for value in devices)
            or len(set(devices)) != len(devices)
        ):
            raise ValueError
        for key, low, high in (
            ("hub_source_port", 1, 65535),
            ("capacity_bytes", 1 << 20, 64 << 30),
            ("future_seconds", 0, 86400),
            ("metrics_port", 1024, 65535),
        ):
            value = data.get(key)
            if (
                key in data
                and not (key == "hub_source_port" and value is None)
                and (type(value) is not int or not low <= value <= high)
            ):
                raise ValueError
        spool = Path(data.get("spool_directory", "/var/lib/weather/telemetry"))
        if not spool.is_absolute():
            raise ValueError
        return ReceiverConfig(
            data["interface_name"],
            source,
            hub,
            frozenset(devices),
            data.get("hub_source_port"),
            spool,
            data.get("capacity_bytes", 1 << 30),
            data.get("future_seconds", 120),
            data.get("metrics_port", 9000),
        )
    except OSError, ValueError, KeyError, TypeError:
        raise ValueError("Invalid mounted private receiver configuration") from None


def _read(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError
    return raw.decode("utf-8")


def _atom(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.isascii()
        and all(32 < ord(character) < 127 for character in value)
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise ValueError
