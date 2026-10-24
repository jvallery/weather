# SPDX-License-Identifier: MIT
"""Role entrypoint. Roles are added as their implementation issues land."""

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="weather", description="Weather archive services")
    parser.add_argument("role", choices=["api"], nargs="?")
    args = parser.parse_args()
    if args.role == "api":
        import uvicorn
        from prometheus_client import start_http_server

        start_http_server(int(os.environ.get("METRICS_PORT", "9000")))
        uvicorn.run("weather.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    else:
        parser.print_help()
