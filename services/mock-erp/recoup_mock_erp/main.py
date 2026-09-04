from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.http import create_app
from recoup_common.logging import get_logger

from recoup_mock_erp import repo
from recoup_mock_erp.routes import router
from recoup_mock_erp.settings import get_settings

log = get_logger(__name__)
settings = get_settings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    app.state.db = db
    if settings.auto_seed:
        async with db.session() as session:
            if not await repo.is_seeded(session):
                ds = await repo.seed(
                    session,
                    seed=settings.seed,
                    customers=settings.seed_customers,
                    invoices=settings.seed_invoices,
                    as_of=None,
                )
                log.info("mockerp.autoseed", invoices=len(ds.invoices), mix=ds.scenario_counts())
    try:
        yield
    finally:
        await db.dispose()


app = create_app(settings, title="Recoup Mock ERP", lifespan=lifespan)
app.include_router(router)
