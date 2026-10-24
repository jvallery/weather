# SPDX-License-Identifier: MIT
"""Role entrypoint. Roles are added as their implementation issues land."""

import argparse
import sys

from weather.config import ConfigurationError, load_settings


def main() -> None:
    if sys.argv[1:2] == ["capture"]:
        from weather.capture.cli import main as capture_main

        capture_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="weather", description="Weather archive services")
    parser.epilog = "Snapshot acquisition: weather capture collect --camera <id>"
    parser.add_argument("role", choices=["api", "scheduler"], nargs="?")
    args = parser.parse_args()
    if args.role == "api":
        try:
            settings = load_settings()
        except ConfigurationError as exc:
            parser.exit(2, f"{exc}\n")

        import uvicorn
        from prometheus_client import start_http_server

        start_http_server(settings.metrics_port)
        uvicorn.run("weather.api:app", host="0.0.0.0", port=settings.port)
    elif args.role == "scheduler":
        from weather.scheduler.runner import main as run_scheduler

        run_scheduler()
    else:
        parser.print_help()
