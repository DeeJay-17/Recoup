"""Integration fixtures. Set TEST_DATABASE_URL to run DB-backed tests; otherwise they skip."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from recoup_case.models import Base
from recoup_common.db import Database
from sqlalchemy.ext.asyncio import AsyncSession

TEST_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    database = Database(TEST_URL, schema="cases_test")
    from sqlalchemy import text

    async with database.engine.begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "cases_test"'))
        # point the metadata at the test schema for this run
        for t in Base.metadata.tables.values():
            t.schema = "cases_test"
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def session(db: Database) -> AsyncIterator[AsyncSession]:
    async with db.session() as s:
        yield s


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()
