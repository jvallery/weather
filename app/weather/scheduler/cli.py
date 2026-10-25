# SPDX-License-Identifier: MIT
"""`weather staging cleanup` and `weather reconcile-objects`: operator one-shots (#19).

Both print one JSON line of counts. Run cleanup only inside a pipeline pod (it needs the
staging mount, UID 1025 / GID 100, and the worker database role); reconcile in a pod that
holds the bucket's key.
"""

import argparse
import asyncio
import json
from datetime import timedelta

from weather.db.connection import (
    DatabaseConfigurationError,
    DatabaseUnavailable,
    create_database_engine,
    wait_for_database,
)
from weather.publish.objects import StoreError

CAMERAS = ("skycam_east", "skycam_west", "frontyard_1")
BUCKETS = ("weather-media", "weather-originals")


async def _cleanup(camera: str, dry_run: bool) -> dict[str, int]:
    from weather.pipeline.worker import PipelineConfig

    from .cleanup import Cleanup, CleanupSettings, SqlCatalog

    config = PipelineConfig.from_environment(camera)
    engine = create_database_engine()
    try:
        await wait_for_database(engine)
        cleanup = Cleanup(
            camera,
            config.staging,
            SqlCatalog(engine),
            CleanupSettings.from_environment(),
            lag=timedelta(seconds=float(config.expected.max_lag)),
            expected=(
                config.expected.cadence_ms,
                config.expected.phase_ms,
                config.expected.width,
                config.expected.height,
            ),
        )
        return await cleanup.run(dry_run=dry_run)
    finally:
        await engine.dispose()


async def _reconcile(bucket: str, rate: float, ratio: float) -> dict[str, int]:
    from weather.publish.objects import ObjectStore

    from .lifecycle import reconcile_objects

    engine = create_database_engine()
    store = ObjectStore.from_environment(bucket)
    try:
        await wait_for_database(engine)
        return await reconcile_objects(
            engine, store, rate_per_second=rate, max_missing_ratio=ratio
        )
    finally:
        await store.aclose()
        await engine.dispose()


def staging_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="weather staging", description="Staging cleanup")
    commands = parser.add_subparsers(dest="command", required=True)
    cleanup = commands.add_parser(
        "cleanup",
        help="One cleanup pass; deletes only with WEATHER_CLEANUP_ENABLED=true",
    )
    cleanup.add_argument("--camera", required=True, choices=CAMERAS)
    cleanup.add_argument("--dry-run", action="store_true", help="Evaluate and report only")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_cleanup(args.camera, args.dry_run))
    except DatabaseConfigurationError, DatabaseUnavailable:
        parser.exit(2, "Database unavailable or not configured\n")
    except ValueError:
        parser.exit(2, "Invalid pipeline or cleanup configuration\n")
    except OSError as error:
        parser.exit(2, f"Staging unavailable ({type(error).__name__})\n")
    print(json.dumps(result, sort_keys=True))


def reconcile_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="weather reconcile-objects",
        description="HEAD every referenced object; mark missing ones unavailable",
    )
    parser.add_argument("--bucket", required=True, choices=BUCKETS)
    parser.add_argument("--rate", type=float, default=20.0, help="HEAD requests per second")
    parser.add_argument(
        "--max-missing-ratio",
        type=float,
        default=0.05,
        help="Mark nothing if more than this share of objects is missing (default 0.05)",
    )
    args = parser.parse_args(argv)
    from .lifecycle import ReconcileAborted

    try:
        result = asyncio.run(_reconcile(args.bucket, args.rate, args.max_missing_ratio))
    except ReconcileAborted as error:
        print(json.dumps(error.counts, sort_keys=True))
        parser.exit(3, f"Reconcile aborted, nothing marked: {error.reason}\n")
    except DatabaseConfigurationError, DatabaseUnavailable:
        parser.exit(2, "Database unavailable or not configured\n")
    except StoreError as error:
        parser.exit(2, f"{error}\n")
    except ValueError:
        parser.exit(2, "Invalid rate\n")
    print(json.dumps(result, sort_keys=True))
