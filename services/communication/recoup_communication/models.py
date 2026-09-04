from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from recoup_common.db import make_metadata
from recoup_common.events.outbox import OutboxMixin
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "comm"


class Base(DeclarativeBase):
    metadata = make_metadata(SCHEMA)


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    customer_ref: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.threads.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # OUT | IN
    status: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # QUEUED|SENT|FAILED|RECEIVED|UNLINKED
    message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)  # Mailpit id for inbound
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    to_addrs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    cc_addrs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    invoice_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    link_method: Mapped[str | None] = mapped_column(Text)  # thread | invoice_ref | none
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    approval_ref: Mapped[str | None] = mapped_column(Text)
    sent_by: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Outbox(OutboxMixin, Base):
    pass
