from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events import build_publisher
from recoup_common.events.outbox import OutboxRelay
from recoup_common.http import create_app
from recoup_erp import MockERPAdapter

from recoup_tool_gateway import tools  # noqa: F401  (registers tools)
from recoup_tool_gateway.clients import CaseClient, CommClient, PolicyClient
from recoup_tool_gateway.models import Outbox
from recoup_tool_gateway.ratelimit import RateLimiter
from recoup_tool_gateway.registry import registry
from recoup_tool_gateway.routes import router
from recoup_tool_gateway.service import ToolService
from recoup_tool_gateway.settings import get_settings

settings = get_settings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    publisher = build_publisher(settings)
    await publisher.start()
    relay = OutboxRelay(db, publisher, Outbox, poll_interval=settings.outbox_poll_interval_seconds)
    limiter = RateLimiter(settings.redis_url, settings.rate_limit_per_minute)
    app.state.db = db
    app.state.erp = MockERPAdapter(settings.mock_erp_url)
    app.state.cases = CaseClient(settings.case_url)
    app.state.policy = PolicyClient(settings.policy_url)
    app.state.comm = CommClient(settings.comm_url)
    app.state.service = ToolService(
        db,
        registry,
        limiter,
        timeout=settings.tool_timeout_seconds,
        result_max_chars=settings.result_max_chars,
    )
    relay.start()
    try:
        yield
    finally:
        await relay.stop()
        await app.state.erp.aclose()
        await app.state.cases.aclose()
        await app.state.policy.aclose()
        await app.state.comm.aclose()
        await limiter.aclose()
        await publisher.stop()
        await db.dispose()


app = create_app(settings, title="Recoup Tool Gateway", lifespan=lifespan)
app.include_router(router)
