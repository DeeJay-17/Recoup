import uuid
from decimal import Decimal

import pytest
from recoup_case import service
from recoup_case.models import Outbox, TimelineEvent
from recoup_case.schemas import CaseCreate, ProposedActionCreate
from recoup_case.state_machine import ActionStatus, ActionType, CaseStatus, PolicyDecision
from recoup_common.auth import Principal, Role
from recoup_common.errors import ConflictError, ForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _principal(tenant_id: uuid.UUID, role: Role = Role.ANALYST) -> Principal:
    return Principal(sub=uuid.uuid4(), tenant_id=tenant_id, email="ava@acme-demo.com", roles=[role])


async def test_create_case_writes_timeline_and_outbox(
    session: AsyncSession, tenant_id: uuid.UUID
) -> None:
    case = await service.create_case(
        session,
        tenant_id,
        CaseCreate(
            customer_ref="CUST-0001",
            invoice_refs=["INV-1"],
            amount_open=Decimal("1200.50"),
            days_overdue=20,
        ),
    )
    await session.flush()
    assert case.status == CaseStatus.NEW
    assert case.priority == 3
    events = await service.timeline(session, case)
    assert [e.kind for e in events] == ["created"]
    from sqlalchemy import select

    out = (await session.scalars(select(Outbox))).all()
    assert [o.event_type for o in out] == ["case.created"]


async def test_duplicate_invoice_rejected(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    data = CaseCreate(customer_ref="C", invoice_refs=["INV-2"], amount_open=Decimal("10"))
    await service.create_case(session, tenant_id, data)
    await session.flush()
    with pytest.raises(ConflictError):
        await service.create_case(session, tenant_id, data)


async def test_approval_flow(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    case = await service.create_case(
        session,
        tenant_id,
        CaseCreate(customer_ref="C", invoice_refs=["INV-3"], amount_open=Decimal("3000")),
    )
    await service.transition(
        session, case, CaseStatus.TRIAGED, actor_type="agent", actor_id="agent:triage"
    )
    action = await service.propose_action(
        session,
        case,
        ProposedActionCreate(
            action_type=ActionType.CREATE_CREDIT_MEMO,
            payload={"amount": "250.00"},
            rationale="price mismatch on line 2",
            policy_decision=PolicyDecision.REQUIRE_APPROVAL,
            required_role="manager",
            proposed_by="agent:reconciler",
        ),
    )
    assert case.status == CaseStatus.PENDING_APPROVAL
    assert action.status == ActionStatus.PENDING

    with pytest.raises(ForbiddenError):
        await service.decide_action(
            session,
            case,
            action,
            _principal(tenant_id, Role.ANALYST),
            decision=ActionStatus.APPROVED,
        )

    await service.decide_action(
        session, case, action, _principal(tenant_id, Role.MANAGER), decision=ActionStatus.APPROVED
    )
    assert action.status == ActionStatus.APPROVED
    assert case.status == CaseStatus.ACTION_TAKEN

    with pytest.raises(ConflictError):
        await service.decide_action(
            session,
            case,
            action,
            _principal(tenant_id, Role.MANAGER),
            decision=ActionStatus.REJECTED,
        )


async def test_optimistic_lock_conflict(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    case = await service.create_case(
        session,
        tenant_id,
        CaseCreate(customer_ref="C", invoice_refs=["INV-4"], amount_open=Decimal("10")),
    )
    await session.flush()
    with pytest.raises(ConflictError):
        await service.transition(
            session,
            case,
            CaseStatus.TRIAGED,
            actor_type="human",
            actor_id="h",
            expected_version=case.version + 5,
        )


async def test_takeover_and_release(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    p = _principal(tenant_id)
    case = await service.create_case(
        session,
        tenant_id,
        CaseCreate(customer_ref="C", invoice_refs=["INV-5"], amount_open=Decimal("10")),
    )
    await service.takeover(session, case, p, "manual outreach")
    assert case.agent_mode == "HUMAN_CONTROL"
    with pytest.raises(ConflictError):
        await service.takeover(session, case, p, None)
    await service.release(session, case, p)
    assert case.agent_mode == "AUTONOMOUS"
    kinds = [e.kind for e in await service.timeline(session, case)]
    assert kinds == ["created", "takeover", "release"]
    assert all(isinstance(e, TimelineEvent) for e in await service.timeline(session, case))
