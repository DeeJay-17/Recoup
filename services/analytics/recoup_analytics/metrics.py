"""Dashboard queries. Every number here comes from the event-built read model, never from
another service's tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OPEN_STATUSES = (
    "NEW",
    "TRIAGED",
    "INVESTIGATING",
    "AWAITING_CUSTOMER",
    "NEGOTIATING",
    "PENDING_APPROVAL",
    "ACTION_TAKEN",
    "ESCALATED",
)
AGING_BUCKETS = [(0, 30, "0-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, 100000, "90+")]


async def _rows(session: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    res = await session.execute(text(sql), params)
    return [dict(r) for r in res.mappings().all()]


async def summary(session: AsyncSession, tenant_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    p = {"t": str(tenant_id), "since": since}
    open_ = (
        await _rows(
            session,
            f"""
        SELECT count(*) AS cases, coalesce(sum(amount_open), 0) AS amount,
               coalesce(sum(amount_open * days_overdue) / nullif(sum(amount_open), 0), 0) AS weighted_age
        FROM analytics.dim_case
        WHERE tenant_id = :t AND closed_at IS NULL AND status IN {OPEN_STATUSES}
    """,
            **p,
        )
    )[0]
    closed = (
        await _rows(
            session,
            """
        SELECT count(*) AS closed,
               count(*) FILTER (WHERE escalated) AS escalated,
               count(*) FILTER (WHERE NOT human_touched) AS untouched,
               coalesce(percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM (closed_at - opened_at)) / 3600), 0) AS median_hours
        FROM analytics.dim_case
        WHERE tenant_id = :t AND closed_at >= :since
    """,
            **p,
        )
    )[0]
    runs = (
        await _rows(
            session,
            """
        SELECT count(*) AS runs, coalesce(sum(cost_usd), 0) AS cost,
               coalesce(sum(tokens_in + tokens_out), 0) AS tokens,
               coalesce(avg(steps), 0) AS avg_steps
        FROM analytics.fact_agent_run
        WHERE tenant_id = :t AND mode = 'LIVE' AND started_at >= :since
    """,
            **p,
        )
    )[0]
    actions = (
        await _rows(
            session,
            """
        SELECT count(*) AS proposed,
               count(*) FILTER (WHERE policy_decision = 'ALLOW') AS auto_allowed,
               count(*) FILTER (WHERE outcome = 'APPROVED') AS approved,
               count(*) FILTER (WHERE outcome = 'EDITED') AS edited,
               count(*) FILTER (WHERE outcome = 'REJECTED') AS rejected
        FROM analytics.fact_action WHERE tenant_id = :t AND proposed_at >= :since
    """,
            **p,
        )
    )[0]
    decided = int(actions["approved"]) + int(actions["edited"]) + int(actions["rejected"])
    closed_n = int(closed["closed"]) or 0
    return {
        "window_days": days,
        "open_cases": int(open_["cases"]),
        "open_amount": float(open_["amount"]),
        "dso_proxy_days": round(float(open_["weighted_age"]), 1),
        "closed_cases": closed_n,
        "median_hours_to_close": round(float(closed["median_hours"]), 1),
        "escalation_rate": round(int(closed["escalated"]) / closed_n, 4) if closed_n else 0.0,
        "untouched_rate": round(int(closed["untouched"]) / closed_n, 4) if closed_n else 0.0,
        "actions_proposed": int(actions["proposed"]),
        "autonomy_rate": round(int(actions["auto_allowed"]) / int(actions["proposed"]), 4)
        if int(actions["proposed"])
        else 0.0,
        "approval_without_edit_rate": round(int(actions["approved"]) / decided, 4)
        if decided
        else 0.0,
        "agent_runs": int(runs["runs"]),
        "cost_usd": round(float(runs["cost"]), 4),
        "tokens": int(runs["tokens"]),
        "cost_per_closed_case": round(float(runs["cost"]) / closed_n, 4) if closed_n else 0.0,
        "avg_steps": round(float(runs["avg_steps"]), 2),
    }


async def aging(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    rows = await _rows(
        session,
        f"""
        SELECT coalesce(root_cause, 'UNCLASSIFIED') AS root_cause,
               CASE WHEN days_overdue <= 30 THEN '0-30'
                    WHEN days_overdue <= 60 THEN '31-60'
                    WHEN days_overdue <= 90 THEN '61-90'
                    ELSE '90+' END AS bucket,
               count(*) AS cases, coalesce(sum(amount_open), 0) AS amount
        FROM analytics.dim_case
        WHERE tenant_id = :t AND closed_at IS NULL AND status IN {OPEN_STATUSES}
        GROUP BY 1, 2
    """,
        t=str(tenant_id),
    )
    buckets = [b[2] for b in AGING_BUCKETS]
    causes = sorted({r["root_cause"] for r in rows})
    cells = {(r["root_cause"], r["bucket"]): r for r in rows}
    return {
        "buckets": buckets,
        "root_causes": causes,
        "cells": [
            {
                "root_cause": c,
                "bucket": b,
                "cases": int(cells.get((c, b), {}).get("cases", 0)),
                "amount": float(cells.get((c, b), {}).get("amount", 0)),
            }
            for c in causes
            for b in buckets
        ],
        "totals": [
            {
                "bucket": b,
                "cases": sum(int(r["cases"]) for r in rows if r["bucket"] == b),
                "amount": sum(float(r["amount"]) for r in rows if r["bucket"] == b),
            }
            for b in buckets
        ],
    }


MUTATING_TOOLS = ("create_credit_memo", "send_email", "apply_payment_plan")


async def trend(
    session: AsyncSession, tenant_id: uuid.UUID, *, days: int = 30
) -> list[dict[str, Any]]:
    rows = await _rows(
        session,
        """
        WITH days AS (
            SELECT generate_series(date_trunc('day', now()) - make_interval(days => :days),
                                   date_trunc('day', now()), '1 day')::date AS day
        )
        SELECT d.day::text AS day,
               (SELECT count(*) FROM analytics.dim_case c WHERE c.tenant_id = :t AND c.opened_at::date = d.day) AS opened,
               (SELECT count(*) FROM analytics.dim_case c WHERE c.tenant_id = :t AND c.closed_at::date = d.day) AS closed,
               (SELECT count(*) FROM analytics.dim_case c WHERE c.tenant_id = :t AND c.closed_at::date = d.day AND c.escalated) AS escalated,
               (SELECT coalesce(sum(r.cost_usd), 0) FROM analytics.fact_agent_run r
                 WHERE r.tenant_id = :t AND r.mode = 'LIVE' AND r.started_at::date = d.day) AS cost
        FROM days d ORDER BY d.day
    """,
        t=str(tenant_id),
        days=days,
    )
    # Numerics arrive as Decimal; the dashboard wants plain JSON numbers.
    return [{**r, "cost": round(float(r["cost"]), 6)} for r in rows]


async def funnel(
    session: AsyncSession, tenant_id: uuid.UUID, *, days: int = 30
) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    p = {"t": str(tenant_id), "since": since}
    opened = (
        await _rows(
            session,
            "SELECT count(*) AS n FROM analytics.dim_case WHERE tenant_id = :t AND opened_at >= :since",
            **p,
        )
    )[0]["n"]
    triaged = (
        await _rows(
            session,
            "SELECT count(*) AS n FROM analytics.dim_case WHERE tenant_id = :t AND opened_at >= :since AND root_cause IS NOT NULL",
            **p,
        )
    )[0]["n"]
    proposed = (
        await _rows(
            session,
            """
        SELECT count(DISTINCT case_id) AS n FROM analytics.fact_action WHERE tenant_id = :t AND proposed_at >= :since
    """,
            **p,
        )
    )[0]["n"]
    # Proposing, noting and remembering are side-effecting but they are not what the case is
    # trying to achieve: only money and outbound tools count as an executed action.
    executed = (
        await _rows(
            session,
            """
        SELECT count(DISTINCT case_id) AS n FROM analytics.fact_tool_call
        WHERE tenant_id = :t AND occurred_at >= :since AND status = 'SUCCESS' AND tool = ANY(:tools)
    """,
            **p,
            tools=list(MUTATING_TOOLS),
        )
    )[0]["n"]
    emailed = (
        await _rows(
            session,
            """
        SELECT count(DISTINCT case_id) AS n FROM analytics.fact_email WHERE tenant_id = :t AND occurred_at >= :since AND direction = 'OUT'
    """,
            **p,
        )
    )[0]["n"]
    resolved = (
        await _rows(
            session,
            """
        SELECT count(*) AS n FROM analytics.dim_case WHERE tenant_id = :t AND opened_at >= :since AND closed_at IS NOT NULL AND NOT escalated
    """,
            **p,
        )
    )[0]["n"]
    stages = [
        ("Opened", opened),
        ("Root cause found", triaged),
        ("Action proposed", proposed),
        ("Action executed", executed),
        ("Customer contacted", emailed),
        ("Closed without escalation", resolved),
    ]
    return [
        {"stage": s, "cases": int(n), "share": round(int(n) / int(opened), 4) if opened else 0.0}
        for s, n in stages
    ]


async def agent_performance(
    session: AsyncSession, tenant_id: uuid.UUID, *, days: int = 30
) -> dict[str, Any]:
    p = {"t": str(tenant_id), "since": datetime.now(UTC) - timedelta(days=days)}
    by_agent = await _rows(
        session,
        """
        SELECT agent, count(*) AS steps,
               count(*) FILTER (WHERE status = 'ERROR') AS errors,
               coalesce(avg(latency_ms), 0) AS avg_latency_ms,
               coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) AS p95_latency_ms,
               coalesce(sum(tokens), 0) AS tokens
        FROM analytics.fact_agent_step WHERE tenant_id = :t AND occurred_at >= :since
        GROUP BY agent ORDER BY steps DESC
    """,
        **p,
    )
    by_cause = await _rows(
        session,
        """
        SELECT coalesce(c.root_cause, 'UNCLASSIFIED') AS root_cause, count(*) AS cases,
               count(*) FILTER (WHERE c.closed_at IS NOT NULL AND NOT c.escalated) AS resolved,
               count(*) FILTER (WHERE c.escalated) AS escalated,
               coalesce(avg(r.cost_usd), 0) AS avg_cost, coalesce(avg(r.steps), 0) AS avg_steps
        FROM analytics.dim_case c
        LEFT JOIN analytics.fact_agent_run r ON r.case_id = c.case_id AND r.mode = 'LIVE'
        WHERE c.tenant_id = :t AND c.opened_at >= :since
        GROUP BY 1 ORDER BY cases DESC
    """,
        **p,
    )
    tools = await _rows(
        session,
        """
        SELECT tool, count(*) AS calls,
               count(*) FILTER (WHERE status = 'SUCCESS') AS ok,
               count(*) FILTER (WHERE status IN ('DENIED', 'APPROVAL_REQUIRED')) AS gated,
               coalesce(avg(latency_ms), 0) AS avg_latency_ms
        FROM analytics.fact_tool_call WHERE tenant_id = :t AND occurred_at >= :since
        GROUP BY tool ORDER BY calls DESC LIMIT 15
    """,
        **p,
    )
    return {
        "by_agent": [
            {
                **r,
                "avg_latency_ms": round(float(r["avg_latency_ms"])),
                "p95_latency_ms": round(float(r["p95_latency_ms"])),
                "error_rate": round(int(r["errors"]) / int(r["steps"]), 4)
                if int(r["steps"])
                else 0.0,
            }
            for r in by_agent
        ],
        "by_root_cause": [
            {
                **r,
                "avg_cost": round(float(r["avg_cost"]), 4),
                "avg_steps": round(float(r["avg_steps"]), 2),
                "resolution_rate": round(int(r["resolved"]) / int(r["cases"]), 4)
                if int(r["cases"])
                else 0.0,
            }
            for r in by_cause
        ],
        "tools": [{**r, "avg_latency_ms": round(float(r["avg_latency_ms"]))} for r in tools],
    }


async def escalations(
    session: AsyncSession, tenant_id: uuid.UUID, *, days: int = 30, limit: int = 10
) -> list[dict[str, Any]]:
    rows = await _rows(
        session,
        """
        SELECT coalesce(nullif(split_part(reason, ':', 1), ''), 'unspecified') AS reason,
               count(*) AS cases, coalesce(avg(steps), 0) AS avg_steps
        FROM analytics.fact_agent_run
        WHERE tenant_id = :t AND mode = 'LIVE' AND status IN ('ESCALATED', 'FAILED') AND ended_at >= :since
        GROUP BY 1 ORDER BY cases DESC LIMIT :limit
    """,
        t=str(tenant_id),
        since=datetime.now(UTC) - timedelta(days=days),
        limit=limit,
    )
    return [{**r, "avg_steps": round(float(r["avg_steps"]), 1)} for r in rows]
