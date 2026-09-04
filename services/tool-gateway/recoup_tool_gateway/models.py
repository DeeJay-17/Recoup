from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from recoup_common.db import make_metadata
from recoup_common.events.outbox import OutboxMixin
from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "tools"


class Base(DeclarativeBase):
    metadata = make_metadata(SCHEMA)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    policy_decision: Mapped[str | None] = mapped_column(Text)
    policy_evaluation_id: Mapped[int | None] = mapped_column(BigInteger)
    approval_ref: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(Text)
    invoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Outbox(OutboxMixin, Base):
    pass
