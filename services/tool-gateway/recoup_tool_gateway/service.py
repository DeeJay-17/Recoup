"""The invoke pipeline.

validate -> rate limit -> idempotency -> policy/approval -> run -> redact -> audit
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from recoup_common.db import Database, utcnow
from recoup_common.errors import DomainError, ForbiddenError, NotFoundError, ValidationError
from recoup_common.events.outbox import enqueue_event
from recoup_common.logging import get_logger
from recoup_common.tracing import current_trace_id
from sqlalchemy import select

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.models import Outbox, ToolInvocation
from recoup_tool_gateway.ratelimit import RateLimiter
from recoup_tool_gateway.redaction import redact, truncate
from recoup_tool_gateway.registry import ToolRegistry, ToolSpec

log = get_logger(__name__)
SOURCE = "tool-gateway"
EVENT_INVOKED = "tool.invoked"
EVENT_BLOCKED = "tool.blocked"


class PolicyDenied(ForbiddenError):
    code = "policy_denied"


class ApprovalRequired(ForbiddenError):
    code = "approval_required"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


class InvokeRequest(BaseModel):
    tenant_id: uuid.UUID
    case_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    actor: str = "agent:unknown"
    args: dict[str, Any] = {}
    idempotency_key: str | None = None
    approval_ref: str | None = None
    mode: str = "LIVE"  # LIVE | SHADOW | REPLAY: non-live never executes side effects


class InvokeResponse(BaseModel):
    tool: str
    status: str
    result: Any = None
    policy_decision: str | None = None
    required_role: str | None = None
    approval_ref: str | None = None
    invocation_id: int | None = None
    latency_ms: int
    cached: bool = False


async def base_policy_context(ctx: ToolContext) -> dict[str, Any]:
    """Facts the policy engine can rely on regardless of what the model claims."""
    context: dict[str, Any] = {"actor": {"id": ctx.actor, "type": ctx.actor.split(":", 1)[0]}}
    case = await ctx.case()
    if case:
        context["case"] = {
            "id": case["id"],
            "status": case["status"],
            "root_cause": case.get("root_cause"),
            "root_cause_conf": float(case["root_cause_conf"])
            if case.get("root_cause_conf") is not None
            else None,
            "amount_open": str(case["amount_open"]),
            "days_overdue": case["days_overdue"],
            "priority": case["priority"],
            "agent_mode": case["agent_mode"],
        }
        try:
            customer = await ctx.customer()
        except Exception:
            customer = None
        if customer:
            context["customer"] = {
                "ref": customer["customer_ref"],
                "credit_hold": customer["credit_hold"],
                "requires_po": customer["requires_po"],
                "credit_risk_score": customer["credit_risk_score"],
                "payment_terms_days": customer["payment_terms_days"],
            }
    return context


class ToolService:
    def __init__(
        self,
        db: Database,
        registry: ToolRegistry,
        limiter: RateLimiter,
        *,
        timeout: float,
        result_max_chars: int,
    ) -> None:
        self.db = db
        self.registry = registry
        self.limiter = limiter
        self.timeout = timeout
        self.result_max_chars = result_max_chars

    async def invoke(self, name: str, req: InvokeRequest, ctx: ToolContext) -> InvokeResponse:
        spec = self.registry.get(name)
        if spec is None:
            raise NotFoundError(f"unknown tool '{name}'")
        t0 = time.perf_counter()
        row = ToolInvocation(
            tenant_id=req.tenant_id,
            case_id=req.case_id,
            run_id=req.run_id,
            tool=name,
            actor=req.actor,
            args=req.args,
            status="STARTED",
            approval_ref=req.approval_ref,
            idempotency_key=req.idempotency_key,
            trace_id=current_trace_id(),
            invoked_at=utcnow(),
        )
        try:
            # 1. validate args
            try:
                args = spec.args_model.model_validate(req.args)
            except PydanticValidationError as e:
                raise ValidationError(
                    "invalid tool arguments", details={"errors": e.errors(include_url=False)}
                ) from e
            # 2. rate limit
            if not await self.limiter.allow(str(req.tenant_id)):
                raise RateLimited("tenant tool-call rate limit exceeded")
            # 3. idempotency (durable, via audit table)
            if spec.side_effect:
                if not req.idempotency_key:
                    raise ValidationError(
                        f"'{name}' has side effects and requires an idempotency_key"
                    )
                cached = await self._cached(req.tenant_id, name, req.idempotency_key)
                if cached is not None:
                    row.status, row.result, row.policy_decision = (
                        "CACHED",
                        cached.result,
                        cached.policy_decision,
                    )
                    return InvokeResponse(
                        tool=name,
                        status="SUCCESS",
                        result=cached.result,
                        policy_decision=cached.policy_decision,
                        approval_ref=cached.approval_ref,
                        invocation_id=cached.id,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        cached=True,
                    )
                ctx._cache["idempotency_key"] = req.idempotency_key
            # 4. policy + approval gate
            if req.mode != "LIVE" and spec.side_effect:
                return await self._simulate(spec, req, ctx, args, row, t0)
            if spec.requires_policy_check:
                args = await self._gate(spec, req, ctx, args, row)
            # 5. run
            result = await asyncio.wait_for(spec.handler(ctx, args), timeout=self.timeout)
            payload = result.model_dump(mode="json")
            # 6. mark approval executed
            if row.approval_ref and req.case_id and spec.name != "propose_action":
                try:
                    await ctx.cases.mark_executed(
                        req.tenant_id,
                        req.case_id,
                        row.approval_ref,
                        {"tool": name, "result": truncate(payload, 4000)},
                    )
                except DomainError as e:
                    log.warning("tool.mark_executed_failed", error=str(e))
            row.status, row.result = "SUCCESS", payload
        except DomainError as e:
            row.status = {
                "policy_denied": "DENIED",
                "approval_required": "APPROVAL_REQUIRED",
                "validation_error": "REJECTED_ARGS",
                "rate_limited": "RATE_LIMITED",
            }.get(e.code, "ERROR")
            row.error = f"{e.code}: {e.message}"
            raise
        except TimeoutError as e:
            row.status, row.error = "ERROR", f"timeout after {self.timeout}s"
            raise DomainError("tool timed out") from e
        except Exception as e:
            row.status, row.error = "ERROR", f"{type(e).__name__}: {e}"[:2000]
            log.exception("tool.failed", tool=name)
            raise DomainError(f"tool '{name}' failed: {e}") from e
        finally:
            if row.status != "SHADOW":
                row.latency_ms = int((time.perf_counter() - t0) * 1000)
                await self._audit(row, spec, ctx)
        return InvokeResponse(
            tool=name,
            status="SUCCESS",
            result=truncate(redact(row.result), self.result_max_chars),
            policy_decision=row.policy_decision,
            approval_ref=row.approval_ref,
            invocation_id=row.id,
            latency_ms=row.latency_ms or 0,
        )

    async def _cached(self, tenant_id: uuid.UUID, tool: str, key: str) -> ToolInvocation | None:
        async with self.db.session() as session:
            row: ToolInvocation | None = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.tenant_id == tenant_id,
                    ToolInvocation.tool == tool,
                    ToolInvocation.idempotency_key == key,
                    ToolInvocation.status == "SUCCESS",
                )
            )
            return row

    async def _simulate(
        self,
        spec: ToolSpec,
        req: InvokeRequest,
        ctx: ToolContext,
        args: BaseModel,
        row: ToolInvocation,
        t0: float,
    ) -> InvokeResponse:
        """Shadow/replay: evaluate policy for fidelity, then return a simulated result instead of
        touching the ERP, mailbox or case. A policy DENY still comes back as a denial so the agent
        re-plans exactly as it would live."""
        decision: dict[str, Any] = {}
        if spec.requires_policy_check:
            context = await base_policy_context(ctx)
            if spec.policy_facts is not None:
                context.update(await spec.policy_facts(ctx, args))
            action_type = spec.action_type or "*"
            if action_type == "*":
                action_type = str(getattr(args, "action_type", "*"))
            decision = await ctx.policy.evaluate(
                tenant_id=req.tenant_id,
                case_id=req.case_id,
                actor=req.actor,
                action_type=action_type,
                context=context,
                record=False,
            )
            ctx._cache["policy_decision"] = decision
            row.policy_decision = decision.get("decision")
            if decision.get("decision") == "DENY":
                raise PolicyDenied(
                    decision.get("reason") or "denied by policy",
                    details={"matched": decision.get("matched", []), "shadow": True},
                )
        result: dict[str, Any] = {
            "shadow": True,
            "tool": spec.name,
            "policy_decision": decision.get("decision"),
            "required_role": decision.get("required_role"),
            "args": redact(truncate(args.model_dump(mode="json"), 2000)),
        }
        if spec.name == "propose_action":
            result |= {
                "action_id": f"shadow-{uuid.uuid4()}",
                "status": "SHADOW",
                "next_step": (
                    "Shadow mode: the proposal was scored, not recorded. Call your submit tool now "
                    "with this action_id."
                ),
            }
        row.status, row.result = "SHADOW", result
        row.latency_ms = int((time.perf_counter() - t0) * 1000)
        await self._audit(row, spec, ctx)
        return InvokeResponse(
            tool=spec.name,
            status="SUCCESS",
            result=result,
            policy_decision=row.policy_decision,
            invocation_id=row.id,
            latency_ms=row.latency_ms or 0,
        )

    async def _gate(
        self,
        spec: ToolSpec,
        req: InvokeRequest,
        ctx: ToolContext,
        args: BaseModel,
        row: ToolInvocation,
    ) -> BaseModel:
        context = await base_policy_context(ctx)
        if spec.policy_facts is not None:
            context.update(await spec.policy_facts(ctx, args))
        action_type = spec.action_type or "*"
        if action_type == "*":  # propose_action carries its own action type
            action_type = getattr(args, "action_type", "*")
        decision = await ctx.policy.evaluate(
            tenant_id=req.tenant_id,
            case_id=req.case_id,
            actor=req.actor,
            action_type=action_type,
            context=context,
        )
        ctx._cache["policy_decision"] = decision
        row.policy_decision = decision["decision"]
        row.policy_evaluation_id = decision.get("evaluation_id")
        if spec.name == "propose_action":
            return args  # proposing is always allowed; the decision is recorded on the proposal
        if decision["decision"] == "DENY":
            raise PolicyDenied(
                decision.get("reason") or "denied by policy",
                details={"matched": decision.get("matched", [])},
            )
        if decision["decision"] == "REQUIRE_APPROVAL":
            if not req.approval_ref or not req.case_id:
                raise ApprovalRequired(
                    f"policy requires {decision.get('required_role') or 'human'} approval; "
                    "call propose_action and retry with approval_ref",
                    details={
                        "required_role": decision.get("required_role"),
                        "matched": decision.get("matched", []),
                    },
                )
            action = await ctx.cases.get_action(req.tenant_id, req.case_id, req.approval_ref)
            if action["status"] not in ("APPROVED", "EDITED"):
                raise ApprovalRequired(
                    f"approval {req.approval_ref} is {action['status']}, not APPROVED/EDITED"
                )
            if action["action_type"] != spec.action_type:
                raise ApprovalRequired(
                    f"approval {req.approval_ref} is for {action['action_type']}, "
                    f"not {spec.action_type}"
                )
            if action.get("executed_at"):
                raise ApprovalRequired(f"approval {req.approval_ref} was already executed")
            if action["status"] == "EDITED" and action.get("human_final"):
                # The human's edit is authoritative: it overrides whatever the model passed.
                merged = {**args.model_dump(mode="json"), **action["human_final"]}
                args = spec.args_model.model_validate(merged)
                row.args = merged
            ctx._cache["approval_ref"] = req.approval_ref
        return args

    async def _audit(self, row: ToolInvocation, spec: ToolSpec, ctx: ToolContext) -> None:
        async with self.db.session() as session:
            session.add(row)
            await session.flush()
            enqueue_event(
                session,
                Outbox,
                source=SOURCE,
                tenant_id=row.tenant_id,
                aggregate_id=row.case_id or row.tenant_id,
                event_type=EVENT_INVOKED if row.status == "SUCCESS" else EVENT_BLOCKED,
                payload={
                    "invocation_id": row.id,
                    "tool": row.tool,
                    "actor": row.actor,
                    "status": row.status,
                    "case_id": str(row.case_id) if row.case_id else None,
                    "run_id": str(row.run_id) if row.run_id else None,
                    "policy_decision": row.policy_decision,
                    "approval_ref": row.approval_ref,
                    "latency_ms": row.latency_ms,
                    "error": row.error,
                    "side_effect": spec.side_effect,
                },
            )
        if row.case_id and (spec.side_effect or row.status != "SUCCESS"):
            try:
                await ctx.cases.append_event(
                    row.tenant_id,
                    row.case_id,
                    {
                        "kind": "tool_call",
                        "actor_type": "agent" if row.actor.startswith("agent") else "system",
                        "actor_id": row.actor,
                        "title": f"{row.tool} → {row.status}"
                        + (f" (policy {row.policy_decision})" if row.policy_decision else ""),
                        "payload": {
                            "invocation_id": row.id,
                            "args": redact(truncate(row.args, 2000)),
                            "result": redact(truncate(row.result, 2000)) if row.result else None,
                            "error": row.error,
                            "latency_ms": row.latency_ms,
                        },
                    },
                )
            except Exception as e:
                log.warning("tool.timeline_failed", error=str(e))


def dumps(o: Any) -> str:
    return json.dumps(o, default=str)
