from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events import build_publisher
from recoup_common.events.consumer import KafkaConsumerLoop
from recoup_common.events.outbox import OutboxRelay
from recoup_common.events.topics import CASE, COMM
from recoup_common.http import create_app
from recoup_common.logging import get_logger
from recoup_erp import MockERPAdapter
from recoup_llm import EmbedSettings, LLMSettings, build_embedder, build_router

from recoup_knowledge import memory  # noqa: F401  (registers the heuristic memory writer)
from recoup_knowledge.clients import CaseClient, CommClient
from recoup_knowledge.models import Outbox
from recoup_knowledge.routes import internal, router
from recoup_knowledge.settings import get_settings
from recoup_knowledge.writer import Writer

log = get_logger(__name__)
settings = get_settings()
llm_settings = LLMSettings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    publisher = build_publisher(settings)
    await publisher.start()
    relay = OutboxRelay(db, publisher, Outbox, poll_interval=settings.outbox_poll_interval_seconds)
    embedder = build_embedder(
        EmbedSettings(), llm_provider=llm_settings.provider, llm_api_key=llm_settings.api_key
    )
    router_ = build_router(llm_settings)
    cases, comm, erp = (
        CaseClient(settings.case_url),
        CommClient(settings.comm_url),
        MockERPAdapter(settings.mock_erp_url),
    )
    writer = Writer(db, settings, embedder, router_, cases, comm, erp)
    app.state.db, app.state.embedder, app.state.writer = db, embedder, writer
    log.info(
        "knowledge.ready",
        embedder=f"{embedder.provider}/{embedder.model}",
        dim=embedder.dim,
        memory_llm=router_.describe()["fast"],
    )
    consumer = None
    if settings.consumer_enabled and settings.kafka_bootstrap_servers:
        consumer = KafkaConsumerLoop(
            settings.kafka_bootstrap_servers,
            group_id="knowledge",
            topics=[CASE, COMM],
            handler=writer.handle_event,
            client_id="knowledge",
        )
        consumer.start()
    relay.start()
    try:
        yield
    finally:
        if consumer:
            await consumer.stop()
        await relay.stop()
        await cases.aclose()
        await comm.aclose()
        await erp.aclose()
        await publisher.stop()
        await db.dispose()


app = create_app(settings, title="Recoup Knowledge Service", lifespan=lifespan)
app.include_router(router)
app.include_router(internal)
