from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database, utcnow
from recoup_common.errors import NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_orchestrator import prompt_store
from recoup_orchestrator.models import AgentRun, AgentStep, ModelConfig
from recoup_orchestrator.runs import RunManager

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Viewer = Annotated[Principal, Depends(get_principal)]
Analyst = Annotated[Principal, Depends(require_role(Role.ANALYST))]
Manager = Annotated[Principal, Depends(require_role(Role.MANAGER))]


# ---------- schemas ----------
class RunOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    workflow_id: str
    mode: str
    status: str
    phase: str | None
    prompt_bundle: dict[str, Any]
    models: dict[str, Any] = Field(default_factory=dict, description="Effective model per tier")
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    outcome: dict[str, Any] | None
    started_at: str
    updated_at: str
    ended_at: str | None

    @classmethod
    def from_row(cls, r: AgentRun) -> RunOut:
        return cls(
            id=r.id,
            case_id=r.case_id,
            workflow_id=r.workflow_id,
            mode=r.mode,
            status=r.status,
            phase=r.phase,
            prompt_bundle=r.prompt_bundle,
            models=r.model_config_,
            steps=r.steps,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            cost_usd=float(r.cost_usd),
            outcome=r.outcome,
            started_at=r.started_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
            ended_at=r.ended_at.isoformat() if r.ended_at else None,
        )


class StepOut(BaseModel):
    id: int
    step_no: int
    agent_name: str
    kind: str
    status: str
    input_summary: dict[str, Any] | None
    output: dict[str, Any] | None
    tool_calls: list[Any]
    provider: str | None
    model: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None
    error: str | None
    trace_id: str | None
    started_at: str
    ended_at: str | None
    messages: list[Any] | None = None

    @classmethod
    def from_row(cls, r: AgentStep, *, with_messages: bool = False) -> StepOut:
        return cls(
            id=r.id,
            step_no=r.step_no,
            agent_name=r.agent_name,
            kind=r.kind,
            status=r.status,
            input_summary=r.input_summary,
            output=r.output,
            tool_calls=r.tool_calls,
            provider=r.provider,
            model=r.model,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            cost_usd=float(r.cost_usd),
            latency_ms=r.latency_ms,
            error=r.error,
            trace_id=r.trace_id,
            started_at=r.started_at.isoformat(),
            ended_at=r.ended_at.isoformat() if r.ended_at else None,
            messages=r.messages if with_messages else None,
        )


class RunDetail(BaseModel):
    run: RunOut
    steps: list[StepOut]
    workflow: dict[str, Any] | None = None


class StartRunBody(BaseModel):
    case_id: uuid.UUID
    mode: str = Field(default="LIVE", pattern=r"^(LIVE|SHADOW|REPLAY)$")


class SignalBody(BaseModel):
    signal: str = Field(
        pattern=r"^(customer_replied|action_decided|human_takeover|human_release|cancel_run)$"
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class PromptOut(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    content: str
    notes: str | None
    created_by: str
    created_at: str
    is_active: bool


class PromptVersionBody(BaseModel):
    content: str = Field(min_length=20)
    notes: str | None = None
    activate: bool = True


class ModelConfigOut(BaseModel):
    defaults: dict[str, dict[str, str]]
    overrides: dict[str, Any]
    effective: dict[str, dict[str, str]]


class ModelConfigBody(BaseModel):
    """Per-tier overrides, e.g. {"fast": {"provider": "google_genai", "model": "gemini-2.5"}}."""

    overrides: dict[str, dict[str, str | None]]


def _prompt_out(p: Any) -> PromptOut:
    return PromptOut(
        id=p.id,
        name=p.name,
        version=p.version,
        content=p.content,
        notes=p.notes,
        created_by=p.created_by,
        created_at=p.created_at.isoformat(),
        is_active=p.is_active,
    )


# ---------- runs ----------
@router.post("/runs", response_model=RunOut, status_code=201, tags=["runs"])
async def start_run(
    body: StartRunBody, session: SessionDep, principal: Analyst, request: Request
) -> RunOut:
    runs: RunManager = request.app.state.runs
    params = await runs.start(
        tenant_id=principal.tenant_id,
        case_id=body.case_id,
        mode=body.mode,
        requested_by=f"human:{principal.sub}",
    )
    # The workflow's first activity inserts the row; return a provisional view meanwhile.
    return RunOut(
        id=params.run_id,
        case_id=body.case_id,
        workflow_id=f"case-{body.case_id}",
        mode=body.mode,
        status="RUNNING",
        phase="starting",
        prompt_bundle={},
        models={},
        steps=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        outcome=None,
        started_at=utcnow().isoformat(),
        updated_at=utcnow().isoformat(),
        ended_at=None,
    )


@router.get("/runs", response_model=list[RunOut], tags=["runs"])
async def list_runs(
    session: SessionDep,
    principal: Viewer,
    case_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[RunOut]:
    stmt = select(AgentRun).where(AgentRun.tenant_id == principal.tenant_id)
    if case_id:
        stmt = stmt.where(AgentRun.case_id == case_id)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    rows = await session.scalars(stmt.order_by(AgentRun.started_at.desc()).limit(limit))
    return [RunOut.from_row(r) for r in rows.all()]


@router.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
async def get_run(
    run_id: uuid.UUID,
    session: SessionDep,
    principal: Viewer,
    request: Request,
    with_messages: bool = False,
) -> RunDetail:
    run = await session.get(AgentRun, run_id)
    if not run or run.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")
    steps = await session.scalars(
        select(AgentStep)
        .where(AgentStep.run_id == run_id)
        .order_by(AgentStep.step_no, AgentStep.id)
    )
    wf = None
    if run.status in ("RUNNING", "WAITING"):
        try:
            wf = await request.app.state.runs.status(run.case_id)
        except NotFoundError:
            wf = None
    return RunDetail(
        run=RunOut.from_row(run),
        steps=[StepOut.from_row(s, with_messages=with_messages) for s in steps.all()],
        workflow=wf,
    )


@router.post("/runs/{run_id}/signal", tags=["runs"])
async def signal_run(
    run_id: uuid.UUID, body: SignalBody, session: SessionDep, principal: Analyst, request: Request
) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if not run or run.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")
    ok = await request.app.state.runs.signal(
        run.case_id, body.signal, {**body.payload, "by": str(principal.sub)}
    )
    return {"delivered": ok}


@router.post("/runs/{run_id}/cancel", tags=["runs"])
async def cancel_run(
    run_id: uuid.UUID,
    session: SessionDep,
    principal: Analyst,
    request: Request,
    reason: str = "cancelled by user",
) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if not run or run.tenant_id != principal.tenant_id:
        raise NotFoundError("run not found")
    return {"delivered": await request.app.state.runs.cancel(run.case_id, reason)}


@router.get("/cases/{case_id}/run", response_model=RunDetail | None, tags=["runs"])
async def latest_run_for_case(
    case_id: uuid.UUID, session: SessionDep, principal: Viewer, request: Request
) -> RunDetail | None:
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.tenant_id == principal.tenant_id, AgentRun.case_id == case_id)
        .order_by(AgentRun.started_at.desc())
    )
    if not run:
        return None
    return await get_run(run.id, session, principal, request)


# ---------- prompts ----------
@router.get("/prompts", response_model=list[PromptOut], tags=["prompts"])
async def list_prompts(session: SessionDep, principal: Viewer) -> list[PromptOut]:
    return [_prompt_out(p) for p in await prompt_store.list_prompts(session)]


@router.post(
    "/prompts/{name}/versions", response_model=PromptOut, status_code=201, tags=["prompts"]
)
async def add_prompt_version(
    name: str, body: PromptVersionBody, session: SessionDep, principal: Manager
) -> PromptOut:
    v = await prompt_store.add_version(
        session,
        name,
        body.content,
        notes=body.notes,
        created_by=principal.email,
        activate=body.activate,
    )
    return _prompt_out(v)


@router.post("/prompts/{name}/activate/{version}", response_model=PromptOut, tags=["prompts"])
async def activate_prompt(
    name: str, version: int, session: SessionDep, principal: Manager
) -> PromptOut:
    return _prompt_out(await prompt_store.activate_version(session, name, version))


# ---------- model config ----------
def _mask(overrides: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tier, cfg in overrides.items():
        out[tier] = {k: ("••••" if k == "api_key" and v else v) for k, v in (cfg or {}).items()}
    return out


@router.get("/model-config", response_model=ModelConfigOut, tags=["models"])
async def get_model_config(
    session: SessionDep, principal: Viewer, request: Request
) -> ModelConfigOut:
    cfg = await session.get(ModelConfig, principal.tenant_id)
    overrides = cfg.overrides if cfg else {}
    factory = request.app.state.router_factory
    return ModelConfigOut(
        defaults=factory(None).describe(),
        overrides=_mask(overrides),
        effective=factory(overrides).describe(),
    )


@router.put("/model-config", response_model=ModelConfigOut, tags=["models"])
async def put_model_config(
    body: ModelConfigBody, session: SessionDep, principal: Manager, request: Request
) -> ModelConfigOut:
    cfg = await session.get(ModelConfig, principal.tenant_id)
    merged: dict[str, Any] = dict(cfg.overrides) if cfg else {}
    for tier, values in body.overrides.items():
        cur = dict(merged.get(tier, {}))
        for k, v in values.items():
            if k == "api_key" and v == "••••":
                continue  # unchanged masked value
            if v in (None, ""):
                cur.pop(k, None)
            else:
                cur[k] = v
        if cur:
            merged[tier] = cur
        else:
            merged.pop(tier, None)
    request.app.state.router_factory(
        merged
    )  # validates provider/model shape by constructing clients
    if cfg is None:
        cfg = ModelConfig(
            tenant_id=principal.tenant_id,
            overrides=merged,
            updated_by=principal.email,
            updated_at=utcnow(),
        )
        session.add(cfg)
    else:
        cfg.overrides, cfg.updated_by, cfg.updated_at = merged, principal.email, utcnow()
    factory = request.app.state.router_factory
    return ModelConfigOut(
        defaults=factory(None).describe(),
        overrides=_mask(merged),
        effective=factory(merged).describe(),
    )


# ---------- internal ----------
@internal.post("/runs", status_code=201)
async def internal_start(
    body: StartRunBody, tenant_id: uuid.UUID, request: Request
) -> dict[str, Any]:
    params = await request.app.state.runs.start(
        tenant_id=tenant_id, case_id=body.case_id, mode=body.mode, requested_by="internal"
    )
    return {"run_id": str(params.run_id), "workflow_id": f"case-{body.case_id}"}
