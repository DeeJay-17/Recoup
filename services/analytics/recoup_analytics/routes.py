from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from recoup_common.auth import Principal, get_principal
from recoup_common.db import Database
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_analytics import metrics
from recoup_analytics.models import DimCase, FactEvent

router = APIRouter(prefix="/metrics", tags=["metrics"])
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Viewer = Annotated[Principal, Depends(get_principal)]
Days = Annotated[int, Query(ge=1, le=365)]


@router.get("/summary")
async def get_summary(session: SessionDep, principal: Viewer, days: Days = 30) -> dict[str, Any]:
    return await metrics.summary(session, principal.tenant_id, days=days)


@router.get("/aging")
async def get_aging(session: SessionDep, principal: Viewer) -> dict[str, Any]:
    return await metrics.aging(session, principal.tenant_id)


@router.get("/trend")
async def get_trend(
    session: SessionDep, principal: Viewer, days: Days = 30
) -> list[dict[str, Any]]:
    return await metrics.trend(session, principal.tenant_id, days=days)


@router.get("/funnel")
async def get_funnel(
    session: SessionDep, principal: Viewer, days: Days = 30
) -> list[dict[str, Any]]:
    return await metrics.funnel(session, principal.tenant_id, days=days)


@router.get("/agent-performance")
async def get_agent_performance(
    session: SessionDep, principal: Viewer, days: Days = 30
) -> dict[str, Any]:
    return await metrics.agent_performance(session, principal.tenant_id, days=days)


@router.get("/escalations")
async def get_escalations(
    session: SessionDep, principal: Viewer, days: Days = 30
) -> list[dict[str, Any]]:
    return await metrics.escalations(session, principal.tenant_id, days=days)


@router.get("/dashboard")
async def get_dashboard(session: SessionDep, principal: Viewer, days: Days = 30) -> dict[str, Any]:
    """Everything the manager dashboard renders, in one round trip."""
    t = principal.tenant_id
    return {
        "window_days": days,
        "summary": await metrics.summary(session, t, days=days),
        "aging": await metrics.aging(session, t),
        "trend": await metrics.trend(session, t, days=days),
        "funnel": await metrics.funnel(session, t, days=days),
        "agents": await metrics.agent_performance(session, t, days=days),
        "escalations": await metrics.escalations(session, t, days=days),
    }


@internal.get("/status")
async def status(session: SessionDep) -> dict[str, Any]:
    events = await session.scalar(select(func.count()).select_from(FactEvent))
    cases = await session.scalar(select(func.count()).select_from(DimCase))
    latest = await session.scalar(select(func.max(FactEvent.occurred_at)))
    return {
        "events": events,
        "cases": cases,
        "latest_event": latest.isoformat() if latest else None,
    }
