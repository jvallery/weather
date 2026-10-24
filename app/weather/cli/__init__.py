# SPDX-License-Identifier: MIT
"""Role entrypoint. Roles are added as their implementation issues land."""

import argparse

from weather.config import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="weather", description="Weather archive services")
    parser.add_argument("role", choices=["api"], nargs="?")
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
    else:
        parser.print_help()
