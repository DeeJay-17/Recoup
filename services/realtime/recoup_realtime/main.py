from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from recoup_common.auth import decode_token
from recoup_common.errors import UnauthorizedError
from recoup_common.events.consumer import KafkaConsumerLoop
from recoup_common.events.topics import ALL_TOPICS
from recoup_common.http import create_app
from recoup_common.logging import get_logger

from recoup_realtime.hub import Hub
from recoup_realtime.settings import get_settings

log = get_logger(__name__)
settings = get_settings()
hub = Hub(settings.replay_buffer)


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    consumer = None
    if settings.kafka_bootstrap_servers:
        consumer = KafkaConsumerLoop(
            settings.kafka_bootstrap_servers,
            group_id=f"realtime-{uuid.uuid4().hex[:8]}",
            topics=ALL_TOPICS,
            handler=hub.publish,
            client_id="realtime",
        )
        consumer.start()
    else:
        log.warning("realtime: no KAFKA_BOOTSTRAP_SERVERS; websocket will only send heartbeats")
    try:
        yield
    finally:
        if consumer:
            await consumer.stop()


app = create_app(settings, title="Recoup Realtime", lifespan=lifespan)


@app.get("/stats", tags=["ops"])
async def stats() -> dict[str, Any]:
    return hub.stats()


@app.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    token: str = Query(...),
    case_id: str | None = None,
    replay: int = Query(default=20, ge=0, le=200),
) -> None:
    try:
        principal = decode_token(settings, token)
    except UnauthorizedError:
        await ws.close(code=4401)
        return
    tenant = str(principal.tenant_id)
    await ws.accept()
    hub.connect(tenant, ws)
    try:
        await ws.send_json(
            {"type": "realtime.hello", "tenantid": tenant, "data": {"replay": replay}}
        )
        for e in hub.recent(tenant, case_id=case_id, limit=replay):
            await ws.send_json(e)
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=settings.heartbeat_seconds)
                if msg == "ping":
                    await ws.send_json({"type": "realtime.pong", "tenantid": tenant, "data": {}})
            except TimeoutError:
                await ws.send_json({"type": "realtime.heartbeat", "tenantid": tenant, "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.info("realtime.ws_closed", error=str(e)[:120])
    finally:
        hub.disconnect(tenant, ws)
        with contextlib.suppress(Exception):
            await ws.close()
