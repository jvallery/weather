# SPDX-License-Identifier: MIT
"""Capture-only arguments contain camera IDs and mount paths, never connection details."""

import argparse
from pathlib import Path

from .collector import collect
from .config import CAMERAS


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="weather capture")
    sub = parser.add_subparsers(dest="action", required=True)
    command = sub.add_parser("collect")
    command.add_argument("--camera", required=True, choices=sorted(CAMERAS))
    command.add_argument("--config-dir", type=Path, default=Path("/etc/weather/capture"))
    command.add_argument("--secret-dir", type=Path, default=Path("/run/secrets/camera"))
    args = parser.parse_args(argv)
    try:
        collect(args.camera, args.config_dir, args.secret_dir)
    except ValueError:
        parser.exit(2, "Invalid mounted capture configuration\n")
