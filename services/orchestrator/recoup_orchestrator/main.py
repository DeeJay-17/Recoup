from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events import build_publisher
from recoup_common.events.consumer import KafkaConsumerLoop
from recoup_common.events.outbox import OutboxRelay
from recoup_common.events.topics import CASE, COMM
from recoup_common.http import create_app
from recoup_common.logging import get_logger
from recoup_llm import LLMSettings, ModelRouter, build_router

from recoup_orchestrator import prompt_store
from recoup_orchestrator.agents import heuristics  # noqa: F401  (registers heuristic policies)
from recoup_orchestrator.clients import CaseClient, KnowledgeClient, ToolGatewayClient
from recoup_orchestrator.consumer import EventBridge
from recoup_orchestrator.models import Outbox
from recoup_orchestrator.routes import internal, router
from recoup_orchestrator.runs import RunManager
from recoup_orchestrator.settings import get_settings
from recoup_orchestrator.workflows.activities import CaseActivities, Deps
from recoup_orchestrator.workflows.worker import WorkerRunner, connect

log = get_logger(__name__)
settings = get_settings()
llm_settings = LLMSettings()


def router_factory(overrides: dict[str, Any] | None) -> ModelRouter:
    return build_router(llm_settings, overrides=overrides)


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    publisher = build_publisher(settings)
    await publisher.start()
    relay = OutboxRelay(db, publisher, Outbox, poll_interval=settings.outbox_poll_interval_seconds)
    async with db.session() as session:
        n = await prompt_store.seed_from_files(session)
        if n:
            log.info("prompts.seeded", count=n)
    gateway, cases = ToolGatewayClient(settings.tool_gateway_url), CaseClient(settings.case_url)
    knowledge = KnowledgeClient(settings.knowledge_url)
    client = await connect(settings)
    runs = RunManager(client, settings)
    activities = CaseActivities(Deps(settings, db, router_factory, gateway, cases, knowledge))
    worker = WorkerRunner(client, settings, activities) if settings.worker_enabled else None
    consumer = None
    if settings.consumer_enabled and settings.kafka_bootstrap_servers:
        bridge = EventBridge(runs, settings)
        consumer = KafkaConsumerLoop(
            settings.kafka_bootstrap_servers,
            group_id="orchestrator",
            topics=[CASE, COMM],
            handler=bridge.handle,
            client_id="orchestrator",
        )
    app.state.db, app.state.runs, app.state.router_factory = db, runs, router_factory
    app.state.llm = router_factory(None).describe()
    log.info(
        "orchestrator.models",
        **{k: f"{v['provider']}/{v['model']}" for k, v in app.state.llm.items()},
    )
    relay.start()
    if worker:
        worker.start()
    if consumer:
        consumer.start()
    try:
        yield
    finally:
        if consumer:
            await consumer.stop()
        if worker:
            await worker.stop()
        await relay.stop()
        await gateway.aclose()
        await cases.aclose()
        await knowledge.aclose()
        await publisher.stop()
        await db.dispose()


app = create_app(settings, title="Recoup Agent Orchestrator", lifespan=lifespan)
app.include_router(router)
app.include_router(internal)


@app.get("/llm", tags=["ops"])
async def llm_info() -> dict[str, Any]:
    return {"provider_default": llm_settings.provider, "tiers": app.state.llm}
