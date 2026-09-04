"""Outbound integrations: SMTP sender and the case-service internal API."""

from __future__ import annotations

import uuid
from email.message import EmailMessage
from typing import Any

import aiosmtplib
import httpx
from recoup_common.logging import get_logger

from recoup_communication.settings import Settings

log = get_logger(__name__)


class SmtpSender:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    async def send(
        self,
        *,
        message_id: str,
        from_addr: str,
        to: list[str],
        cc: list[str],
        subject: str,
        body_text: str,
        in_reply_to: str | None,
    ) -> None:
        msg = EmailMessage()
        msg["Message-ID"] = message_id
        msg["From"] = f"{self.s.from_name} <{from_addr}>"
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body_text)
        await aiosmtplib.send(
            msg,
            hostname=self.s.smtp_host,
            port=self.s.smtp_port,
            use_tls=self.s.smtp_use_tls,
            start_tls=False,
        )


class CaseClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._client = httpx.AsyncClient(base_url=settings.case_url.rstrip("/"), timeout=15)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def case_by_invoice(
        self, tenant_id: uuid.UUID, invoice_ref: str
    ) -> dict[str, Any] | None:
        r = await self._client.get(
            f"/internal/cases/by-invoice/{invoice_ref}", params={"tenant_id": str(tenant_id)}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return dict(r.json())

    async def get_case(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any] | None:
        r = await self._client.get(
            f"/internal/cases/{case_id}", params={"tenant_id": str(tenant_id)}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return dict(r.json())

    async def append_event(
        self,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        *,
        kind: str,
        actor_type: str,
        actor_id: str,
        title: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            r = await self._client.post(
                f"/internal/cases/{case_id}/events",
                params={"tenant_id": str(tenant_id)},
                json={
                    "kind": kind,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "title": title,
                    "payload": payload,
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            # Timeline is a convenience mirror; the comm record itself is the source of truth.
            log.warning("comm.timeline_append_failed", case_id=str(case_id), error=str(e))


async def resolve_tenant_id(settings: Settings) -> uuid.UUID:
    if settings.default_tenant_id:
        return uuid.UUID(settings.default_tenant_id)
    async with httpx.AsyncClient(base_url=settings.iam_url, timeout=10) as client:
        r = await client.get(f"/tenants/by-slug/{settings.default_tenant_slug}")
        r.raise_for_status()
        return uuid.UUID(r.json()["id"])
