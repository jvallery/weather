# SPDX-License-Identifier: MIT
"""`weather pipeline run --camera <id>` and `weather pipeline bench` (in-cluster Job only)."""

import argparse

from .recipe import CAMERAS


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="weather pipeline", description="Timelapse renderer")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Stream, finalize and publish one camera's segments")
    run.add_argument("--camera", required=True, choices=CAMERAS)
    commands.add_parser("bench", help="Synthetic throughput check (in-cluster Job only)")
    args, rest = parser.parse_known_args(argv)
    if args.command == "run":
        if rest:
            parser.exit(2, "run takes only --camera; everything else is mounted configuration\n")
        from .worker import run as run_worker

        run_worker(args.camera)
    else:
        from .bench import main as bench

        bench(rest)
