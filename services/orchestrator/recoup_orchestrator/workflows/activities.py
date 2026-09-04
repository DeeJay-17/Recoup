"""Activities: everything with IO. Each persists its own agent_steps row + events."""

from __future__ import annotations

import json
import time
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
    CommunicatorOutput,
    CustomerIntent,
    InvestigationOutput,
    NegotiatorOutput,
    ProposedActionRef,
    ReconcilerOutput,
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


class ExecuteArgs(BaseModel):
    state: CaseState
    action_id: str


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
        detail = await self.d.cases.get_detail(p.tenant_id, p.case_id)
        case = detail["case"]
        router = await self._router(p.tenant_id)
        # Proposals from earlier runs stay authoritative: a re-run must not re-propose them.
        prior_actions = [
            ProposedActionRef(
                action_id=a["id"],
                action_type=a["action_type"],
                policy_decision=a["policy_decision"],
                required_role=a.get("required_role"),
                status="EXECUTED"
                if a.get("executed_at") or a["status"] == "AUTO_EXECUTED"
                else a["status"],
                proposed_by=a.get("proposed_by", "agent"),
                summary=f"from earlier run: {a['action_type']}",
                execution_result=a.get("execution_result"),
            )
            for a in detail["actions"]
            if a["status"] not in ("REJECTED", "DENIED")
        ]
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
                actions=prior_actions,
                outreach_count=sum(
                    1
                    for a in prior_actions
                    if a.action_type == "SEND_EMAIL" and a.status == "EXECUTED"
                ),
            )
            if prior_actions:
                state.history.append(
                    StepSummary(
                        step_no=0,
                        agent="case",
                        kind="context",
                        summary="Prior proposals: "
                        + "; ".join(f"{a.action_type} {a.status}" for a in prior_actions),
                    )
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
        nxt = decision.next
        reason = decision.escalation_reason or (
            decision.reasoning_summary if nxt == "RESOLVED" else None
        )
        # Guardrails the model cannot talk its way around:
        unavailable = nxt in SPECIALISTS and nxt not in SPECIALISTS_AVAILABLE
        if unavailable:
            nxt, reason = (
                "ESCALATED",
                f"specialist {decision.next} is not available in this deployment",
            )
        elif nxt in SPECIALISTS and state.specialist_runs.get(nxt, 0) >= 3:
            nxt, reason = "ESCALATED", f"loop detected: {decision.next} already ran three times"
        elif state.pending_actions() and nxt not in ("AWAIT_APPROVAL", "ESCALATED"):
            nxt, reason = "AWAIT_APPROVAL", "a proposed action is pending a human decision"
        elif nxt == "RESOLVED" and not _resolution_secured(state):
            nxt, reason = (
                "ESCALATED",
                "resolution not secured: no zero balance, confirmation, PO or accepted offer",
            )
        elif nxt in ("Reconciler", "Negotiator") and not state.investigation:
            nxt, reason = "ESCALATED", f"{decision.next} requires an investigation first"
        elif nxt == "WAIT_FOR_CUSTOMER" and not state.executed("SEND_EMAIL"):
            nxt, reason = "ESCALATED", "cannot wait for a customer that was never emailed"
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
            partial = getattr(e, "partial", {}) or {}
            await self._finish_step(
                step_id,
                state,
                status="ERROR",
                output=None,
                usage=Usage.model_validate(partial.get("usage") or {}),
                tool_calls=list(partial.get("tool_log") or []),
                messages=list(partial.get("messages") or []) or None,
                provider=router.for_tier("fast" if spec.tier == "fast" else "strong").provider,
                model=router.for_tier("fast" if spec.tier == "fast" else "strong").model,
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
        if name == "Reconciler":
            rec: ReconcilerOutput = output
            state.reconciliation = rec
            self._track_action(
                state,
                rec.action_id,
                "CREATE_CREDIT_MEMO",
                f"credit memo {rec.proposed_credit_memo}",
            )
            if rec.nothing_owed:
                return "Reconciler: nothing owed"
            return f"Reconciler: credit memo {rec.proposed_credit_memo} proposed" + (
                " (void duplicate)" if rec.void_duplicate else ""
            )
        if name == "Negotiator":
            neg: NegotiatorOutput = output
            state.negotiation = neg
            self._track_action(state, neg.action_id, "PAYMENT_PLAN", neg.offer.summary)
            return f"Negotiator: {neg.offer.type} {neg.offer.summary[:80]}"
        if name == "Communicator":
            em: CommunicatorOutput = output
            state.last_email = em
            self._track_action(
                state, em.action_id, "SEND_EMAIL", f"{em.purpose} to {', '.join(em.to)}"
            )
            return (
                f"Communicator: {em.purpose} → {', '.join(em.to)} "
                f"(tone {em.tone_score:.2f}, policy {em.policy_decision})"
            )
        if name == "Intent":
            it: CustomerIntent = output
            state.last_intent = it
            return f"Customer intent: {it.intent} ({it.confidence:.0%}) {it.summary[:100]}"
        return f"{name} completed"

    def _track_action(
        self, state: CaseState, action_id: str | None, action_type: str, summary: str
    ) -> None:
        if not action_id:
            return
        if any(a.action_id == action_id for a in state.actions):
            return
        state.actions.append(
            ProposedActionRef(
                action_id=action_id,
                action_type=action_type,
                policy_decision="UNKNOWN",
                proposed_by="agent",
                summary=summary,
            )
        )

    async def _refresh_actions(self, state: CaseState) -> None:
        """Pull the authoritative status of every tracked action from the case service."""
        if not state.actions:
            return
        detail = await self.d.cases.get_detail(state.tenant_id, state.case_id)
        by_id = {a["id"]: a for a in detail["actions"]}
        for ref in state.actions:
            a = by_id.get(ref.action_id)
            if not a:
                continue
            ref.policy_decision = a["policy_decision"]
            ref.required_role = a.get("required_role")
            ref.status = "EXECUTED" if a.get("executed_at") else a["status"]
            if a["status"] in ("EXECUTED", "AUTO_EXECUTED"):
                ref.status = "EXECUTED"
            ref.execution_result = a.get("execution_result")
        state.case = detail["case"]

    @activity.defn(name="refresh_actions")
    async def refresh_actions(self, state: CaseState) -> CaseState:
        state = state.model_copy(deep=True)
        await self._refresh_actions(state)
        return state

    @activity.defn(name="execute_action")
    async def execute_action(self, args: ExecuteArgs) -> CaseState:
        """Deterministic executor: runs an approved or allowed proposal through the Tool Gateway."""
        state = args.state.model_copy(deep=True)
        activity.heartbeat(args.action_id)
        ref = next((a for a in state.actions if a.action_id == args.action_id), None)
        if ref is None:
            return state
        action = await self.d.cases.get_action(state.tenant_id, state.case_id, args.action_id)
        payload = {**action["payload"], **(action.get("human_final") or {})}
        tool = {
            "CREATE_CREDIT_MEMO": "create_credit_memo",
            "SEND_EMAIL": "send_email",
            "PAYMENT_PLAN": "apply_payment_plan",
            "ESCALATE": "escalate_case",
        }.get(action["action_type"])
        step_id = await self._start_step(
            state,
            "executor",
            "execute",
            {"action_id": args.action_id, "action_type": action["action_type"], "tool": tool},
        )
        state.step_no += 1
        t0 = time.perf_counter()
        if tool is None:
            ref.status = "FAILED"
            title = f"Executor: no tool for {action['action_type']}"
            env: dict[str, Any] = {"status": "UNSUPPORTED"}
        else:
            env = await self.d.gateway.invoke(
                tool,
                tenant_id=state.tenant_id,
                case_id=state.case_id,
                run_id=state.run_id,
                actor="agent:executor",
                args=payload,
                idempotency_key=f"exec-{args.action_id}",
                approval_ref=args.action_id,
            )
            if env.get("status") == "SUCCESS":
                ref.status, ref.execution_result = "EXECUTED", {"result": env.get("result")}
                if action["action_type"] == "SEND_EMAIL":
                    state.outreach_count += 1
                title = f"Executor: {tool} executed ({action['action_type']})"
            else:
                ref.status = "FAILED"
                title = (
                    f"Executor: {tool} blocked: {env.get('error')} {str(env.get('message'))[:120]}"
                )
        state.history.append(
            StepSummary(
                step_no=state.step_no, agent="executor", kind="execute", summary=title[:200]
            )
        )
        await self._finish_step(
            step_id,
            state,
            status="SUCCESS" if ref.status == "EXECUTED" else "ERROR",
            output={
                "action_id": args.action_id,
                "gateway": {k: env.get(k) for k in ("status", "error", "message", "invocation_id")},
                "result": env.get("result"),
            },
            usage=Usage(),
            tool_calls=[{"tool": tool, "status": env.get("status")}],
            messages=None,
            provider="",
            model="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=None if ref.status == "EXECUTED" else str(env.get("message")),
            title=title,
        )
        await self._refresh_actions(state)
        return state

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
            if any(sig.get("type") in ("timeout", "approval_timeout") for sig in args.signals):
                state.waits += 1
            await self._refresh_actions(state)
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
        if status in ("ESCALATED", "FAILED"):
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


def _resolution_secured(state: CaseState) -> bool:
    try:
        if Decimal(str(state.case.get("amount_open", "1"))) <= 0:
            return True
    except Exception:
        pass
    it = state.last_intent
    if it and it.intent in ("CONFIRMS_PAYMENT", "ACCEPTS_OFFER"):
        return True
    return bool(it and it.intent == "PROVIDES_PO" and state.executed("SEND_EMAIL"))


def _resolution_payload(state: CaseState) -> dict[str, Any]:
    it = state.last_intent
    payload = {
        "root_cause": state.investigation.confirmed_cause if state.investigation else None,
        "credit_memo": state.reconciliation.proposed_credit_memo if state.reconciliation else None,
        "offer": state.negotiation.offer.model_dump(mode="json") if state.negotiation else None,
        "customer_intent": it.model_dump(mode="json") if it else None,
        "emails_sent": len(state.executed("SEND_EMAIL")),
        "actions": [a.model_dump(mode="json", exclude={"execution_result"}) for a in state.actions],
    }
    safe: dict[str, Any] = json.loads(json.dumps(payload, default=str))  # Decimals -> strings
    return safe


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
