# SPDX-License-Identifier: MIT
"""Wait boundedly for the database and CNPG roles, then exec Alembic as migrator."""

import asyncio
import os
import sys
import time

from sqlalchemy import text

from weather.db.connection import attempt_seconds, create_database_engine, wait_for_database

ROLES = ("weather_migrator", "weather_ingest", "weather_worker", "weather_api", "weather_editor")
ROLE_SECONDS = 120


async def wait_for_roles() -> None:
    engine = create_database_engine()
    try:
        await wait_for_database(engine)
        deadline = time.monotonic() + ROLE_SECONDS
        while time.monotonic() < deadline:
            try:
                async with (
                    asyncio.timeout(attempt_seconds(deadline)),
                    engine.connect() as connection,
                ):
                    count = (
                        await connection.execute(
                            text("SELECT count(*) FROM pg_roles WHERE rolname = ANY(:roles)"),
                            {"roles": list(ROLES)},
                        )
                    ).scalar_one()
                if count == len(ROLES):
                    return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise TimeoutError("Database roles not ready within startup deadline")
    finally:
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(wait_for_roles())
    except Exception:
        print("Migration prerequisites not ready", file=sys.stderr)
        raise SystemExit(2) from None
    os.execv(sys.executable, [sys.executable, "-m", "alembic", "upgrade", "head"])


if __name__ == "__main__":
    main()
