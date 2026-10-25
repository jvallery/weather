# SPDX-License-Identifier: MIT
"""`weather media withdraw|reconcile|expire|gc|purge`: operator commands, worker identity.

expire, gc and purge are #19 (`weather.scheduler.lifecycle`); gc and purge need the bucket key.
"""

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


async def _lifecycle(args: argparse.Namespace) -> dict[str, object]:
    from weather.scheduler import lifecycle

    from .objects import ObjectStore

    settings = lifecycle.LifecycleSettings.from_environment()
    engine = create_database_engine()
    store = None if args.command == "expire" else ObjectStore.from_environment(args.bucket)
    try:
        await wait_for_database(engine)
        if args.command == "expire":
            return await lifecycle.expire(engine, settings, dry_run=args.dry_run)
        assert store is not None
        if args.command == "gc":
            return dict(await lifecycle.gc(engine, store, settings, dry_run=args.dry_run))
        return dict(
            await lifecycle.purge(engine, store, args.asset, reason=args.reason, actor=args.actor)
        )
    finally:
        if store is not None:
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
    expire = commands.add_parser(
        "expire",
        help="List assets past retention; tombstone only under the enabled retention flags",
    )
    expire.add_argument("--dry-run", action="store_true")
    gc = commands.add_parser(
        "gc", help="Delete orphan/tombstoned objects past grace (WEATHER_GC_ENABLED only)"
    )
    gc.add_argument("--bucket", required=True, choices=(MEDIA_BUCKET, ORIGINALS_BUCKET))
    gc.add_argument("--dry-run", action="store_true")
    purge = commands.add_parser(
        "purge", help="Hard purge one withdrawn asset's bytes now (Jason's instruction only)"
    )
    purge.add_argument("asset", type=UUID, help="asset_id")
    purge.add_argument("--bucket", required=True, choices=(MEDIA_BUCKET, ORIGINALS_BUCKET))
    purge.add_argument("--reason", required=True, help="Audit reason, e.g. third_party_privacy")
    purge.add_argument("--actor", required=True, help="Who instructed it, e.g. jason")
    args = parser.parse_args(argv)
    if args.command in {"expire", "gc", "purge"}:
        from weather.scheduler.lifecycle import GcRefused, PurgeRefused

        try:
            print(json.dumps(asyncio.run(_lifecycle(args)), sort_keys=True))
        except GcRefused as error:
            parser.exit(3, f"GC refused: {error.reason}\n")
        except PurgeRefused as error:
            parser.exit(3, f"Purge refused: {error}\n")
        except DatabaseConfigurationError, DatabaseUnavailable:
            parser.exit(2, "Database unavailable or not configured\n")
        except StoreError as error:
            parser.exit(2, f"{error}\n")
        except ValueError:
            parser.exit(2, "Invalid lifecycle configuration\n")
        return
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
