from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database
from recoup_common.errors import NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_evals.models import EvalCase, EvalDataset, EvalResult, EvalRun
from recoup_evals.runner import Harness
from recoup_evals.scoring import compare

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Viewer = Annotated[Principal, Depends(get_principal)]
Manager = Annotated[Principal, Depends(require_role(Role.MANAGER))]


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    description: str
    case_count: int
    spec: dict[str, Any]
    created_at: str


class DatasetBuild(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    kind: str = Field(default="golden", pattern=r"^(golden|redteam)$")
    size: int = Field(default=100, ge=1, le=1000)
    overdue_days: int = Field(default=7, ge=0, le=365)
    scenarios: list[str] | None = None
    description: str = ""


class EvalCaseOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    invoice_ref: str
    customer_ref: str
    scenario: str
    expected_root_cause: str
    expected_credit_memo: float | None
    expected_final_state: str | None


class RunStart(BaseModel):
    dataset_id: uuid.UUID
    label: str = Field(default="manual", max_length=120)
    judge: bool = False
    concurrency: int | None = Field(default=None, ge=1, le=16)
    limit: int | None = Field(default=None, ge=1, le=1000)


class RunOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    label: str
    status: str
    judge: bool
    concurrency: int
    prompt_bundle: dict[str, Any]
    models: dict[str, Any]
    metrics: dict[str, Any]
    cases_total: int
    cases_done: int
    error: str | None
    started_at: str
    ended_at: str | None


class ResultOut(BaseModel):
    id: int
    eval_case_id: uuid.UUID
    agent_run_id: uuid.UUID | None
    status: str
    passed: bool
    expected_root_cause: str | None
    triage_root_cause: str | None
    predicted_root_cause: str | None
    expected_credit_memo: float | None
    predicted_credit_memo: float | None
    credit_delta: float | None
    terminal_status: str | None
    steps: int
    tool_calls: int
    policy_denials: int
    approval_gates: int
    unauthorized_mutations: int
    tokens: int
    cost_usd: float
    latency_ms: int | None
    scores: dict[str, Any]
    failures: list[Any]
    detail: dict[str, Any]


class RunDetailOut(BaseModel):
    run: RunOut
    dataset: DatasetOut
    results: list[ResultOut]


def _ds(d: EvalDataset) -> DatasetOut:
    return DatasetOut(
        id=d.id,
        name=d.name,
        kind=d.kind,
        description=d.description,
        case_count=d.case_count,
        spec=d.spec,
        created_at=d.created_at.isoformat(),
    )


def _run(r: EvalRun) -> RunOut:
    return RunOut(
        id=r.id,
        dataset_id=r.dataset_id,
        label=r.label,
        status=r.status,
        judge=r.judge,
        concurrency=r.concurrency,
        prompt_bundle=r.prompt_bundle,
        models=r.models,
        metrics=r.metrics,
        cases_total=r.cases_total,
        cases_done=r.cases_done,
        error=r.error,
        started_at=r.started_at.isoformat(),
        ended_at=r.ended_at.isoformat() if r.ended_at else None,
    )


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _res(r: EvalResult) -> ResultOut:
    f = _f
    return ResultOut(
        id=r.id,
        eval_case_id=r.eval_case_id,
        agent_run_id=r.agent_run_id,
        status=r.status,
        passed=r.passed,
        expected_root_cause=r.expected_root_cause,
        triage_root_cause=r.triage_root_cause,
        predicted_root_cause=r.predicted_root_cause,
        expected_credit_memo=f(r.expected_credit_memo),
        predicted_credit_memo=f(r.predicted_credit_memo),
        credit_delta=f(r.credit_delta),
        terminal_status=r.terminal_status,
        steps=r.steps,
        tool_calls=r.tool_calls,
        policy_denials=r.policy_denials,
        approval_gates=r.approval_gates,
        unauthorized_mutations=r.unauthorized_mutations,
        tokens=r.tokens,
        cost_usd=float(r.cost_usd),
        latency_ms=r.latency_ms,
        scores=r.scores,
        failures=r.failures,
        detail=r.detail,
    )


async def _detail(session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunDetailOut:
    run = await session.get(EvalRun, run_id)
    if not run or run.tenant_id != tenant_id:
        raise NotFoundError("eval run not found")
    ds = await session.get(EvalDataset, run.dataset_id)
    rows = await session.scalars(
        select(EvalResult).where(EvalResult.run_id == run_id).order_by(EvalResult.id)
    )
    assert ds is not None
    return RunDetailOut(run=_run(run), dataset=_ds(ds), results=[_res(r) for r in rows.all()])


# ---------- datasets ----------
@router.post("/datasets/build", response_model=DatasetOut, status_code=201, tags=["datasets"])
async def build_dataset(body: DatasetBuild, principal: Manager, request: Request) -> DatasetOut:
    h: Harness = request.app.state.harness
    ds = await h.build_dataset(
        principal.tenant_id,
        name=body.name,
        kind=body.kind,
        size=body.size,
        overdue_days=body.overdue_days,
        scenarios=body.scenarios,
        description=body.description,
    )
    return _ds(ds)


@router.get("/datasets", response_model=list[DatasetOut], tags=["datasets"])
async def list_datasets(session: SessionDep, principal: Viewer) -> list[DatasetOut]:
    rows = await session.scalars(
        select(EvalDataset)
        .where(EvalDataset.tenant_id == principal.tenant_id)
        .order_by(EvalDataset.created_at.desc())
    )
    return [_ds(d) for d in rows.all()]


@router.get("/datasets/{dataset_id}/cases", response_model=list[EvalCaseOut], tags=["datasets"])
async def dataset_cases(
    dataset_id: uuid.UUID, session: SessionDep, principal: Viewer
) -> list[EvalCaseOut]:
    rows = await session.scalars(
        select(EvalCase)
        .where(EvalCase.dataset_id == dataset_id, EvalCase.tenant_id == principal.tenant_id)
        .order_by(EvalCase.invoice_ref)
    )
    return [
        EvalCaseOut(
            id=c.id,
            case_id=c.case_id,
            invoice_ref=c.invoice_ref,
            customer_ref=c.customer_ref,
            scenario=c.scenario,
            expected_root_cause=c.expected_root_cause,
            expected_credit_memo=float(c.expected_credit_memo)
            if c.expected_credit_memo is not None
            else None,
            expected_final_state=c.expected_final_state,
        )
        for c in rows.all()
    ]


# ---------- runs ----------
@router.post("/runs", response_model=RunOut, status_code=201, tags=["runs"])
async def start_run(body: RunStart, principal: Manager, request: Request) -> RunOut:
    h: Harness = request.app.state.harness
    run = await h.start_run(
        principal.tenant_id,
        dataset_id=body.dataset_id,
        label=body.label,
        judge=body.judge,
        concurrency=body.concurrency,
        limit=body.limit,
    )
    return _run(run)


@router.get("/runs", response_model=list[RunOut], tags=["runs"])
async def list_runs(
    session: SessionDep, principal: Viewer, limit: int = Query(default=50, ge=1, le=200)
) -> list[RunOut]:
    rows = await session.scalars(
        select(EvalRun)
        .where(EvalRun.tenant_id == principal.tenant_id)
        .order_by(EvalRun.started_at.desc())
        .limit(limit)
    )
    return [_run(r) for r in rows.all()]


@router.get("/runs/{run_id}", response_model=RunDetailOut, tags=["runs"])
async def get_run(run_id: uuid.UUID, session: SessionDep, principal: Viewer) -> RunDetailOut:
    return await _detail(session, principal.tenant_id, run_id)


@router.post("/runs/{run_id}/cancel", tags=["runs"])
async def cancel_run(run_id: uuid.UUID, principal: Manager, request: Request) -> dict[str, bool]:
    h: Harness = request.app.state.harness
    return {"cancelled": await h.cancel(run_id)}


@router.get("/compare", tags=["runs"])
async def compare_runs(
    session: SessionDep, principal: Viewer, a: uuid.UUID, b: uuid.UUID
) -> dict[str, Any]:
    ra, rb = await session.get(EvalRun, a), await session.get(EvalRun, b)
    if (
        not ra
        or not rb
        or ra.tenant_id != principal.tenant_id
        or rb.tenant_id != principal.tenant_id
    ):
        raise NotFoundError("run not found")
    return {
        "a": {"id": str(ra.id), "label": ra.label, "models": ra.models, "metrics": ra.metrics},
        "b": {"id": str(rb.id), "label": rb.label, "models": rb.models, "metrics": rb.metrics},
        "delta": compare(ra.metrics, rb.metrics),
    }


# ---------- internal (CI) ----------
@internal.post("/datasets/build", response_model=DatasetOut, status_code=201)
async def build_internal(body: DatasetBuild, tenant_id: uuid.UUID, request: Request) -> DatasetOut:
    h: Harness = request.app.state.harness
    ds = await h.build_dataset(
        tenant_id,
        name=body.name,
        kind=body.kind,
        size=body.size,
        overdue_days=body.overdue_days,
        scenarios=body.scenarios,
        description=body.description,
    )
    return _ds(ds)


@internal.get("/datasets", response_model=list[DatasetOut])
async def list_datasets_internal(tenant_id: uuid.UUID, session: SessionDep) -> list[DatasetOut]:
    rows = await session.scalars(
        select(EvalDataset)
        .where(EvalDataset.tenant_id == tenant_id)
        .order_by(EvalDataset.created_at.desc())
    )
    return [_ds(d) for d in rows.all()]


@internal.post("/runs", response_model=RunOut, status_code=201)
async def start_internal(body: RunStart, tenant_id: uuid.UUID, request: Request) -> RunOut:
    h: Harness = request.app.state.harness
    run = await h.start_run(
        tenant_id,
        dataset_id=body.dataset_id,
        label=body.label,
        judge=body.judge,
        concurrency=body.concurrency,
        limit=body.limit,
    )
    return _run(run)


@internal.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_internal(
    run_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> RunDetailOut:
    return await _detail(session, tenant_id, run_id)


@internal.get("/runs", response_model=list[RunOut])
async def list_runs_internal(
    tenant_id: uuid.UUID, session: SessionDep, limit: int = Query(default=50, ge=1, le=200)
) -> list[RunOut]:
    rows = await session.scalars(
        select(EvalRun)
        .where(EvalRun.tenant_id == tenant_id)
        .order_by(EvalRun.started_at.desc())
        .limit(limit)
    )
    return [_run(r) for r in rows.all()]
