from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events import build_publisher
from recoup_common.events.outbox import OutboxRelay
from recoup_common.http import create_app
from recoup_common.logging import get_logger
from sqlalchemy import select

from recoup_iam import service
from recoup_iam.models import Outbox, Tenant
from recoup_iam.routes import router
from recoup_iam.schemas import TenantCreate
from recoup_iam.settings import get_settings

log = get_logger(__name__)
settings = get_settings()


async def _bootstrap(db: Database) -> None:
    """Create a demo tenant + admin on first run in dev so the console has something to log into."""
    if not settings.is_dev or not settings.bootstrap_tenant_slug:
        return
    async with db.session() as session:
        if await session.scalar(select(Tenant).limit(1)):
            return
        await service.create_tenant(
            session,
            TenantCreate(
                slug=settings.bootstrap_tenant_slug,
                name=settings.bootstrap_tenant_name,
                admin_email=settings.bootstrap_admin_email,
                admin_password=settings.bootstrap_password,
                admin_full_name="Platform Admin",
            ),
        )
        log.info("iam.bootstrap", tenant=settings.bootstrap_tenant_slug)


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    publisher = build_publisher(settings)
    await publisher.start()
    relay = OutboxRelay(db, publisher, Outbox, poll_interval=settings.outbox_poll_interval_seconds)
    app.state.db = db
    app.state.publisher = publisher
    await _bootstrap(db)
    relay.start()
    try:
        yield
    finally:
        await relay.stop()
        await publisher.stop()
        await db.dispose()


app = create_app(settings, title="Recoup IAM", lifespan=lifespan)
app.include_router(router)
