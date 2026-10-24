# SPDX-License-Identifier: MIT
"""`weather media withdraw|reconcile`: operator commands run with the worker identity."""

import argparse
import asyncio
import json
from uuid import UUID

from weather.db.connection import (
    DatabaseConfigurationError,
    DatabaseUnavailable,
    create_database_engine,
    wait_for_database,
)

from . import catalog
from .model import MEDIA_BUCKET, ORIGINALS_BUCKET
from .objects import StoreError


async def _withdraw(target: UUID, reason: str, actor: str) -> dict[str, object]:
    engine = create_database_engine()
    try:
        await wait_for_database(engine)
        async with engine.begin() as connection:
            return await catalog.withdraw(connection, target, reason=reason, actor=actor)
    finally:
        await engine.dispose()


async def _reconcile() -> dict[str, int]:
    from .objects import ObjectStore
    from .reconcile import reconcile

    engine = create_database_engine()
    stores = {
        bucket: ObjectStore.from_environment(bucket) for bucket in (MEDIA_BUCKET, ORIGINALS_BUCKET)
    }
    try:
        await wait_for_database(engine)
        return await reconcile(engine, stores)
    finally:
        for store in stores.values():
            await store.aclose()
        await engine.dispose()


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="weather media", description="Publication operations")
    commands = parser.add_subparsers(dest="command", required=True)
    withdraw = commands.add_parser(
        "withdraw",
        help="Tombstone a video (and its assets) or one asset; the bytes remain",
    )
    withdraw.add_argument("target", type=UUID, help="video_id or asset_id")
    withdraw.add_argument("--reason", required=True, help="Audit reason, 1-500 characters")
    withdraw.add_argument(
        "--actor",
        default="operator",
        help="Audit actor name (default: operator)",
    )
    commands.add_parser(
        "reconcile",
        help="Commit or orphan stale intents, restore lost sidecars, abort stale multipart",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "withdraw":
            result: dict[str, object] = asyncio.run(_withdraw(args.target, args.reason, args.actor))
        else:
            result = dict(asyncio.run(_reconcile()))
    except catalog.NotFound:
        parser.exit(1, "No video or asset with that ID\n")
    except DatabaseConfigurationError, DatabaseUnavailable:
        parser.exit(2, "Database unavailable or not configured\n")
    except ValueError:
        parser.exit(2, "Invalid reason or actor\n")
    except StoreError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, sort_keys=True))
