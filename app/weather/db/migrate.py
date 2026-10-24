# SPDX-License-Identifier: MIT
"""Wait boundedly for CNPG role reconciliation, then exec Alembic as migrator."""

import asyncio
import os
import sys
import time

from sqlalchemy import text

from weather.db.connection import create_database_engine


async def wait_for_roles() -> None:
    engine = create_database_engine()
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            try:
                async with engine.connect() as connection:
                    count = (
                        await connection.execute(
                            text("""
                        SELECT count(*) FROM pg_roles WHERE rolname IN
                        ('weather_migrator','weather_ingest','weather_worker',
                         'weather_api','weather_editor')
                    """)
                        )
                    ).scalar_one()
                    if count == 5:
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
