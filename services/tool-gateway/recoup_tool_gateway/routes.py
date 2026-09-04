from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from recoup_common.auth import Principal, get_principal
from recoup_common.db import Database
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.models import ToolInvocation
from recoup_tool_gateway.registry import registry
from recoup_tool_gateway.service import InvokeRequest, InvokeResponse, ToolService

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Viewer = Annotated[Principal, Depends(get_principal)]


def _ctx(request: Request, req: InvokeRequest) -> ToolContext:
    st = request.app.state
    return ToolContext(
        tenant_id=req.tenant_id,
        case_id=req.case_id,
        run_id=req.run_id,
        actor=req.actor,
        settings=st.settings,
        erp=st.erp,
        cases=st.cases,
        policy=st.policy,
        comm=st.comm,
    )


class ManifestResponse(BaseModel):
    tools: list[dict[str, Any]]
    hidden: list[str]
    case_id: str | None


# ---------- agent-facing (internal network) ----------
@router.get("/tools/manifest", response_model=ManifestResponse, tags=["tools"])
async def manifest(
    request: Request, tenant_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None
) -> ManifestResponse:
    """Least-privilege manifest: mutating tools appear only when the case context warrants them."""
    case = customer = None
    if tenant_id and case_id:
        ctx = ToolContext(
            tenant_id=tenant_id,
            case_id=case_id,
            run_id=None,
            actor="system:manifest",
            settings=request.app.state.settings,
            erp=request.app.state.erp,
            cases=request.app.state.cases,
            policy=request.app.state.policy,
            comm=request.app.state.comm,
        )
        case = await ctx.case()
        try:
            customer = await ctx.customer()
        except Exception:
            customer = None
    tools = registry.manifest(case=case, customer=customer)
    shown = {t["name"] for t in tools}
    return ManifestResponse(
        tools=tools,
        hidden=[t.name for t in registry.all() if t.name not in shown],
        case_id=str(case_id) if case_id else None,
    )


@router.post("/tools/{name}/invoke", response_model=InvokeResponse, tags=["tools"])
async def invoke(name: str, body: InvokeRequest, request: Request) -> InvokeResponse:
    svc: ToolService = request.app.state.service
    return await svc.invoke(name, body, _ctx(request, body))


# ---------- human-facing audit (via gateway, JWT) ----------
class InvocationOut(BaseModel):
    id: int
    case_id: uuid.UUID | None
    run_id: uuid.UUID | None
    tool: str
    actor: str
    args: dict[str, Any]
    result: dict[str, Any] | None
    status: str
    policy_decision: str | None
    approval_ref: str | None
    idempotency_key: str | None
    error: str | None
    latency_ms: int | None
    trace_id: str | None
    invoked_at: str

    @classmethod
    def from_row(cls, r: ToolInvocation) -> InvocationOut:
        return cls(
            id=r.id,
            case_id=r.case_id,
            run_id=r.run_id,
            tool=r.tool,
            actor=r.actor,
            args=r.args,
            result=r.result,
            status=r.status,
            policy_decision=r.policy_decision,
            approval_ref=r.approval_ref,
            idempotency_key=r.idempotency_key,
            error=r.error,
            latency_ms=r.latency_ms,
            trace_id=r.trace_id,
            invoked_at=r.invoked_at.isoformat(),
        )


@router.get("/invocations", response_model=list[InvocationOut], tags=["audit"])
async def list_invocations(
    session: SessionDep,
    principal: Viewer,
    case_id: uuid.UUID | None = None,
    tool: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[InvocationOut]:
    stmt = select(ToolInvocation).where(ToolInvocation.tenant_id == principal.tenant_id)
    if case_id:
        stmt = stmt.where(ToolInvocation.case_id == case_id)
    if tool:
        stmt = stmt.where(ToolInvocation.tool == tool)
    rows = await session.scalars(stmt.order_by(ToolInvocation.invoked_at.desc()).limit(limit))
    return [InvocationOut.from_row(r) for r in rows.all()]


@router.get("/internal/invocations", response_model=list[InvocationOut], tags=["internal"])
async def internal_invocations(
    session: SessionDep,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[InvocationOut]:
    """Audit rows for one agent run: the eval harness uses these to prove that a shadow run
    executed nothing."""
    stmt = select(ToolInvocation).where(ToolInvocation.tenant_id == tenant_id)
    if run_id:
        stmt = stmt.where(ToolInvocation.run_id == run_id)
    if case_id:
        stmt = stmt.where(ToolInvocation.case_id == case_id)
    rows = await session.scalars(stmt.order_by(ToolInvocation.invoked_at).limit(limit))
    return [InvocationOut.from_row(r) for r in rows.all()]


@router.get("/catalog", tags=["audit"])
async def catalog(principal: Viewer) -> list[dict[str, Any]]:
    """Full tool catalog (no case filtering) for the console's Agents page."""
    return [{k: v for k, v in t.manifest_entry().items() if k != "returns"} for t in registry.all()]
