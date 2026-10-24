# SPDX-License-Identifier: MIT
"""Migrations use private connection files; SQL and credentials are never logged."""

import asyncio
from collections.abc import Mapping
from typing import Any

from alembic import context
from sqlalchemy import Connection
from weather.db.connection import create_database_engine, wait_for_database
from weather.db.models import metadata

# A migration waits briefly for locks and fails rather than queueing behind live traffic.
TIMEOUTS = ("SET LOCAL lock_timeout = '10s'", "SET LOCAL statement_timeout = '60s'")


def include_name(name: str | None, type_: str, parent_names: Mapping[str, str | None]) -> bool:
    return type_ != "schema" or name == "weather"


def run() -> None:
    with context.begin_transaction():
        for statement in TIMEOUTS:
            context.execute(statement)
        context.run_migrations()


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    run()


async def online() -> None:
    engine = create_database_engine()
    try:
        await wait_for_database(engine)
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


def offline() -> None:
    config: dict[str, Any] = {
        "dialect_name": "postgresql",
        "target_metadata": metadata,
        "literal_binds": True,
        "include_schemas": True,
    }
    context.configure(**config)
    run()


if context.is_offline_mode():
    offline()
else:
    asyncio.run(online())
