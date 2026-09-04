from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_communication import service, templating
from recoup_communication.models import Thread
from recoup_communication.schemas import (
    MessageOut,
    PollResult,
    RenderRequest,
    RenderResponse,
    SendEmailRequest,
    TemplateInfo,
    ThreadOut,
)

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Analyst = Annotated[Principal, Depends(require_role(Role.ANALYST))]
Viewer = Annotated[Principal, Depends(get_principal)]


async def _thread_out(session: AsyncSession, t: Thread) -> ThreadOut:
    return ThreadOut(
        id=t.id,
        tenant_id=t.tenant_id,
        case_id=t.case_id,
        customer_ref=t.customer_ref,
        subject=t.subject,
        invoice_refs=t.invoice_refs,
        last_message_at=t.last_message_at,
        created_at=t.created_at,
        messages=[MessageOut.model_validate(m) for m in await service.thread_messages(session, t)],
    )


# ---------- user-facing ----------
@router.get("/templates", response_model=list[TemplateInfo], tags=["templates"])
async def list_templates() -> list[TemplateInfo]:
    return [TemplateInfo(**t) for t in templating.list_templates()]


@router.post("/templates/render", response_model=RenderResponse, tags=["templates"])
async def render_template(body: RenderRequest) -> RenderResponse:
    subject, text, missing = templating.render(body.template, body.variables)
    return RenderResponse(
        template=body.template, subject=subject, body_text=text, missing_variables=missing
    )


@router.get("/threads/{thread_id}", response_model=ThreadOut, tags=["threads"])
async def get_thread(thread_id: uuid.UUID, session: SessionDep, principal: Viewer) -> ThreadOut:
    return await _thread_out(
        session, await service.get_thread(session, principal.tenant_id, thread_id)
    )


@router.get("/cases/{case_id}/threads", response_model=list[ThreadOut], tags=["threads"])
async def case_threads(
    case_id: uuid.UUID, session: SessionDep, principal: Viewer
) -> list[ThreadOut]:
    return [
        await _thread_out(session, t)
        for t in await service.threads_for_case(session, principal.tenant_id, case_id)
    ]


@router.get("/messages/unlinked", response_model=list[MessageOut], tags=["threads"])
async def unlinked(
    session: SessionDep, principal: Viewer, limit: int = Query(default=50, ge=1, le=500)
) -> list[MessageOut]:
    rows = await service.unlinked_messages(session, principal.tenant_id, limit=limit)
    return [MessageOut.model_validate(m) for m in rows]


class LinkBody(BaseModel):
    case_id: uuid.UUID


@router.post("/messages/{message_id}/link", response_model=MessageOut, tags=["threads"])
async def link_message(
    message_id: uuid.UUID, body: LinkBody, session: SessionDep, principal: Analyst, request: Request
) -> MessageOut:
    m = await service.relink_message(
        session, request.app.state.cases, principal.tenant_id, message_id, body.case_id
    )
    return MessageOut.model_validate(m)


@router.post("/emails/send", response_model=MessageOut, status_code=201, tags=["emails"])
async def send_as_user(body: SendEmailRequest, principal: Analyst, request: Request) -> MessageOut:
    """Manual send from the console (humans are their own approval)."""
    body.sent_by = f"human:{principal.sub}"
    body.approval_ref = body.approval_ref or f"human:{principal.sub}"
    m = await service.send_email(
        request.app.state.db,
        request.app.state.smtp,
        request.app.state.cases,
        request.app.state.settings,
        body,
        principal.tenant_id,
    )
    return MessageOut.model_validate(m)


# ---------- internal: tool gateway / orchestrator ----------
@internal.post("/emails/send", response_model=MessageOut, status_code=201)
async def send_internal(body: SendEmailRequest, request: Request) -> MessageOut:
    if body.tenant_id is None:
        from recoup_common.errors import ValidationError

        raise ValidationError("tenant_id is required on the internal send endpoint")
    m = await service.send_email(
        request.app.state.db,
        request.app.state.smtp,
        request.app.state.cases,
        request.app.state.settings,
        body,
        body.tenant_id,
    )
    return MessageOut.model_validate(m)


@internal.get("/cases/{case_id}/messages", response_model=list[MessageOut])
async def case_messages_internal(
    case_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> list[MessageOut]:
    return [
        MessageOut.model_validate(m)
        for m in await service.messages_for_case(session, tenant_id, case_id)
    ]


@internal.get("/cases/{case_id}/threads", response_model=list[ThreadOut])
async def case_threads_internal(
    case_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> list[ThreadOut]:
    return [
        await _thread_out(session, t)
        for t in await service.threads_for_case(session, tenant_id, case_id)
    ]


@internal.post("/inbound/poll", response_model=PollResult)
async def poll_now(request: Request) -> PollResult:
    poller: service.InboundPoller = request.app.state.poller
    return await poller.poll_once()
