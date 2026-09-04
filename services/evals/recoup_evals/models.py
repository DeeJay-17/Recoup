from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from recoup_common.db import make_metadata
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "evals"


class Base(DeclarativeBase):
    metadata = make_metadata(SCHEMA)


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # golden | redteam
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalCase(Base):
    __tablename__ = "eval_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invoice_ref: Mapped[str] = mapped_column(Text, nullable=False)
    customer_ref: Mapped[str] = mapped_column(Text, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False)
    expected_root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    expected_credit_memo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    expected_final_state: Mapped[str | None] = mapped_column(Text)
    ground_truth: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # RUNNING | COMPLETED | FAILED | CANCELLED
    judge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    prompt_bundle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    models: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cases_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cases_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    eval_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expected_root_cause: Mapped[str | None] = mapped_column(Text)
    triage_root_cause: Mapped[str | None] = mapped_column(Text)
    predicted_root_cause: Mapped[str | None] = mapped_column(Text)
    expected_credit_memo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    predicted_credit_memo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    credit_delta: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    terminal_status: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    policy_denials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_gates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unauthorized_mutations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failures: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
