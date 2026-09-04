"""Internal HTTP clients for the services behind the gateway."""

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
            msg = body.get("message", r.text)
            details = body.get("details") or {}
        except ValueError:
            msg, details = r.text, {}
        err = UpstreamError(f"{what}: {msg}", details=details)
        err.status_code = r.status_code if r.status_code < 500 else 502
        raise err


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
        _raise(r, "approval")
        return dict(r.json())

    async def propose_action(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, body: dict[str, Any]
    ) -> dict[str, Any]:
        r = await self.c.post(
            f"/internal/cases/{case_id}/actions", params={"tenant_id": str(tenant_id)}, json=body
        )
        _raise(r, "case")
        return dict(r.json())

    async def mark_executed(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, action_id: str, result: dict[str, Any]
    ) -> None:
        r = await self.c.post(
            f"/internal/cases/{case_id}/actions/{action_id}/executed",
            params={"tenant_id": str(tenant_id), "actor_id": "tool-gateway"},
            json=result,
        )
        _raise(r, "action")

    async def triage(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, body: dict[str, Any]
    ) -> dict[str, Any]:
        r = await self.c.post(
            f"/internal/cases/{case_id}/triage", params={"tenant_id": str(tenant_id)}, json=body
        )
        _raise(r, "case")
        return dict(r.json())

    async def transition(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, to: str, reason: str, actor: str
    ) -> dict[str, Any]:
        r = await self.c.post(
            f"/internal/cases/{case_id}/transition",
            params={"tenant_id": str(tenant_id), "actor_id": actor},
            json={"to": to, "reason": reason},
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


class PolicyClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def evaluate(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID | None,
        actor: str,
        action_type: str,
        context: dict[str, Any],
        record: bool = True,
    ) -> dict[str, Any]:
        r = await self.c.post(
            "/internal/evaluate",
            json={
                "tenant_id": str(tenant_id),
                "case_id": str(case_id) if case_id else None,
                "actor": actor,
                "action_type": action_type,
                "context": context,
                "record": record,
            },
        )
        _raise(r, "policy")
        return dict(r.json())


class CommClient:
    def __init__(self, base_url: str) -> None:
        self.c = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30)

    async def aclose(self) -> None:
        await self.c.aclose()

    async def send(self, body: dict[str, Any]) -> dict[str, Any]:
        r = await self.c.post("/internal/emails/send", json=body)
        _raise(r, "email")
        return dict(r.json())

    async def render(self, template: str, variables: dict[str, Any]) -> dict[str, Any]:
        r = await self.c.post(
            "/templates/render", json={"template": template, "variables": variables}
        )
        _raise(r, "template")
        return dict(r.json())

    async def templates(self) -> list[dict[str, Any]]:
        r = await self.c.get("/templates")
        _raise(r, "templates")
        return list(r.json())

    async def threads(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[dict[str, Any]]:
        r = await self.c.get(
            f"/internal/cases/{case_id}/threads", params={"tenant_id": str(tenant_id)}
        )
        _raise(r, "threads")
        return list(r.json())

    async def messages(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[dict[str, Any]]:
        r = await self.c.get(
            f"/internal/cases/{case_id}/messages", params={"tenant_id": str(tenant_id)}
        )
        _raise(r, "messages")
        return list(r.json())
