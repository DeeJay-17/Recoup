from __future__ import annotations

import uuid
from typing import Any

import httpx

from recoup_knowledge.settings import Settings


class CaseClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=20)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def detail(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any] | None:
        r = await self.c.get(
            f"/internal/cases/{case_id}/detail", params={"tenant_id": str(tenant_id)}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return dict(r.json())


class CommClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=20)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def messages(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[dict[str, Any]]:
        r = await self.c.get(
            f"/internal/cases/{case_id}/messages", params={"tenant_id": str(tenant_id)}
        )
        if r.status_code >= 400:
            return []
        return list(r.json())


async def resolve_tenant_id(settings: Settings) -> uuid.UUID:
    if settings.default_tenant_id:
        return uuid.UUID(settings.default_tenant_id)
    async with httpx.AsyncClient(base_url=settings.iam_url, timeout=10) as client:
        r = await client.get(f"/tenants/by-slug/{settings.default_tenant_slug}")
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])
