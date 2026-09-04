from __future__ import annotations

import uuid
from typing import Any

import httpx
from recoup_common.errors import DomainError, NotFoundError


class UpstreamError(DomainError):
    status_code = 502
    code = "upstream_error"


def _raise(r: httpx.Response, what: str) -> None:
    if r.status_code == 404:
        raise NotFoundError(f"{what} not found")
    if r.status_code >= 400:
        try:
            body = r.json()
            msg, details = body.get("message", r.text), body.get("details") or {}
            code = body.get("error", "upstream_error")
        except ValueError:
            msg, details, code = r.text, {}, "upstream_error"
        err = UpstreamError(f"{what}: {msg}", details={**details, "upstream_code": code})
        err.status_code = r.status_code if r.status_code < 500 else 502
        raise err


class ToolGatewayClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def manifest(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[dict[str, Any]]:
        r = await self.c.get(
            "/tools/manifest", params={"tenant_id": str(tenant_id), "case_id": str(case_id)}
        )
        _raise(r, "manifest")
        return list(r.json()["tools"])

    async def invoke(
        self,
        name: str,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        run_id: uuid.UUID,
        actor: str,
        args: dict[str, Any],
        idempotency_key: str | None = None,
        approval_ref: str | None = None,
    ) -> dict[str, Any]:
        """Returns the gateway envelope. Policy/approval outcomes (4xx) come back as data, not
        exceptions, so the agent can reason about them."""
        r = await self.c.post(
            f"/tools/{name}/invoke",
            json={
                "tenant_id": str(tenant_id),
                "case_id": str(case_id),
                "run_id": str(run_id),
                "actor": actor,
                "args": args,
                "idempotency_key": idempotency_key,
                "approval_ref": approval_ref,
            },
        )
        if r.status_code in (403, 404, 409, 422, 429):
            body = r.json()
            return {
                "status": "BLOCKED",
                "error": body.get("error"),
                "message": body.get("message"),
                "details": body.get("details") or {},
            }
        _raise(r, f"tool {name}")
        return dict(r.json())


class CaseClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def get_case(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
        r = await self.c.get(f"/internal/cases/{case_id}", params={"tenant_id": str(tenant_id)})
        _raise(r, "case")
        return dict(r.json())

    async def get_detail(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
        r = await self.c.get(
            f"/internal/cases/{case_id}/detail", params={"tenant_id": str(tenant_id)}
        )
        _raise(r, "case")
        return dict(r.json())

    async def get_action(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, action_id: str
    ) -> dict[str, Any]:
        r = await self.c.get(
            f"/internal/cases/{case_id}/actions/{action_id}", params={"tenant_id": str(tenant_id)}
        )
        _raise(r, "action")
        return dict(r.json())

    async def triage(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, body: dict[str, Any]
    ) -> dict[str, Any]:
        r = await self.c.post(
            f"/internal/cases/{case_id}/triage", params={"tenant_id": str(tenant_id)}, json=body
        )
        _raise(r, "case")
        return dict(r.json())

    async def transition(
        self,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        to: str,
        reason: str,
        actor: str,
        resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r = await self.c.post(
            f"/internal/cases/{case_id}/transition",
            params={"tenant_id": str(tenant_id), "actor_id": actor},
            json={"to": to, "reason": reason, "resolution": resolution},
        )
        _raise(r, "case")
        return dict(r.json())

    async def append_event(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, body: dict[str, Any]
    ) -> None:
        r = await self.c.post(
            f"/internal/cases/{case_id}/events", params={"tenant_id": str(tenant_id)}, json=body
        )
        _raise(r, "case")


class KnowledgeClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def extract_memory(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
        r = await self.c.post(
            "/internal/memory/extract", json={"tenant_id": str(tenant_id), "case_id": str(case_id)}
        )
        _raise(r, "knowledge")
        return dict(r.json())
