"""Shared Alembic ``env.py`` logic so each service only declares its metadata + schema."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import MetaData, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config


def run_migrations(target_metadata: MetaData, schema: str) -> None:
    config = context.config
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    config.set_main_option("sqlalchemy.url", url)

    def include_object(
        obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
    ) -> bool:
        if type_ == "table":
            return bool(obj.schema == schema)
        return True

    def do_run(connection: Connection) -> None:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=schema,
            include_schemas=True,
            include_object=include_object,  # type: ignore[arg-type]
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async() -> None:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with connectable.connect() as connection:
            await connection.run_sync(do_run)
        await connectable.dispose()

    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            version_table_schema=schema,
            include_schemas=True,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        asyncio.run(run_async())
