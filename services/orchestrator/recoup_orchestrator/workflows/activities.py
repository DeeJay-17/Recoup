"""Activities: everything with IO. Each persists its own agent_steps row + events."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from recoup_common.db import Database, utcnow
from recoup_common.events.outbox import enqueue_event
from recoup_common.events.topics import EventTypes
from recoup_common.logging import get_logger
from recoup_common.tracing import current_trace_id
from recoup_llm import ModelRouter, Usage
from temporalio import activity

from recoup_orchestrator.agents.loop import AgentSpec, ToolLoopAgent
from recoup_orchestrator.agents.schemas import (
    SPECIALISTS_AVAILABLE,
    Budget,
    CaseOutcome,
    CaseState,
    InvestigationOutput,
    StepSummary,
    SupervisorDecision,
    TriageOutput,
)
from recoup_orchestrator.agents.specs import SPECIALISTS, SUPERVISOR
from recoup_orchestrator.clients import CaseClient, ToolGatewayClient
from recoup_orchestrator.models import AgentRun, AgentStep, ModelConfig, Outbox
from recoup_orchestrator.prompt_store import active_bundle
from recoup_orchestrator.settings import Settings

log = get_logger(__name__)
SOURCE = "orchestrator"


# ---------- args / results (Pydantic; serialised by the pydantic data converter) ----------
class RunParams(BaseModel):
    run_id: uuid.UUID
    case_id: uuid.UUID
    tenant_id: uuid.UUID
    mode: str = "LIVE"
    max_steps: int = 12
    max_tokens: int = 150_000
    customer_wait_hours: int = 72
    approval_wait_days: int = 30
    requested_by: str = "system"


class StepResult(BaseModel):
    state: CaseState
    next: str
    reason: str | None = None
    wait_timeout_hours: int | None = None


class SpecialistArgs(BaseModel):
    state: CaseState
    specialist: str


class MergeArgs(BaseModel):
    state: CaseState
    signals: list[dict[str, Any]] = Field(default_factory=list)


class WaitArgs(BaseModel):
    state: CaseState
    reason: str
    hours: int | None = None


class FinalizeArgs(BaseModel):
    state: CaseState
    status: str
    reason: str


class Deps:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        router_factory: Any,
        gateway: ToolGatewayClient,
        cases: CaseClient,
    ) -> None:
        self.settings = settings
        self.db = db
        self.router_factory = router_factory  # (overrides) -> ModelRouter
        self.gateway = gateway
        self.cases = cases


class CaseActivities:
    def __init__(self, deps: Deps) -> None:
        self.d = deps

    # ---------- helpers ----------
    async def _router(self, tenant_id: uuid.UUID) -> ModelRouter:
        async with self.d.db.session() as session:
            cfg = await session.get(ModelConfig, tenant_id)
        return self.d.router_factory(cfg.overrides if cfg else None)  # type: ignore[no-any-return]

    async def _emit(
        self, session: Any, state: CaseState, event_type: str, data: dict[str, Any]
    ) -> None:
        enqueue_event(
            session,
            Outbox,
            source=SOURCE,
            tenant_id=state.tenant_id,
            aggregate_id=state.case_id,
            event_type=event_type,
            payload={"run_id": str(state.run_id), "case_id": str(state.case_id), **data},
        )

    async def _start_step(
        self, state: CaseState, agent: str, kind: str, input_summary: dict[str, Any]
    ) -> int:
        async with self.d.db.session() as session:
            row = AgentStep(
                run_id=state.run_id,
                tenant_id=state.tenant_id,
                case_id=state.case_id,
                step_no=state.step_no + 1,
                agent_name=agent,
                kind=kind,
                status="RUNNING",
                input_summary=input_summary,
                trace_id=current_trace_id(),
                started_at=utcnow(),
            )
            session.add(row)
            await session.flush()
            await self._emit(
                session,
                state,
                EventTypes.AGENT_STEP_STARTED,
                {"step_no": row.step_no, "agent": agent, "kind": kind},
            )
            run = await session.get(AgentRun, state.run_id)
            if run:
                run.phase, run.status, run.updated_at = f"{kind}:{agent}", "RUNNING", utcnow()
            return row.id

    async def _finish_step(
        self,
        step_id: int,
        state: CaseState,
        *,
        status: str,
        output: dict[str, Any] | None,
        usage: Usage,
        tool_calls: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None,
        provider: str,
        model: str,
        latency_ms: int,
        error: str | None,
        title: str,
    ) -> None:
        async with self.d.db.session() as session:
            row = await session.get(AgentStep, step_id)
            assert row is not None
            row.status, row.output, row.tool_calls, row.messages = (
                status,
                output,
                tool_calls,
                messages,
            )
            row.provider, row.model = provider, model
            row.tokens_in, row.tokens_out, row.cost_usd = (
                usage.input_tokens,
                usage.output_tokens,
                Decimal(str(usage.cost_usd)),
            )
            row.latency_ms, row.error, row.ended_at = latency_ms, error, utcnow()
            run = await session.get(AgentRun, state.run_id)
            if run:
                run.steps = state.step_no
                run.tokens_in, run.tokens_out, run.cost_usd = (
                    state.tokens_in,
                    state.tokens_out,
                    Decimal(str(state.cost_usd)),
                )
                run.updated_at = utcnow()
            await self._emit(
                session,
                state,
                EventTypes.AGENT_STEP_COMPLETED,
                {
                    "step_no": row.step_no,
                    "agent": row.agent_name,
                    "kind": row.kind,
                    "status": status,
                    "title": title,
                    "tokens": usage.input_tokens + usage.output_tokens,
                    "latency_ms": latency_ms,
                    "tool_calls": [t.get("tool") for t in tool_calls],
                },
            )
        try:
            await self.d.cases.append_event(
                state.tenant_id,
                state.case_id,
                {
                    "kind": "agent_step",
                    "actor_type": "agent",
                    "actor_id": f"agent:{row.agent_name}",
                    "title": title,
                    "payload": {
                        "run_id": str(state.run_id),
                        "step_no": row.step_no,
                        "status": status,
                        "output": output,
                        "tool_calls": tool_calls,
                        "tokens": usage.input_tokens + usage.output_tokens,
                        "provider": provider,
                        "model": model,
                        "error": error,
                    },
                },
            )
        except Exception as e:
            log.warning("orchestrator.timeline_failed", error=str(e))

    # ---------- activities ----------
    @activity.defn(name="load_case_state")
    async def load_case_state(self, p: RunParams) -> CaseState:
        case = await self.d.cases.get_case(p.tenant_id, p.case_id)
        router = await self._router(p.tenant_id)
        async with self.d.db.session() as session:
            bundle = {name: v.version for name, v in (await active_bundle(session)).items()}
            run = await session.get(AgentRun, p.run_id)
            if run is None:
                run = AgentRun(
                    id=p.run_id,
                    tenant_id=p.tenant_id,
                    case_id=p.case_id,
                    workflow_id=activity.info().workflow_id,
                    mode=p.mode,
                    status="RUNNING",
                    phase="loaded",
                    prompt_bundle=bundle,
                    model_config_=router.describe(),
                    started_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(run)
            state = CaseState(
                run_id=p.run_id,
                case_id=p.case_id,
                tenant_id=p.tenant_id,
                mode=p.mode,
                budget=Budget(max_steps=p.max_steps, max_tokens=p.max_tokens),
                case=case,
                prompt_bundle=bundle,
            )
            if case.get("root_cause"):
                state.history.append(
                    StepSummary(
                        step_no=0,
                        agent="case",
                        kind="context",
                        summary=(
                            f"Case already triaged as {case['root_cause']} "
                            f"({case.get('root_cause_conf')})"
                        ),
                    )
                )
            await self._emit(
                session,
                state,
                EventTypes.AGENT_RUN_STARTED,
                {"mode": p.mode, "requested_by": p.requested_by, "models": router.describe()},
            )
        return state

    @activity.defn(name="supervisor_step")
    async def supervisor_step(self, state: CaseState) -> StepResult:
        activity.heartbeat("supervisor")
        router = await self._router(state.tenant_id)
        step_id = await self._start_step(
            state, "supervisor", "supervisor", {"budget_used_pct": state.budget_used_pct()}
        )
        async with self.d.db.session() as session:
            prompts = await active_bundle(session)
        agent = ToolLoopAgent(
            SUPERVISOR,
            llm=router.for_tier("strong"),
            gateway=self.d.gateway,
            tenant_id=state.tenant_id,
            case_id=state.case_id,
            run_id=state.run_id,
            max_iterations=4,
            max_repairs=self.d.settings.max_repairs,
        )
        summary = state.summary_for_prompt()
        try:
            res = await agent.run(
                system_prompt=prompts["supervisor"].content,
                user_message=SUPERVISOR.task_instructions(summary),  # type: ignore[misc]
                manifest=[],
                thread_id=f"{state.run_id}:{state.step_no + 1}:supervisor",
            )
        except Exception as e:
            await self._finish_step(
                step_id,
                state,
                status="ERROR",
                output=None,
                usage=Usage(),
                tool_calls=[],
                messages=None,
                provider="",
                model="",
                latency_ms=0,
                error=str(e)[:2000],
                title=f"Supervisor failed: {type(e).__name__}",
            )
            raise
        decision: SupervisorDecision = res.output  # type: ignore[assignment]
        state = state.model_copy(deep=True)
        state.step_no += 1
        state.plan = decision.updated_plan
        state.last_decision = decision
        state.tokens_in += res.usage.input_tokens
        state.tokens_out += res.usage.output_tokens
        state.cost_usd = round(state.cost_usd + res.usage.cost_usd, 6)
        nxt, reason = decision.next, decision.escalation_reason
        # Guardrails the model cannot talk its way around:
        unavailable = nxt in ("Reconciler", "Negotiator", "Communicator")
        if (nxt in SPECIALISTS and nxt not in SPECIALISTS_AVAILABLE) or unavailable:
            nxt, reason = (
                "ESCALATED",
                f"specialist {decision.next} is not available in this deployment",
            )
        elif nxt in SPECIALISTS and state.specialist_runs.get(nxt, 0) >= 2:
            nxt, reason = "ESCALATED", f"loop detected: {decision.next} already ran twice"
        elif nxt == "RESOLVED" and not state.investigation and not state.case.get("root_cause"):
            nxt, reason = "ESCALATED", "cannot resolve without an investigation"
        if state.budget_used_pct() >= 100 and nxt not in ("RESOLVED", "ESCALATED"):
            nxt, reason = "ESCALATED", "STEP_OR_TOKEN_BUDGET_EXCEEDED"
        title = f"Supervisor → {nxt}: {decision.reasoning_summary[:160]}"
        state.history.append(
            StepSummary(
                step_no=state.step_no,
                agent="supervisor",
                kind="decision",
                summary=f"{nxt}: {decision.reasoning_summary[:200]}",
                tokens=res.usage.input_tokens + res.usage.output_tokens,
            )
        )
        await self._finish_step(
            step_id,
            state,
            status="SUCCESS",
            output={
                **decision.model_dump(mode="json"),
                "effective_next": nxt,
                "effective_reason": reason,
            },
            usage=res.usage,
            tool_calls=res.tool_log,
            messages=res.messages,
            provider=res.provider,
            model=res.model,
            latency_ms=res.latency_ms,
            error=None,
            title=title,
        )
        return StepResult(
            state=state, next=nxt, reason=reason, wait_timeout_hours=decision.wait_timeout_hours
        )

    @activity.defn(name="run_specialist")
    async def run_specialist(self, args: SpecialistArgs) -> CaseState:
        state, name = args.state, args.specialist
        spec: AgentSpec = SPECIALISTS[name]
        activity.heartbeat(name)
        router = await self._router(state.tenant_id)
        step_id = await self._start_step(state, spec.name, "specialist", {"specialist": name})
        async with self.d.db.session() as session:
            prompts = await active_bundle(session)
        manifest = await self.d.gateway.manifest(state.tenant_id, state.case_id)
        agent = ToolLoopAgent(
            spec,
            llm=router.for_tier("fast" if spec.tier == "fast" else "strong"),
            gateway=self.d.gateway,
            tenant_id=state.tenant_id,
            case_id=state.case_id,
            run_id=state.run_id,
            max_iterations=self.d.settings.max_agent_iterations,
            max_repairs=self.d.settings.max_repairs,
        )
        summary = state.summary_for_prompt()
        try:
            res = await agent.run(
                system_prompt=prompts[spec.prompt_name].content,
                user_message=spec.task_instructions(summary),  # type: ignore[misc]
                manifest=manifest,
                thread_id=f"{state.run_id}:{state.step_no + 1}:{spec.name}",
            )
        except Exception as e:
            await self._finish_step(
                step_id,
                state,
                status="ERROR",
                output=None,
                usage=Usage(),
                tool_calls=[],
                messages=None,
                provider="",
                model="",
                latency_ms=0,
                error=str(e)[:2000],
                title=f"{name} failed: {type(e).__name__}",
            )
            raise
        state = state.model_copy(deep=True)
        state.step_no += 1
        state.specialist_runs[name] = state.specialist_runs.get(name, 0) + 1
        state.tokens_in += res.usage.input_tokens
        state.tokens_out += res.usage.output_tokens
        state.cost_usd = round(state.cost_usd + res.usage.cost_usd, 6)
        title = await self._apply_specialist_output(state, name, res.output)
        state.history.append(
            StepSummary(
                step_no=state.step_no,
                agent=spec.name,
                kind="specialist",
                summary=title[:200],
                tokens=res.usage.input_tokens + res.usage.output_tokens,
            )
        )
        await self._finish_step(
            step_id,
            state,
            status="SUCCESS",
            output=res.output.model_dump(mode="json"),
            usage=res.usage,
            tool_calls=res.tool_log,
            messages=res.messages,
            provider=res.provider,
            model=res.model,
            latency_ms=res.latency_ms,
            error=None,
            title=title,
        )
        return state

    async def _apply_specialist_output(self, state: CaseState, name: str, output: Any) -> str:
        """Deterministic side effects of a specialist result (the model never writes state)."""
        if name == "Triage":
            t: TriageOutput = output
            state.triage = t
            top = t.top
            case = await self.d.cases.triage(
                state.tenant_id,
                state.case_id,
                {
                    "root_cause": top.cause,
                    "root_cause_conf": str(round(top.confidence, 3)),
                    "priority": t.priority,
                    "summary": t.summary,
                    "actor_id": "agent:triage",
                },
            )
            state.case = case
            return f"Triage: {top.cause} ({top.confidence:.0%}), path {t.recommended_path}"
        if name == "Investigator":
            inv: InvestigationOutput = output
            state.investigation = inv
            case = await self.d.cases.triage(
                state.tenant_id,
                state.case_id,
                {
                    "root_cause": inv.confirmed_cause,
                    "root_cause_conf": str(round(inv.confidence, 3)),
                    "summary": inv.summary,
                    "actor_id": "agent:investigator",
                },
            )
            state.case = case
            credit = f", credit {inv.proposed_credit_memo}" if inv.proposed_credit_memo else ""
            gaps = f", gaps: {len(inv.gaps)}" if inv.gaps else ""
            return f"Investigator: {inv.confirmed_cause} ({inv.confidence:.0%}){credit}{gaps}"
        return f"{name} completed"

    @activity.defn(name="merge_signals")
    async def merge_signals(self, args: MergeArgs) -> CaseState:
        state = args.state.model_copy(deep=True)
        for s in args.signals:
            state.signals.append(s)
            state.history.append(
                StepSummary(
                    step_no=state.step_no,
                    agent="workflow",
                    kind="signal",
                    summary=f"signal: {s.get('type')} {str(s)[:150]}",
                )
            )
        if args.signals:
            state.case = await self.d.cases.get_case(state.tenant_id, state.case_id)
        async with self.d.db.session() as session:
            run = await session.get(AgentRun, state.run_id)
            if run:
                run.status, run.phase, run.updated_at = "RUNNING", "resumed", utcnow()
        return state

    @activity.defn(name="record_wait")
    async def record_wait(self, args: WaitArgs) -> None:
        state = args.state
        async with self.d.db.session() as session:
            run = await session.get(AgentRun, state.run_id)
            if run:
                run.status, run.phase, run.updated_at = "WAITING", args.reason, utcnow()
            await self._emit(
                session,
                state,
                EventTypes.AGENT_RUN_WAITING,
                {"reason": args.reason, "hours": args.hours},
            )
        try:
            await self.d.cases.append_event(
                state.tenant_id,
                state.case_id,
                {
                    "kind": "agent_wait",
                    "actor_type": "agent",
                    "actor_id": "agent:supervisor",
                    "title": f"Waiting: {args.reason}"
                    + (f" (up to {args.hours}h)" if args.hours else ""),
                    "payload": {"run_id": str(state.run_id)},
                },
            )
        except Exception as e:
            log.warning("orchestrator.timeline_failed", error=str(e))

    @activity.defn(name="finalize_case")
    async def finalize_case(self, args: FinalizeArgs) -> CaseOutcome:
        state, status, reason = args.state, args.status, args.reason
        inv, tri = state.investigation, state.triage
        cause = (
            inv.confirmed_cause if inv else (tri.top.cause if tri else state.case.get("root_cause"))
        )
        conf = inv.confidence if inv else (tri.top.confidence if tri else None)
        if status == "ESCALATED":
            brief = _escalation_brief(state, reason)
            env = await self.d.gateway.invoke(
                "escalate_case",
                tenant_id=state.tenant_id,
                case_id=state.case_id,
                run_id=state.run_id,
                actor="agent:supervisor",
                args={"reason": reason[:1000], "summary": brief[:3000]},
                idempotency_key=f"escalate-{state.run_id}",
            )
            if env.get("status") not in ("SUCCESS",):
                log.warning("orchestrator.escalate_blocked", detail=env)
                # Fall back to a timeline note so the human still sees the brief.
                try:
                    await self.d.cases.append_event(
                        state.tenant_id,
                        state.case_id,
                        {
                            "kind": "escalation_brief",
                            "actor_type": "agent",
                            "actor_id": "agent:supervisor",
                            "title": f"Escalation requested: {reason[:150]}",
                            "payload": {"summary": brief},
                        },
                    )
                except Exception as e:
                    log.warning("orchestrator.timeline_failed", error=str(e))
        elif status == "RESOLVED":
            try:
                await self.d.cases.transition(
                    state.tenant_id, state.case_id, "RESOLVED", reason, "agent:supervisor"
                )
            except Exception as e:
                log.warning("orchestrator.resolve_failed", error=str(e))
                status, reason = "ESCALATED", f"could not resolve: {e}"
        outcome = CaseOutcome(
            run_id=state.run_id,
            case_id=state.case_id,
            status=status,
            reason=reason,
            steps=state.step_no,  # type: ignore[arg-type]
            tokens_in=state.tokens_in,
            tokens_out=state.tokens_out,
            cost_usd=state.cost_usd,
            root_cause=cause,
            confidence=conf,
        )
        async with self.d.db.session() as session:
            run = await session.get(AgentRun, state.run_id)
            if run:
                run.status = {
                    "RESOLVED": "COMPLETED",
                    "ESCALATED": "ESCALATED",
                    "FAILED": "FAILED",
                    "CANCELLED": "CANCELLED",
                }[status]
                run.phase, run.outcome, run.ended_at, run.updated_at = (
                    "done",
                    outcome.model_dump(mode="json"),
                    utcnow(),
                    utcnow(),
                )
                run.steps, run.tokens_in, run.tokens_out, run.cost_usd = (
                    state.step_no,
                    state.tokens_in,
                    state.tokens_out,
                    Decimal(str(state.cost_usd)),
                )
            await self._emit(
                session, state, EventTypes.AGENT_RUN_COMPLETED, outcome.model_dump(mode="json")
            )
        return outcome


def _escalation_brief(state: CaseState, reason: str) -> str:
    tri, inv = state.triage, state.investigation
    lines = [f"Why escalated: {reason}", ""]
    if inv:
        lines += [
            f"Confirmed root cause: {inv.confirmed_cause} (confidence {inv.confidence:.0%})",
            inv.summary,
            "",
        ]
        if inv.evidence:
            lines.append("Evidence:")
            lines += [f"- [{e.source}] {e.ref}: {e.finding}" for e in inv.evidence[:10]]
        if inv.proposed_credit_memo is not None:
            lines.append(
                f"Reconciled credit due: {inv.proposed_credit_memo}"
                + (" (rebill required)" if inv.rebill_required else "")
            )
        if inv.gaps:
            lines.append("Open questions / gaps: " + "; ".join(inv.gaps))
    elif tri:
        lines += [
            "Triage hypotheses: "
            + ", ".join(f"{h.cause} {h.confidence:.0%}" for h in tri.root_cause_hypotheses),
            tri.summary,
        ]
    lines += ["", "What was tried: " + "; ".join(h.summary for h in state.history[-6:])]
    rec = (
        "Issue the reconciled credit memo and notify the customer."
        if inv and inv.proposed_credit_memo
        else "Contact the customer's active AP contact."
        if (inv and inv.confirmed_cause in ("MISSING_PO", "WRONG_CONTACT"))
        else "Review the evidence and decide next outreach."
    )
    lines.append(f"Recommended human action: {rec}")
    return "\n".join(lines)


def customer_wait(hours: int) -> timedelta:
    return timedelta(hours=hours)
