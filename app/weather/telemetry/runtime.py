# SPDX-License-Identifier: MIT
"""Commissioned Linux role only; receive and CNPG drain have separate execution paths."""

import argparse
import asyncio
import os
import signal
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

from .config import ReceiverConfig, load_config
from .drain import Drain, reproject, seed
from .metrics import Metrics
from .receiver import AdmissionConfig, Receiver
from .spool import Spool


async def run() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Receiver runtime requires the commissioned Linux pod")
    from weather.db.connection import create_database_engine

    config = _config()
    index = socket.if_nametoindex(config.interface_name)
    spool = Spool(config.spool_directory, config.capacity_bytes)
    engine = create_database_engine()
    stop, failed = threading.Event(), threading.Event()
    drain_stop = asyncio.Event()
    receiver = Receiver(
        AdmissionConfig(
            index,
            config.hub_identity,
            config.device_identities,
            config.hub_source_ip,
            config.hub_source_port,
        ),
        spool,
    )
    # No first-connect wait: the drain retries every second while the spool holds receipts.
    drain = Drain(
        spool, engine, config.future_seconds, config.hub_identity, config.device_identities
    )

    def receive() -> None:
        try:
            receiver.run(stop, config.interface_name)
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


def _config() -> ReceiverConfig:
    return load_config(
        Path(os.environ.get("TELEMETRY_CONFIG_DIRECTORY", "/etc/weather/telemetry"))
    )


async def run_reproject(since: datetime) -> tuple[int, int, int]:
    from weather.db.connection import create_database_engine, wait_for_database

    config = _config()
    engine = create_database_engine()
    try:
        await wait_for_database(engine)
        async with engine.begin() as db:
            await seed(db, config.hub_identity, config.device_identities)
        receipts, rows, counts = await reproject(engine, since, config.future_seconds)
        return receipts, rows, counts["unprojected"] + counts["reproject_errors"]
    finally:
        await engine.dispose()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def reproject_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="weather tempest-receiver reproject",
        description="Rebuild typed telemetry rows from retained raw receipts",
    )
    parser.add_argument(
        "--since", type=_instant, required=True, help="ISO-8601 receive time with offset"
    )
    args = parser.parse_args(argv)
    try:
        receipts, rows, skipped = asyncio.run(run_reproject(args.since))
    except Exception:
        raise SystemExit("Telemetry reproject unavailable; inspect runbook") from None
    print(f"reprojected receipts={receipts} new_rows={rows} unprojected_rows={skipped}")


def main() -> None:
    try:
        asyncio.run(run())
    except Exception:
        # Error details may contain private config or DB bind parameters.
        raise SystemExit(
            "Telemetry receiver unavailable; inspect bounded metrics and runbook"
        ) from None
