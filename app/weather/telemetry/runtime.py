# SPDX-License-Identifier: MIT
"""Commissioned Linux role only; receive and CNPG drain have separate execution paths."""

import asyncio
import os
import signal
import socket
import sys
import threading
from pathlib import Path

from .config import load_config
from .drain import Drain
from .metrics import Metrics
from .receiver import AdmissionConfig, Receiver
from .spool import Spool


async def run() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Receiver runtime requires the commissioned Linux pod")
    from weather.db.connection import create_database_engine  # type: ignore[import-not-found]

    config = load_config(
        Path(os.environ.get("TELEMETRY_CONFIG_DIRECTORY", "/etc/weather/telemetry"))
    )
    index = socket.if_nametoindex(config.interface_name)
    spool = Spool(config.spool_directory, config.capacity_bytes)
    engine = create_database_engine()
    stop, failed = threading.Event(), threading.Event()
    drain_stop = asyncio.Event()
    receiver = Receiver(
        AdmissionConfig(
            index,
            config.hub_source_ip,
            config.hub_identity,
            config.device_identities,
            config.hub_source_port,
        ),
        spool,
    )
    drain = Drain(spool, engine, config.future_seconds)

    def receive() -> None:
        try:
            receiver.run(stop)
        except Exception:
            failed.set()

    def halt() -> None:
        stop.set()
        drain_stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, halt)
    thread = threading.Thread(target=receive, name="tempest-receiver", daemon=True)
    thread.start()
    server = Metrics(receiver, drain).serve(config.metrics_port, thread)
    task = asyncio.create_task(drain.run(drain_stop))
    try:
        while not drain_stop.is_set():
            if failed.is_set() or not thread.is_alive():
                raise RuntimeError("Telemetry receive loop unavailable")
            try:
                await asyncio.wait_for(drain_stop.wait(), 1)
            except TimeoutError:
                pass
    finally:
        halt()
        await task
        await asyncio.to_thread(thread.join, 2)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await engine.dispose()
        spool.close()


def main() -> None:
    try:
        asyncio.run(run())
    except Exception:
        # Error details may contain private config or DB bind parameters.
        raise SystemExit(
            "Telemetry receiver unavailable; inspect bounded metrics and runbook"
        ) from None
