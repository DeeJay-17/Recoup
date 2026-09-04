"""Thin client for Mailpit's REST API (dev SMTP sandbox) used for inbound polling."""

from __future__ import annotations

from typing import Any

import httpx


class MailpitClient:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_messages(self, *, limit: int = 100) -> list[dict[str, Any]]:
        r = await self._client.get("/api/v1/messages", params={"start": 0, "limit": limit})
        r.raise_for_status()
        return list(r.json().get("messages", []))

    async def search(self, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
        r = await self._client.get("/api/v1/search", params={"query": query, "limit": limit})
        r.raise_for_status()
        return list(r.json().get("messages", []))

    async def get_message(self, mailpit_id: str) -> dict[str, Any]:
        r = await self._client.get(f"/api/v1/message/{mailpit_id}")
        r.raise_for_status()
        return dict(r.json())

    async def get_headers(self, mailpit_id: str) -> dict[str, list[str]]:
        r = await self._client.get(f"/api/v1/message/{mailpit_id}/headers")
        r.raise_for_status()
        return {k: list(v) for k, v in r.json().items()}

    async def get_part(self, mailpit_id: str, part_id: str) -> bytes:
        r = await self._client.get(f"/api/v1/message/{mailpit_id}/part/{part_id}")
        r.raise_for_status()
        return r.content
