from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events import build_publisher
from recoup_common.events.outbox import OutboxRelay
from recoup_common.http import create_app
from recoup_erp import MockERPAdapter

from recoup_case.ingestion import IngestionJob
from recoup_case.models import Outbox
from recoup_case.routes import internal, router
from recoup_case.settings import get_settings

settings = get_settings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    publisher = build_publisher(settings)
    await publisher.start()
    relay = OutboxRelay(db, publisher, Outbox, poll_interval=settings.outbox_poll_interval_seconds)
    erp = MockERPAdapter(settings.mock_erp_url)
    ingestion = IngestionJob(db, erp, settings)
    app.state.db = db
    app.state.publisher = publisher
    app.state.erp = erp
    app.state.ingestion = ingestion
    relay.start()
    if settings.ingest_enabled:
        ingestion.start()
    try:
        yield
    finally:
        await ingestion.stop()
        await relay.stop()
        await erp.aclose()
        await publisher.stop()
        await db.dispose()


app = create_app(settings, title="Recoup Case Service", lifespan=lifespan)
app.include_router(router)
app.include_router(internal)
