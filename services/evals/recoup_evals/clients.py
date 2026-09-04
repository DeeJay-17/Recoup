from __future__ import annotations

import uuid
from typing import Any

import httpx
from recoup_common.errors import DomainError, NotFoundError

from recoup_evals.settings import Settings


class UpstreamError(DomainError):
    status_code = 502
    code = "upstream_error"


def _raise(r: httpx.Response, what: str) -> None:
    if r.status_code == 404:
        raise NotFoundError(f"{what} not found")
    if r.status_code >= 400:
        raise UpstreamError(f"{what}: {r.text[:300]}")


class ErpClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def overdue_invoices(self, *, days: int, limit: int) -> list[dict[str, Any]]:
        r = await self.c.get(
            "/invoices", params={"status": "OPEN", "overdue_days_gte": days, "limit": limit}
        )
        _raise(r, "invoices")
        return list(r.json()["items"])

    async def invoice(self, ref: str) -> dict[str, Any] | None:
        r = await self.c.get(f"/invoices/{ref}")
        return None if r.status_code == 404 else dict(r.json())

    async def customer(self, ref: str) -> dict[str, Any] | None:
        r = await self.c.get(f"/customers/{ref}")
        return None if r.status_code == 404 else dict(r.json())

    async def ground_truth(self, ref: str) -> dict[str, Any] | None:
        r = await self.c.get(f"/admin/ground-truth/{ref}")
        return None if r.status_code == 404 else dict(r.json())


class CaseClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def by_invoice(self, tenant_id: uuid.UUID, invoice_ref: str) -> dict[str, Any] | None:
        r = await self.c.get(
            f"/internal/cases/by-invoice/{invoice_ref}", params={"tenant_id": str(tenant_id)}
        )
        return None if r.status_code == 404 else dict(r.json())


class OrchestratorClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def start(self, tenant_id: uuid.UUID, case_id: uuid.UUID, mode: str = "SHADOW") -> str:
        r = await self.c.post(
            "/internal/runs",
            params={"tenant_id": str(tenant_id)},
            json={"case_id": str(case_id), "mode": mode},
        )
        _raise(r, "run")
        return str(r.json()["run_id"])

    async def run(
        self, tenant_id: uuid.UUID, run_id: str, *, with_messages: bool = False
    ) -> dict[str, Any] | None:
        """None while the workflow's first activity has not yet written the run row."""
        r = await self.c.get(
            f"/internal/runs/{run_id}",
            params={"tenant_id": str(tenant_id), "with_messages": with_messages},
        )
        if r.status_code == 404:
            return None
        _raise(r, "run")
        return dict(r.json())


class ToolGatewayClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def invocations(self, tenant_id: uuid.UUID, run_id: str) -> list[dict[str, Any]]:
        r = await self.c.get(
            "/internal/invocations", params={"tenant_id": str(tenant_id), "run_id": run_id}
        )
        if r.status_code >= 400:
            return []
        return list(r.json())

    async def invoke(self, tool: str, body: dict[str, Any]) -> dict[str, Any]:
        r = await self.c.post(f"/tools/{tool}/invoke", json=body)
        try:
            payload = dict(r.json())
        except ValueError:
            payload = {"status": "ERROR", "message": r.text[:200]}
        payload["http_status"] = r.status_code
        return payload


async def resolve_tenant_id(settings: Settings) -> uuid.UUID:
    if settings.default_tenant_id:
        return uuid.UUID(settings.default_tenant_id)
    async with httpx.AsyncClient(base_url=settings.iam_url, timeout=10) as client:
        r = await client.get(f"/tenants/by-slug/{settings.default_tenant_slug}")
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])
