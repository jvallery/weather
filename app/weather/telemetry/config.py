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
        data: Any = json.loads((directory / "receiver.json").read_text())
        allowed = {
            "interface_name",
            "hub_source_ip",
            "hub_identity",
            "device_identities",
            "hub_source_port",
            "spool_directory",
            "capacity_bytes",
            "future_seconds",
            "metrics_port",
        }
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError
        for key in ("interface_name", "hub_source_ip", "hub_identity"):
            if not isinstance(data[key], str) or not data[key] or len(data[key]) > 128:
                raise ValueError
        if ipaddress.ip_address(data["hub_source_ip"]).version != 4:
            raise ValueError
        devices = data["device_identities"]
        if (
            not isinstance(devices, list)
            or not 1 <= len(devices) <= 32
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in devices)
        ):
            raise ValueError
        for key, low, high in (
            ("hub_source_port", 1, 65535),
            ("capacity_bytes", 1 << 20, 64 << 30),
            ("future_seconds", 0, 86400),
            ("metrics_port", 1024, 65535),
        ):
            value = data.get(key)
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise ValueError
        spool = Path(data.get("spool_directory", "/var/lib/weather/telemetry"))
        if not spool.is_absolute():
            raise ValueError
        return ReceiverConfig(
            data["interface_name"],
            data["hub_source_ip"],
            data["hub_identity"],
            frozenset(devices),
            data.get("hub_source_port"),
            spool,
            data.get("capacity_bytes", 1 << 30),
            data.get("future_seconds", 120),
            data.get("metrics_port", 9000),
        )
    except OSError, ValueError, KeyError, TypeError:
        raise ValueError("Invalid mounted private receiver configuration") from None
