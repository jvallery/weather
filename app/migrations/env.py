# SPDX-License-Identifier: MIT
"""Migrations use private connection files; SQL and credentials are never logged."""

import asyncio
from collections.abc import Mapping
from typing import Any

from alembic import context
from sqlalchemy import Connection
from weather.db.connection import create_database_engine
from weather.db.models import metadata


def include_name(
    name: str | None, type_: str, parent_names: Mapping[str, str | None]
) -> bool:
    return type_ != "schema" or name == "weather"


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine = create_database_engine()
    try:
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
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    offline()
else:
    asyncio.run(online())
