"""Kafka events in, read model out.

Every handler is an idempotent upsert keyed by the event or entity id, so replaying the topic
from the beginning rebuilds the same tables. That is what makes `fact_events` a replay log rather
than a second source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from recoup_common.db import Database
from recoup_common.events import DomainEvent
from recoup_common.logging import get_logger
from sqlalchemy import Table
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_analytics.models import (
    DimCase,
    FactAction,
    FactAgentRun,
    FactAgentStep,
    FactEmail,
    FactEvent,
    FactToolCall,
)
from recoup_analytics.settings import Settings

log = get_logger(__name__)


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except (ValueError, AttributeError):
        return None


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


async def _upsert(
    session: AsyncSession, model: Any, values: dict[str, Any], *, key: str, update: list[str]
) -> None:
    table = cast(Table, model.__table__)
    stmt = insert(table).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[key],
        set_={k: getattr(stmt.excluded, k) for k in update},
    )
    await session.execute(stmt)


async def _case_patch(
    session: AsyncSession,
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    patch: dict[str, Any],
    occurred_at: datetime,
) -> None:
    """Update a case row, creating a stub if the case.created event has not been seen yet
    (topics are consumed in parallel, so order across topics is not guaranteed)."""
    table = cast(Table, DimCase.__table__)
    base = {
        "case_id": case_id,
        "tenant_id": tenant_id,
        "opened_at": occurred_at,
        "updated_at": occurred_at,
    }
    stmt = insert(table).values(**base | patch)
    stmt = stmt.on_conflict_do_update(
        index_elements=["case_id"], set_={**patch, "updated_at": occurred_at}
    )
    await session.execute(stmt)


class Projector:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.s = settings
        self.seen = 0

    async def handle(self, event: DomainEvent) -> None:
        tenant_id = _uuid(event.tenantid)
        if tenant_id is None:
            return
        case_id = _uuid(event.data.get("case_id")) or _uuid(event.subject)
        async with self.db.session() as session:
            if self.s.keep_raw_events:
                await _upsert(
                    session,
                    FactEvent,
                    {
                        "event_id": event.id,
                        "tenant_id": tenant_id,
                        "type": event.type,
                        "case_id": case_id,
                        "source": event.source,
                        "payload": event.data,
                        "occurred_at": event.time,
                    },
                    key="event_id",
                    update=["payload"],
                )
            await self._project(session, event, tenant_id, case_id)
        self.seen += 1

    async def _project(
        self, session: AsyncSession, e: DomainEvent, tenant_id: uuid.UUID, case_id: uuid.UUID | None
    ) -> None:
        d, t, at = e.data, e.type, e.time
        if t == "case.created" and case_id:
            await _upsert(
                session,
                DimCase,
                {
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "customer_ref": d.get("customer_ref"),
                    "invoice_refs": d.get("invoice_refs"),
                    "currency": d.get("currency"),
                    "amount_open": _dec(d.get("amount_open")),
                    "days_overdue": d.get("days_overdue"),
                    "priority": d.get("priority"),
                    "status": "NEW",
                    "opened_at": at,
                    "updated_at": at,
                },
                key="case_id",
                update=[
                    "customer_ref",
                    "invoice_refs",
                    "currency",
                    "amount_open",
                    "days_overdue",
                    "priority",
                    "opened_at",
                    "updated_at",
                ],
            )
        elif t == "case.state_changed" and case_id:
            to = d.get("to")
            patch: dict[str, Any] = {"status": to}
            if to in ("RESOLVED", "WRITTEN_OFF"):
                patch |= {"closed_at": at, "close_reason": d.get("reason")}
            if to == "ESCALATED":
                patch |= {"escalated": True}
            await _case_patch(session, case_id, tenant_id, patch, at)
        elif t == "case.resolved" and case_id:
            await _case_patch(
                session,
                case_id,
                tenant_id,
                {"closed_at": at, "close_reason": d.get("reason"), "status": d.get("status")},
                at,
            )
        elif t in ("case.takeover", "case.released") and case_id:
            await _case_patch(
                session,
                case_id,
                tenant_id,
                {
                    "human_touched": True,
                    "agent_mode": "HUMAN_CONTROL" if t == "case.takeover" else "AUTONOMOUS",
                },
                at,
            )
        elif t == "case.action.proposed" and case_id:
            aid = _uuid(d.get("action_id"))
            if aid:
                await _upsert(
                    session,
                    FactAction,
                    {
                        "action_id": aid,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "action_type": d.get("action_type", "?"),
                        "policy_decision": d.get("policy_decision"),
                        "required_role": d.get("required_role"),
                        "outcome": "PROPOSED",
                        "proposed_at": at,
                    },
                    key="action_id",
                    update=["policy_decision", "required_role", "proposed_at"],
                )
        elif (
            t in ("case.action.approved", "case.action.edited", "case.action.rejected") and case_id
        ):
            aid = _uuid(d.get("action_id"))
            if aid:
                outcome = t.rsplit(".", 1)[-1].upper()
                await _upsert(
                    session,
                    FactAction,
                    {
                        "action_id": aid,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "action_type": d.get("action_type", "?"),
                        "outcome": outcome,
                        "decided_at": at,
                    },
                    key="action_id",
                    update=["outcome", "decided_at"],
                )
                await _case_patch(session, case_id, tenant_id, {"human_touched": True}, at)
        elif t == "agent.run.started":
            rid = _uuid(d.get("run_id"))
            if rid:
                await _upsert(
                    session,
                    FactAgentRun,
                    {
                        "run_id": rid,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "mode": d.get("mode"),
                        "status": "RUNNING",
                        "started_at": at,
                    },
                    key="run_id",
                    update=["mode", "status", "started_at"],
                )
            if case_id and d.get("mode") == "LIVE":
                await _case_patch(session, case_id, tenant_id, {"first_agent_at": at}, at)
        elif t == "agent.run.completed":
            rid = _uuid(d.get("run_id"))
            if rid:
                await _upsert(
                    session,
                    FactAgentRun,
                    {
                        "run_id": rid,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "status": d.get("status"),
                        "steps": int(d.get("steps") or 0),
                        "tokens_in": int(d.get("tokens_in") or 0),
                        "tokens_out": int(d.get("tokens_out") or 0),
                        "cost_usd": _dec(d.get("cost_usd")) or Decimal("0"),
                        "root_cause": d.get("root_cause"),
                        "reason": d.get("reason"),
                        "ended_at": at,
                    },
                    key="run_id",
                    update=[
                        "status",
                        "steps",
                        "tokens_in",
                        "tokens_out",
                        "cost_usd",
                        "root_cause",
                        "reason",
                        "ended_at",
                    ],
                )
            if case_id and d.get("root_cause"):
                await _case_patch(
                    session, case_id, tenant_id, {"root_cause": d.get("root_cause")}, at
                )
        elif t == "agent.step.completed":
            await _upsert(
                session,
                FactAgentStep,
                {
                    "event_id": e.id,
                    "tenant_id": tenant_id,
                    "case_id": case_id,
                    "run_id": _uuid(d.get("run_id")),
                    "agent": d.get("agent", "?"),
                    "kind": d.get("kind"),
                    "status": d.get("status"),
                    "tokens": int(d.get("tokens") or 0),
                    "latency_ms": d.get("latency_ms"),
                    "occurred_at": at,
                },
                key="event_id",
                update=["status", "tokens", "latency_ms"],
            )
        elif t in ("tool.invoked", "tool.blocked"):
            await _upsert(
                session,
                FactToolCall,
                {
                    "event_id": e.id,
                    "tenant_id": tenant_id,
                    "case_id": case_id,
                    "run_id": _uuid(d.get("run_id")),
                    "tool": d.get("tool", "?"),
                    "actor": d.get("actor"),
                    "status": d.get("status"),
                    "policy_decision": d.get("policy_decision"),
                    "side_effect": bool(d.get("side_effect")),
                    "latency_ms": d.get("latency_ms"),
                    "occurred_at": at,
                },
                key="event_id",
                update=["status", "policy_decision", "latency_ms"],
            )
        elif t in ("comm.email.sent", "comm.email.received"):
            direction = "OUT" if t.endswith("sent") else "IN"
            await _upsert(
                session,
                FactEmail,
                {
                    "event_id": e.id,
                    "tenant_id": tenant_id,
                    "case_id": case_id,
                    "direction": direction,
                    "template": d.get("template"),
                    "occurred_at": at,
                },
                key="event_id",
                update=["template"],
            )
            if case_id and direction == "OUT":
                dim = cast(Table, DimCase.__table__)
                await session.execute(
                    sa_update(dim)
                    .where(dim.c.case_id == case_id)
                    .values(emails_sent=dim.c.emails_sent + 1, updated_at=at)
                )
