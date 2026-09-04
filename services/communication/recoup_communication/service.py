from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import Any

from recoup_common.db import Database, utcnow
from recoup_common.errors import ConflictError, NotFoundError, ValidationError
from recoup_common.events.outbox import enqueue_event
from recoup_common.logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_communication import templating
from recoup_communication.attachments import extract_text
from recoup_communication.clients import CaseClient, SmtpSender, resolve_tenant_id
from recoup_communication.linking import (
    extract_invoice_refs,
    new_message_id,
    normalise_address,
    strip_reply_prefix,
)
from recoup_communication.mailpit import MailpitClient
from recoup_communication.models import Message, Outbox, Thread
from recoup_communication.schemas import PollResult, SendEmailRequest
from recoup_communication.settings import Settings

log = get_logger(__name__)
SOURCE = "communication-service"
EVENT_SENT = "comm.email.sent"
EVENT_RECEIVED = "comm.email.received"
EVENT_FAILED = "comm.email.failed"


# ---------- queries ----------
async def get_thread(session: AsyncSession, tenant_id: uuid.UUID, thread_id: uuid.UUID) -> Thread:
    t = await session.get(Thread, thread_id)
    if not t or t.tenant_id != tenant_id:
        raise NotFoundError("thread not found")
    return t


async def thread_messages(session: AsyncSession, thread: Thread) -> list[Message]:
    rows = await session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.created_at)
    )
    return list(rows.all())


async def threads_for_case(
    session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[Thread]:
    rows = await session.scalars(
        select(Thread)
        .where(Thread.tenant_id == tenant_id, Thread.case_id == case_id)
        .order_by(Thread.created_at)
    )
    return list(rows.all())


async def messages_for_case(
    session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.case_id == case_id)
        .order_by(Message.created_at)
    )
    return list(rows.all())


async def unlinked_messages(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int
) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.status == "UNLINKED")
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


# ---------- outbound ----------
async def _thread_for_send(
    session: AsyncSession, req: SendEmailRequest, tenant_id: uuid.UUID, subject: str
) -> Thread:
    if req.in_reply_to:
        parent = await session.scalar(select(Message).where(Message.message_id == req.in_reply_to))
        if parent and parent.tenant_id == tenant_id:
            return await get_thread(session, tenant_id, parent.thread_id)
    existing = await threads_for_case(session, tenant_id, req.case_id)
    if existing:
        return existing[-1]
    t = Thread(
        tenant_id=tenant_id,
        case_id=req.case_id,
        subject=strip_reply_prefix(subject),
        invoice_refs=list(req.invoice_refs),
        created_at=utcnow(),
    )
    session.add(t)
    await session.flush()
    return t


async def send_email(
    db: Database,
    smtp: SmtpSender,
    cases: CaseClient,
    settings: Settings,
    req: SendEmailRequest,
    tenant_id: uuid.UUID,
) -> Message:
    """Idempotent send. Records QUEUED -> SENT/FAILED in separate transactions so a crash mid-SMTP
    leaves an auditable row rather than a silent gap."""
    async with db.session() as session:
        dup = await session.scalar(
            select(Message).where(Message.idempotency_key == req.idempotency_key)
        )
        if dup:
            return dup
        if req.template:
            subject, body, _ = templating.render(req.template, req.variables)
        else:
            if not req.subject or not req.body_text:
                raise ValidationError("subject and body_text are required without a template")
            subject, body = req.subject, req.body_text
        thread = await _thread_for_send(session, req, tenant_id, subject)
        invoice_refs = list(dict.fromkeys(req.invoice_refs + extract_invoice_refs(subject, body)))
        msg = Message(
            thread_id=thread.id,
            tenant_id=tenant_id,
            case_id=req.case_id,
            direction="OUT",
            status="QUEUED",
            message_id=new_message_id(settings.message_id_domain),
            in_reply_to=req.in_reply_to,
            from_addr=settings.inbound_mailbox,
            to_addrs=[str(a).lower() for a in req.to],
            cc_addrs=[str(a).lower() for a in req.cc],
            subject=subject,
            body_text=body,
            template=req.template,
            invoice_refs=invoice_refs,
            link_method="case",
            idempotency_key=req.idempotency_key,
            approval_ref=req.approval_ref,
            sent_by=req.sent_by,
            created_at=utcnow(),
        )
        session.add(msg)
        for ref in invoice_refs:
            if ref not in thread.invoice_refs:
                thread.invoice_refs = [*thread.invoice_refs, ref]
        await session.flush()
        msg_id = msg.id

    error: str | None = None
    try:
        await smtp.send(
            message_id=msg.message_id,
            from_addr=msg.from_addr,
            to=msg.to_addrs,
            cc=msg.cc_addrs,
            subject=msg.subject,
            body_text=msg.body_text,
            in_reply_to=msg.in_reply_to,
        )
    except Exception as e:
        error = str(e)[:2000]
        log.error("comm.smtp_failed", message_id=msg.message_id, error=error)

    async with db.session() as session:
        m = await session.get(Message, msg_id)
        assert m is not None
        t = await session.get(Thread, m.thread_id)
        assert t is not None
        if error:
            m.status, m.error = "FAILED", error
        else:
            m.status, m.sent_at = "SENT", utcnow()
            t.last_message_at = m.sent_at
        enqueue_event(
            session,
            Outbox,
            source=SOURCE,
            tenant_id=tenant_id,
            aggregate_id=m.case_id or m.thread_id,
            event_type=EVENT_FAILED if error else EVENT_SENT,
            payload={
                "message_id": m.message_id,
                "thread_id": str(m.thread_id),
                "case_id": str(m.case_id) if m.case_id else None,
                "to": m.to_addrs,
                "subject": m.subject,
                "template": m.template,
                "approval_ref": m.approval_ref,
                "error": error,
            },
        )
        out = m
    if out.case_id:
        await cases.append_event(
            tenant_id,
            out.case_id,
            kind="email_sent" if not error else "email_failed",
            actor_type="agent" if (out.sent_by or "").startswith("agent") else "human",
            actor_id=out.sent_by or "system",
            title=(
                f"Email {'sent' if not error else 'FAILED'} to "
                f"{', '.join(out.to_addrs)}: {out.subject}"
            ),
            payload={
                "message_id": out.message_id,
                "thread_id": str(out.thread_id),
                "template": out.template,
                "body_preview": out.body_text[:300],
                "approval_ref": out.approval_ref,
                "error": error,
            },
        )
    return out


# ---------- inbound ----------
class InboundPoller:
    def __init__(
        self, db: Database, mailpit: MailpitClient, cases: CaseClient, settings: Settings
    ) -> None:
        self.db = db
        self.mailpit = mailpit
        self.cases = cases
        self.s = settings
        self._tenant_id: uuid.UUID | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def tenant_id(self) -> uuid.UUID:
        if self._tenant_id is None:
            self._tenant_id = await resolve_tenant_id(self.s)
        return self._tenant_id

    async def poll_once(self) -> PollResult:
        tenant_id = await self.tenant_id()
        mailbox = self.s.inbound_mailbox.lower()
        scanned = ingested = linked = unlinked = 0
        for summary in await self.mailpit.list_messages(limit=200):
            scanned += 1
            to_addrs = [normalise_address(a.get("Address", "")) for a in summary.get("To", [])]
            from_addr = normalise_address(summary.get("From", {}).get("Address", ""))
            if mailbox not in to_addrs or from_addr == mailbox:
                continue  # not for us, or our own outbound copy
            async with self.db.session() as session:
                if await session.scalar(
                    select(Message.id).where(Message.external_id == summary["ID"])
                ):
                    continue
                msg = await self._ingest(session, tenant_id, summary["ID"])
                ingested += 1
                if msg.case_id:
                    linked += 1
                else:
                    unlinked += 1
                to_notify = (msg.case_id, msg.id)
            if to_notify[0]:
                await self._mirror_to_case(tenant_id, to_notify[1])
        res = PollResult(scanned=scanned, ingested=ingested, linked=linked, unlinked=unlinked)
        if ingested:
            log.info("comm.inbound.poll", **res.model_dump())
        return res

    async def _ingest(
        self, session: AsyncSession, tenant_id: uuid.UUID, mailpit_id: str
    ) -> Message:
        full = await self.mailpit.get_message(mailpit_id)
        headers = await self.mailpit.get_headers(mailpit_id)
        irt = headers.get("In-Reply-To") or []
        in_reply_to: str | None = irt[0] if irt else None
        refs = " ".join(headers.get("References") or [])
        message_id = full.get("MessageID") or new_message_id("inbound.mailpit")
        if not message_id.startswith("<"):
            message_id = f"<{message_id}>"
        subject = full.get("Subject") or "(no subject)"
        body = full.get("Text") or ""
        attachments: list[dict[str, Any]] = []
        for att in full.get("Attachments") or []:
            text: str | None = None
            with contextlib.suppress(Exception):
                raw = await self.mailpit.get_part(mailpit_id, att["PartID"])
                text = extract_text(
                    raw, att.get("ContentType", ""), max_chars=self.s.attachment_text_max_chars
                )
            attachments.append(
                {
                    "filename": att.get("FileName"),
                    "content_type": att.get("ContentType"),
                    "size": att.get("Size"),
                    "text_excerpt": text,
                }
            )
        att_text = " ".join(a["text_excerpt"] or "" for a in attachments)
        invoice_refs = extract_invoice_refs(subject, body, att_text)

        # 1) thread by In-Reply-To / References
        thread: Thread | None = None
        link_method = "none"
        candidates: list[str] = [x for x in [in_reply_to, *refs.split()] if x]
        if candidates:
            parent = await session.scalar(
                select(Message).where(
                    Message.message_id.in_(candidates), Message.tenant_id == tenant_id
                )
            )
            if parent:
                thread = await session.get(Thread, parent.thread_id)
                link_method = "thread"
        # 2) invoice number -> case
        case_id: uuid.UUID | None = thread.case_id if thread else None
        customer_ref: str | None = thread.customer_ref if thread else None
        if thread is None and invoice_refs:
            for ref in invoice_refs:
                case = await self.cases.case_by_invoice(tenant_id, ref)
                if case:
                    case_id = uuid.UUID(case["id"])
                    customer_ref = case["customer_ref"]
                    existing = await threads_for_case(session, tenant_id, case_id)
                    thread = existing[-1] if existing else None
                    link_method = "invoice_ref"
                    break
        if thread is None:
            thread = Thread(
                tenant_id=tenant_id,
                case_id=case_id,
                customer_ref=customer_ref,
                subject=strip_reply_prefix(subject),
                invoice_refs=invoice_refs,
                created_at=utcnow(),
            )
            session.add(thread)
            await session.flush()
        received_at = _parse_dt(full.get("Date")) or utcnow()
        msg = Message(
            thread_id=thread.id,
            tenant_id=tenant_id,
            case_id=case_id,
            direction="IN",
            status="RECEIVED" if case_id else "UNLINKED",
            message_id=message_id,
            in_reply_to=in_reply_to,
            external_id=mailpit_id,
            from_addr=normalise_address(full.get("From", {}).get("Address", "")),
            to_addrs=[normalise_address(a.get("Address", "")) for a in full.get("To", [])],
            cc_addrs=[normalise_address(a.get("Address", "")) for a in full.get("Cc", []) or []],
            subject=subject,
            body_text=body,
            body_html=full.get("HTML") or None,
            attachments=attachments,
            invoice_refs=invoice_refs,
            link_method=link_method,
            received_at=received_at,
            created_at=utcnow(),
        )
        session.add(msg)
        thread.last_message_at = received_at
        for ref in invoice_refs:
            if ref not in thread.invoice_refs:
                thread.invoice_refs = [*thread.invoice_refs, ref]
        await session.flush()
        enqueue_event(
            session,
            Outbox,
            source=SOURCE,
            tenant_id=tenant_id,
            aggregate_id=case_id or thread.id,
            event_type=EVENT_RECEIVED,
            payload={
                "message_id": msg.message_id,
                "thread_id": str(thread.id),
                "case_id": str(case_id) if case_id else None,
                "from": msg.from_addr,
                "subject": subject,
                "invoice_refs": invoice_refs,
                "link_method": link_method,
                "has_attachments": bool(attachments),
            },
        )
        return msg

    async def _mirror_to_case(self, tenant_id: uuid.UUID, message_pk: uuid.UUID) -> None:
        async with self.db.session() as session:
            m = await session.get(Message, message_pk)
            if not m or not m.case_id:
                return
            await self.cases.append_event(
                tenant_id,
                m.case_id,
                kind="email_received",
                actor_type="system",
                actor_id=f"customer:{m.from_addr}",
                title=f"Email received from {m.from_addr}: {m.subject}",
                payload={
                    "message_id": m.message_id,
                    "thread_id": str(m.thread_id),
                    "body_preview": m.body_text[:300],
                    "attachments": [a.get("filename") for a in m.attachments],
                    "link_method": m.link_method,
                },
            )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception as e:
                log.warning("comm.inbound.poll_failed", error=str(e))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.s.inbound_poll_interval_seconds
                )

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="inbound-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


async def relink_message(
    session: AsyncSession,
    cases: CaseClient,
    tenant_id: uuid.UUID,
    message_pk: uuid.UUID,
    case_id: uuid.UUID,
) -> Message:
    """Human links an UNLINKED inbound message to a case from the console."""
    m = await session.get(Message, message_pk)
    if not m or m.tenant_id != tenant_id:
        raise NotFoundError("message not found")
    if m.case_id:
        raise ConflictError("message is already linked")
    if not await cases.get_case(tenant_id, case_id):
        raise NotFoundError("case not found")
    m.case_id, m.status, m.link_method = case_id, "RECEIVED", "manual"
    t = await session.get(Thread, m.thread_id)
    if t and t.case_id is None:
        t.case_id = case_id
    return m


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return None
