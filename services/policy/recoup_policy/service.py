from __future__ import annotations

import uuid
from typing import Any

from recoup_common.db import utcnow
from recoup_common.errors import ConflictError, NotFoundError
from recoup_common.events.outbox import enqueue_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from recoup_policy.defaults import DEFAULT_POLICIES
from recoup_policy.engine import Decision, EvaluationResult, PolicyRule, evaluate
from recoup_policy.models import Outbox, Policy, PolicyEvaluation, PolicyVersion
from recoup_policy.schemas import (
    EvaluateRequest,
    PolicyCreate,
    PolicyUpdate,
    PolicyVersionCreate,
    SimulateRequest,
    SimulateResponse,
    SimulateRow,
)

SOURCE = "policy-service"
EVENT_EVALUATED = "policy.evaluated"


async def list_policies(session: AsyncSession, tenant_id: uuid.UUID) -> list[Policy]:
    rows = await session.scalars(
        select(Policy)
        .where(Policy.tenant_id == tenant_id)
        .options(selectinload(Policy.versions))
        .order_by(Policy.action_type, Policy.priority, Policy.name)
    )
    return list(rows.all())


async def get_policy(session: AsyncSession, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> Policy:
    p = await session.scalar(
        select(Policy)
        .where(Policy.id == policy_id, Policy.tenant_id == tenant_id)
        .options(selectinload(Policy.versions))
    )
    if not p:
        raise NotFoundError("policy not found")
    return p


def current_version(p: Policy) -> PolicyVersion | None:
    return next((v for v in p.versions if v.version == p.current_version), None)


async def create_policy(
    session: AsyncSession, tenant_id: uuid.UUID, data: PolicyCreate, *, created_by: str
) -> Policy:
    dup = await session.scalar(
        select(Policy).where(Policy.tenant_id == tenant_id, Policy.name == data.name)
    )
    if dup:
        raise ConflictError(f"policy '{data.name}' already exists")
    p = Policy(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        action_type=data.action_type,
        priority=data.priority,
        enabled=data.enabled,
        current_version=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    p.versions.append(
        PolicyVersion(
            version=1,
            rule=data.rule,
            decision=data.decision.value,
            required_role=data.required_role,
            reason=data.reason,
            created_by=created_by,
            created_at=utcnow(),
        )
    )
    session.add(p)
    await session.flush()
    return p


async def update_policy(session: AsyncSession, p: Policy, data: PolicyUpdate) -> Policy:
    if data.description is not None:
        p.description = data.description
    if data.priority is not None:
        p.priority = data.priority
    if data.enabled is not None:
        p.enabled = data.enabled
    p.updated_at = utcnow()
    return p


async def add_version(
    session: AsyncSession, p: Policy, data: PolicyVersionCreate, *, created_by: str
) -> PolicyVersion:
    v = PolicyVersion(
        policy_id=p.id,
        version=p.current_version + 1,
        rule=data.rule,
        decision=data.decision.value,
        required_role=data.required_role,
        reason=data.reason,
        created_by=created_by,
        created_at=utcnow(),
    )
    p.versions.append(v)
    p.current_version = v.version
    p.updated_at = utcnow()
    await session.flush()
    return v


async def delete_policy(session: AsyncSession, p: Policy) -> None:
    await session.delete(p)


async def install_defaults(session: AsyncSession, tenant_id: uuid.UUID, *, created_by: str) -> int:
    existing = {p.name for p in await list_policies(session, tenant_id)}
    n = 0
    for d in DEFAULT_POLICIES:
        if d["name"] in existing:
            continue
        await create_policy(session, tenant_id, PolicyCreate(**d), created_by=created_by)
        n += 1
    return n


async def compiled_rules(session: AsyncSession, tenant_id: uuid.UUID) -> list[PolicyRule]:
    out: list[PolicyRule] = []
    for p in await list_policies(session, tenant_id):
        if not p.enabled:
            continue
        v = current_version(p)
        if v is None:
            continue
        out.append(
            PolicyRule(
                policy_id=str(p.id),
                name=p.name,
                action_type=p.action_type,
                priority=p.priority,
                rule=v.rule,
                decision=Decision(v.decision),
                required_role=v.required_role,
                reason=v.reason,
                version=v.version,
            )
        )
    return out


async def evaluate_request(
    session: AsyncSession,
    req: EvaluateRequest,
    *,
    default_decision: Decision,
    default_role: str,
) -> tuple[EvaluationResult, int | None]:
    rules = await compiled_rules(session, req.tenant_id)
    result = evaluate(
        req.action_type,
        req.context,
        rules,
        default_decision=default_decision,
        default_role=default_role,
    )
    if not req.record:
        return result, None
    row = PolicyEvaluation(
        tenant_id=req.tenant_id,
        case_id=req.case_id,
        actor=req.actor,
        action_type=req.action_type,
        context=req.context,
        decision=result.decision.value,
        required_role=result.required_role,
        reason=result.reason,
        matched=result.matched,
        evaluated_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=req.tenant_id,
        aggregate_id=req.case_id or req.tenant_id,
        event_type=EVENT_EVALUATED,
        payload={
            "evaluation_id": row.id,
            "case_id": str(req.case_id) if req.case_id else None,
            "actor": req.actor,
            "action_type": req.action_type,
            "decision": result.decision.value,
            "required_role": result.required_role,
            "matched": [m["name"] for m in result.matched],
        },
    )
    return result, row.id


async def simulate(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    p: Policy | None,
    req: SimulateRequest,
    *,
    default_decision: Decision,
    default_role: str,
) -> SimulateResponse:
    """Compare current decisions with what they'd be if ``p`` used the candidate rule."""
    rules = await compiled_rules(session, tenant_id)
    action_type = p.action_type if p else "*"
    candidate = PolicyRule(
        policy_id=str(p.id) if p else "candidate",
        name=p.name if p else "candidate",
        action_type=action_type,
        priority=p.priority if p else 0,
        rule=req.rule,
        decision=req.decision,
        required_role=req.required_role,
        version=(p.current_version + 1) if p else 1,
    )
    candidate_rules = [r for r in rules if r.policy_id != candidate.policy_id] + [candidate]

    contexts: list[tuple[str, dict[str, Any]]] = [(action_type, c) for c in req.contexts]
    if req.use_history:
        stmt = select(PolicyEvaluation).where(PolicyEvaluation.tenant_id == tenant_id)
        if p:
            stmt = stmt.where(PolicyEvaluation.action_type == p.action_type)
        rows = await session.scalars(
            stmt.order_by(PolicyEvaluation.evaluated_at.desc()).limit(req.history_limit)
        )
        contexts += [(r.action_type, r.context) for r in rows.all()]

    out: list[SimulateRow] = []
    for at, ctx in contexts:
        cur = evaluate(at, ctx, rules, default_decision=default_decision, default_role=default_role)
        cand = evaluate(
            at, ctx, candidate_rules, default_decision=default_decision, default_role=default_role
        )
        out.append(
            SimulateRow(
                context=ctx,
                current_decision=cur.decision,
                candidate_decision=cand.decision,
                changed=cur.decision != cand.decision or cur.required_role != cand.required_role,
            )
        )
    return SimulateResponse(rows=out, changed=sum(r.changed for r in out), total=len(out))
