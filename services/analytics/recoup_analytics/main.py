from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from recoup_common.db import Database
from recoup_common.events.consumer import KafkaConsumerLoop
from recoup_common.events.topics import ALL_TOPICS
from recoup_common.http import create_app
from recoup_common.logging import get_logger

from recoup_analytics.projector import Projector
from recoup_analytics.routes import internal, router
from recoup_analytics.settings import get_settings

log = get_logger(__name__)
settings = get_settings()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db = Database(settings.database_url, schema=settings.db_schema, pool_size=settings.db_pool_size)
    projector = Projector(db, settings)
    app.state.db, app.state.projector = db, projector
    consumer = None
    if settings.consumer_enabled and settings.kafka_bootstrap_servers:
        # One durable group reading every topic from the beginning: the read model is rebuilt
        # from history on a fresh database and kept current afterwards.
        consumer = KafkaConsumerLoop(
            settings.kafka_bootstrap_servers,
            group_id=settings.consumer_group,
            topics=ALL_TOPICS,
            handler=projector.handle,
            client_id="analytics",
        )
        consumer.start()
        log.info("analytics.consuming", topics=ALL_TOPICS, group=settings.consumer_group)
    else:
        log.warning("analytics: no KAFKA_BOOTSTRAP_SERVERS; the read model will stay empty")
    try:
        yield
    finally:
        if consumer:
            await consumer.stop()
        await db.dispose()


app = create_app(settings, title="Recoup Analytics", lifespan=lifespan)
app.include_router(router)
app.include_router(internal)


@app.get("/stats", tags=["ops"])
async def stats() -> dict[str, Any]:
    return {"events_projected": app.state.projector.seen}
