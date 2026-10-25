# SPDX-License-Identifier: MIT
"""`weather pipeline run --camera <id>`, `weather pipeline bench` (in-cluster Job only) and
`weather pipeline abandon <segment> --actor <name>` (a stuck segment, #19)."""

import argparse
import asyncio
import json
import logging
import re
from uuid import UUID

from .recipe import CAMERAS


def _abandon(parser: argparse.ArgumentParser, segment: str, actor: str) -> None:
    from weather.db.connection import (
        DatabaseConfigurationError,
        DatabaseUnavailable,
        create_database_engine,
        wait_for_database,
    )

    from .schedule import abandon

    try:
        segment_id = UUID(segment)
    except ValueError:
        parser.exit(2, "Invalid segment id\n")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}", actor):
        parser.exit(2, "Invalid actor\n")

    async def run() -> bool:
        engine = create_database_engine()
        try:
            await wait_for_database(engine)
            return await abandon(engine, segment_id, actor)
        finally:
            await engine.dispose()

    try:
        changed = asyncio.run(run())
    except DatabaseConfigurationError, DatabaseUnavailable:
        parser.exit(2, "Database unavailable or not configured\n")
    logging.getLogger("weather.pipeline").warning(
        "Segment abandoned by operator (%s): %s", actor, "recorded" if changed else "no change"
    )
    print(json.dumps({"segment": str(segment_id), "abandoned": changed, "actor": actor}))


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="weather pipeline", description="Timelapse renderer")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Stream, finalize and publish one camera's segments")
    run.add_argument("--camera", required=True, choices=CAMERAS)
    commands.add_parser("bench", help="Synthetic throughput check (in-cluster Job only)")
    abandon = commands.add_parser(
        "abandon",
        help="Finalize a stuck segment as unavailable/abandoned (Jason's instruction only)",
    )
    abandon.add_argument("segment", help="segment id")
    abandon.add_argument("--actor", required=True, help="Who instructed it, for the log")
    args, rest = parser.parse_known_args(argv)
    if args.command == "abandon":
        if rest:
            parser.exit(2, "abandon takes only a segment id and --actor\n")
        _abandon(parser, args.segment, args.actor)
        return
    if args.command == "run":
        if rest:
            parser.exit(2, "run takes only --camera; everything else is mounted configuration\n")
        from .worker import run as run_worker

        run_worker(args.camera)
    else:
        from .bench import main as bench

        bench(rest)
