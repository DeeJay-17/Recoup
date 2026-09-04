"""Builds golden datasets from Mock ERP ground truth and replays them through the agents in
shadow mode, scoring each case."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from decimal import Decimal
from typing import Any

from recoup_common.db import Database, utcnow
from recoup_common.errors import ConflictError, NotFoundError, ValidationError
from recoup_common.logging import get_logger
from recoup_llm import ModelRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_evals import judge as judging
from recoup_evals import redteam
from recoup_evals.clients import CaseClient, ErpClient, OrchestratorClient, ToolGatewayClient
from recoup_evals.models import EvalCase, EvalDataset, EvalResult, EvalRun
from recoup_evals.scoring import aggregate, score_case, to_decimal
from recoup_evals.settings import Settings

log = get_logger(__name__)
TERMINAL = {"COMPLETED", "ESCALATED", "FAILED", "CANCELLED"}


class Harness:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        router: ModelRouter,
        erp: ErpClient,
        cases: CaseClient,
        orch: OrchestratorClient,
        gateway: ToolGatewayClient,
    ) -> None:
        self.db, self.s, self.router = db, settings, router
        self.erp, self.cases, self.orch, self.gateway = erp, cases, orch, gateway
        self.tasks: dict[str, asyncio.Task[None]] = {}

    # ---------- datasets ----------
    async def build_dataset(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        kind: str = "golden",
        size: int = 100,
        overdue_days: int = 7,
        scenarios: list[str] | None = None,
        description: str = "",
    ) -> EvalDataset:
        """One eval case per overdue invoice that has ground truth and an open case, balanced
        across scenarios so no single root cause dominates the score."""
        invoices = await self.erp.overdue_invoices(days=overdue_days, limit=max(size * 6, 300))
        buckets: dict[str, list[dict[str, Any]]] = {}
        for inv in invoices:
            gt = await self.erp.ground_truth(inv["invoice_ref"])
            if not gt or gt.get("root_cause") in (None, "NONE"):
                continue
            if scenarios and gt["root_cause"] not in scenarios:
                continue
            case = await self.cases.by_invoice(tenant_id, inv["invoice_ref"])
            if not case:
                continue
            buckets.setdefault(gt["root_cause"], []).append(
                {"invoice": inv, "gt": gt, "case": case}
            )
        picked: list[dict[str, Any]] = []
        order = sorted(buckets)
        i = 0
        while len(picked) < size and any(buckets[c] for c in order):
            c = order[i % len(order)]
            if buckets[c]:
                picked.append(buckets[c].pop(0))
            i += 1
        if not picked:
            raise ValidationError("no eligible cases: seed the Mock ERP and run ingestion first")
        async with self.db.session() as session:
            existing = await session.scalar(
                select(EvalDataset).where(
                    EvalDataset.tenant_id == tenant_id, EvalDataset.name == name
                )
            )
            if existing:
                raise ConflictError(f"dataset '{name}' already exists")
            ds = EvalDataset(
                tenant_id=tenant_id,
                name=name,
                kind=kind,
                description=description,
                case_count=len(picked),
                created_at=utcnow(),
                spec={"size": size, "overdue_days": overdue_days, "scenarios": scenarios or "all"},
            )
            session.add(ds)
            await session.flush()
            for p in picked:
                gt, inv, case = p["gt"], p["invoice"], p["case"]
                session.add(
                    EvalCase(
                        dataset_id=ds.id,
                        tenant_id=tenant_id,
                        case_id=uuid.UUID(case["id"]),
                        invoice_ref=inv["invoice_ref"],
                        customer_ref=inv["customer_ref"],
                        scenario=gt.get("scenario") or gt["root_cause"],
                        expected_root_cause=gt["root_cause"],
                        expected_credit_memo=to_decimal(gt.get("expected_credit_memo"))
                        or Decimal("0"),
                        expected_final_state=gt.get("expected_final_state"),
                        ground_truth=gt,
                    )
                )
            return ds

    # ---------- runs ----------
    async def start_run(
        self,
        tenant_id: uuid.UUID,
        *,
        dataset_id: uuid.UUID,
        label: str,
        judge: bool,
        concurrency: int | None,
        limit: int | None,
    ) -> EvalRun:
        async with self.db.session() as session:
            ds = await session.get(EvalDataset, dataset_id)
            if not ds or ds.tenant_id != tenant_id:
                raise NotFoundError("dataset not found")
            n = await self._case_count(session, dataset_id, limit)
            run = EvalRun(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                label=label,
                status="RUNNING",
                judge=judge,
                concurrency=concurrency or self.s.run_concurrency,
                models=self.router.describe(),
                cases_total=n,
                started_at=utcnow(),
            )
            session.add(run)
            await session.flush()
            run_id = run.id
        self.tasks[str(run_id)] = asyncio.create_task(
            self._run(run_id, limit), name=f"eval-{run_id}"
        )
        return run

    async def _case_count(
        self, session: AsyncSession, dataset_id: uuid.UUID, limit: int | None
    ) -> int:
        rows = (
            await session.scalars(select(EvalCase.id).where(EvalCase.dataset_id == dataset_id))
        ).all()
        return min(len(rows), limit) if limit else len(rows)

    async def _run(self, run_id: uuid.UUID, limit: int | None) -> None:
        try:
            async with self.db.session() as session:
                run = await session.get(EvalRun, run_id)
                assert run is not None
                dataset = await session.get(EvalDataset, run.dataset_id)
                assert dataset is not None
                cases = list(
                    (
                        await session.scalars(
                            select(EvalCase)
                            .where(EvalCase.dataset_id == run.dataset_id)
                            .order_by(EvalCase.invoice_ref)
                        )
                    ).all()
                )
                tenant_id, judge, concurrency, kind = (
                    run.tenant_id,
                    run.judge,
                    run.concurrency,
                    dataset.kind,
                )
            if limit:
                cases = cases[:limit]
            if kind == "redteam":
                await self._run_redteam(run_id, tenant_id, cases)
            else:
                sem = asyncio.Semaphore(concurrency)

                async def one(ec: EvalCase) -> None:
                    async with sem:
                        await self._score_one(run_id, tenant_id, ec, judge)

                await asyncio.gather(*(one(ec) for ec in cases))
            await self._finish(run_id)
        except asyncio.CancelledError:
            await self._finish(run_id, status="CANCELLED")
            raise
        except Exception as e:
            log.exception("eval.run_failed", run_id=str(run_id))
            await self._finish(run_id, status="FAILED", error=str(e)[:2000])

    async def _score_one(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID, ec: EvalCase, judge: bool
    ) -> None:
        t0 = time.perf_counter()
        agent_run_id: str | None = None
        try:
            if ec.case_id is None:
                raise ValidationError("eval case has no linked case")
            agent_run_id = await self.orch.start(tenant_id, ec.case_id, mode="SHADOW")
            detail = await self._await_run(tenant_id, agent_run_id)
            invocations = await self.gateway.invocations(tenant_id, agent_run_id)
            score = score_case(
                expected_root_cause=ec.expected_root_cause,
                expected_credit=ec.expected_credit_memo,
                run=detail["run"],
                steps=detail["steps"],
                invocations=invocations,
            )
            scores: dict[str, Any] = {}
            if judge:
                j = await judging.judge_run(self.router.for_tier("strong"), detail["steps"])
                if j:
                    scores = {
                        "faithfulness": j.faithfulness,
                        "faithfulness_reason": j.faithfulness_reason,
                        "email_quality": j.email_quality,
                        "email_quality_reason": j.email_quality_reason,
                        "unsupported_claims": j.unsupported_claims,
                    }
            await self._store(
                run_id,
                ec,
                agent_run_id,
                score.__dict__ | {"scores": scores},
                latency_override=score.latency_ms or int((time.perf_counter() - t0) * 1000),
            )
        except Exception as e:
            log.warning("eval.case_failed", invoice=ec.invoice_ref, error=str(e)[:300])
            await self._store_error(run_id, ec, agent_run_id, str(e)[:500])

    async def _await_run(self, tenant_id: uuid.UUID, agent_run_id: str) -> dict[str, Any]:
        """Poll until the shadow run finishes. A run that parks on a customer or approval wait has
        gone as far as it can without a real human, so we cancel it and score what it produced."""
        deadline = time.monotonic() + self.s.case_timeout_seconds
        waiting_since: float | None = None
        while time.monotonic() < deadline:
            detail = await self.orch.run(tenant_id, agent_run_id, with_messages=True)
            if detail is None:  # the workflow has not reached its first activity yet
                await asyncio.sleep(self.s.poll_interval_seconds)
                continue
            status = detail["run"]["status"]
            if status in TERMINAL:
                return detail
            if status == "WAITING":
                waiting_since = waiting_since or time.monotonic()
                if time.monotonic() - waiting_since > 10:
                    with contextlib.suppress(Exception):
                        await self.orch.c.post(
                            f"/internal/runs/{agent_run_id}/cancel",
                            params={
                                "tenant_id": str(tenant_id),
                                "reason": "eval: shadow run parked on a wait",
                            },
                        )
                    await asyncio.sleep(self.s.poll_interval_seconds)
                    final = await self.orch.run(tenant_id, agent_run_id, with_messages=True)
                    if final is not None:
                        return final
            await asyncio.sleep(self.s.poll_interval_seconds)
        raise TimeoutError(
            f"shadow run {agent_run_id} did not finish in {self.s.case_timeout_seconds}s"
        )

    async def _store(
        self,
        run_id: uuid.UUID,
        ec: EvalCase,
        agent_run_id: str | None,
        d: dict[str, Any],
        *,
        latency_override: int | None,
    ) -> None:
        async with self.db.session() as session:
            session.add(
                EvalResult(
                    run_id=run_id,
                    eval_case_id=ec.id,
                    agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                    status=d["status"],
                    passed=bool(d["passed"]),
                    expected_root_cause=d.get("expected_root_cause"),
                    triage_root_cause=d.get("triage_root_cause"),
                    predicted_root_cause=d.get("predicted_root_cause"),
                    expected_credit_memo=d.get("expected_credit_memo"),
                    predicted_credit_memo=d.get("predicted_credit_memo"),
                    credit_delta=d.get("credit_delta"),
                    terminal_status=d.get("terminal_status"),
                    steps=int(d.get("steps") or 0),
                    tool_calls=int(d.get("tool_calls") or 0),
                    policy_denials=int(d.get("policy_denials") or 0),
                    approval_gates=int(d.get("approval_gates") or 0),
                    unauthorized_mutations=int(d.get("unauthorized_mutations") or 0),
                    tokens=int(d.get("tokens") or 0),
                    cost_usd=d.get("cost_usd") or Decimal("0"),
                    latency_ms=latency_override,
                    scores=d.get("scores") or {},
                    failures=list(d.get("failures") or []),
                    detail=dict(d.get("detail") or {}),
                    created_at=utcnow(),
                )
            )
            run = await session.get(EvalRun, run_id)
            if run:
                run.cases_done += 1

    async def _store_error(
        self, run_id: uuid.UUID, ec: EvalCase, agent_run_id: str | None, error: str
    ) -> None:
        async with self.db.session() as session:
            session.add(
                EvalResult(
                    run_id=run_id,
                    eval_case_id=ec.id,
                    agent_run_id=uuid.UUID(agent_run_id) if agent_run_id else None,
                    status="ERROR",
                    passed=False,
                    expected_root_cause=ec.expected_root_cause,
                    expected_credit_memo=ec.expected_credit_memo,
                    failures=[error],
                    detail={"error": error},
                    created_at=utcnow(),
                )
            )
            run = await session.get(EvalRun, run_id)
            if run:
                run.cases_done += 1

    async def _run_redteam(
        self, run_id: uuid.UUID, tenant_id: uuid.UUID, cases: list[EvalCase]
    ) -> None:
        ec = cases[0]
        invoice = await self.erp.invoice(ec.invoice_ref)
        customer = await self.erp.customer(ec.customer_ref)
        if not invoice or not customer or ec.case_id is None:
            raise ValidationError("red-team dataset needs an invoice, a customer and a linked case")
        hold = None
        for inv in await self.erp.overdue_invoices(days=1, limit=200):
            c = await self.erp.customer(inv["customer_ref"])
            if c and c.get("credit_hold"):
                hold = c
                break
        probes = await redteam.build_probes(self.erp, invoice, customer, hold)
        results = await redteam.run_probes(
            self.gateway,
            self.erp,
            tenant_id=tenant_id,
            case_id=ec.case_id,
            invoice_ref=ec.invoice_ref,
            probes=probes,
        )
        async with self.db.session() as session:
            for r in results:
                session.add(
                    EvalResult(
                        run_id=run_id,
                        eval_case_id=ec.id,
                        status="PROBE",
                        passed=r.passed,
                        unauthorized_mutations=0 if r.passed else 1,
                        failures=[] if r.passed else [f"{r.name}: {r.detail}"],
                        detail={
                            "probe": r.name,
                            "description": r.description,
                            "detail": r.detail,
                            "envelope": r.envelope,
                            "mutated": r.mutated,
                        },
                        created_at=utcnow(),
                    )
                )
            run = await session.get(EvalRun, run_id)
            if run:
                run.cases_done = len(results)
                run.cases_total = len(results)

    async def _finish(
        self, run_id: uuid.UUID, *, status: str = "COMPLETED", error: str | None = None
    ) -> None:
        async with self.db.session() as session:
            run = await session.get(EvalRun, run_id)
            if not run:
                return
            rows = list(
                (await session.scalars(select(EvalResult).where(EvalResult.run_id == run_id))).all()
            )
            results = [
                {
                    "status": r.status,
                    "passed": r.passed,
                    "expected_root_cause": r.expected_root_cause,
                    "triage_root_cause": r.triage_root_cause,
                    "predicted_root_cause": r.predicted_root_cause,
                    "credit_delta": r.credit_delta,
                    "steps": r.steps,
                    "tool_calls": r.tool_calls,
                    "policy_denials": r.policy_denials,
                    "approval_gates": r.approval_gates,
                    "unauthorized_mutations": r.unauthorized_mutations,
                    "tokens": r.tokens,
                    "cost_usd": r.cost_usd,
                    "latency_ms": r.latency_ms,
                    "scores": r.scores,
                }
                for r in rows
            ]
            run.metrics = aggregate(results)
            run.status, run.error, run.ended_at = status, error, utcnow()
        self.tasks.pop(str(run_id), None)
        log.info("eval.run_finished", run_id=str(run_id), status=status)

    async def cancel(self, run_id: uuid.UUID) -> bool:
        task = self.tasks.get(str(run_id))
        if not task:
            return False
        task.cancel()
        return True

    async def stop(self) -> None:
        for task in list(self.tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
