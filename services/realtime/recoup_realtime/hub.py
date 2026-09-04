"""In-process fan-out. Every replica consumes the full stream (unique consumer group), so no
cross-replica bus is needed; a Redis pub/sub layer can be added if sticky sessions are not used."""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket
from recoup_common.events import DomainEvent
from recoup_common.logging import get_logger

log = get_logger(__name__)


class Hub:
    def __init__(self, replay_buffer: int = 200) -> None:
        self._conns: dict[str, set[WebSocket]] = defaultdict(set)
        self._recent: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=replay_buffer)
        )
        self.delivered = 0

    def connect(self, tenant_id: str, ws: WebSocket) -> None:
        self._conns[tenant_id].add(ws)

    def disconnect(self, tenant_id: str, ws: WebSocket) -> None:
        self._conns[tenant_id].discard(ws)

    def recent(
        self, tenant_id: str, *, case_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        items = list(self._recent[tenant_id])
        if case_id:
            items = [
                e
                for e in items
                if e.get("data", {}).get("case_id") == case_id or e.get("subject") == case_id
            ]
        return items[-limit:]

    async def publish(self, event: DomainEvent) -> None:
        payload = event.model_dump(mode="json")
        self._recent[event.tenantid].append(payload)
        dead: list[WebSocket] = []
        for ws in list(self._conns.get(event.tenantid, ())):
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=2)
                self.delivered += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(event.tenantid, ws)
            with contextlib.suppress(Exception):
                await ws.close()

    def stats(self) -> dict[str, Any]:
        return {
            "tenants": len(self._conns),
            "connections": sum(len(v) for v in self._conns.values()),
            "delivered": self.delivered,
        }
