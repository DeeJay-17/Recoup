"""Transactional outbox.

Services write domain events to their own ``outbox`` table inside the same transaction as the
state change. A relay loop drains the table and publishes to Kafka, marking rows published.
This guarantees at-least-once delivery without dual writes; consumers must be idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from recoup_common.db import Database, utcnow
from recoup_common.events.envelope import DomainEvent
from recoup_common.events.publisher import EventPublisher
from recoup_common.logging import get_logger
from recoup_common.tracing import current_traceparent

log = get_logger(__name__)


class OutboxMixin:
    """Mix into a service's declarative Base to get an outbox table."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    traceparent: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    def to_event(self) -> DomainEvent:
        return DomainEvent(
            id=self.event_id,
            type=self.event_type,
            source=self.source,
            subject=str(self.aggregate_id),
            tenantid=str(self.tenant_id),
            traceparent=self.traceparent,
            time=self.created_at,
            data=self.payload,
        )


def enqueue_event(
    session: AsyncSession,
    outbox_model: type[Any],
    *,
    source: str,
    tenant_id: uuid.UUID,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> Any:
    """Add an outbox row to the current session (does not commit)."""
    row = outbox_model(
        aggregate_id=aggregate_id,
        tenant_id=tenant_id,
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        source=source,
        traceparent=current_traceparent(),
        payload=payload,
        created_at=utcnow(),
    )
    session.add(row)
    return row


class OutboxRelay:
    """Background loop: drain unpublished outbox rows in id order and publish them."""

    def __init__(
        self,
        db: Database,
        publisher: EventPublisher,
        outbox_model: type[Any],
        *,
        poll_interval: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        self.db = db
        self.publisher = publisher
        self.model = outbox_model
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def drain_once(self) -> int:
        """Publish one batch. Returns number of rows published."""
        published = 0
        async with self.db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(self.model)
                        .where(self.model.published_at.is_(None))
                        .order_by(self.model.id)
                        .limit(self.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                try:
                    await self.publisher.publish(row.to_event(), key=str(row.aggregate_id))
                except Exception as e:
                    log.warning("outbox.publish_failed", event_id=row.event_id, error=str(e))
                    await session.execute(
                        update(self.model)
                        .where(self.model.id == row.id)
                        .values(attempts=self.model.attempts + 1, last_error=str(e)[:2000])
                    )
                    break  # preserve ordering: stop the batch on first failure
                await session.execute(
                    update(self.model)
                    .where(self.model.id == row.id)
                    .values(published_at=utcnow(), attempts=self.model.attempts + 1)
                )
                published += 1
        return published

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self.drain_once()
            except Exception as e:
                log.error("outbox.relay_error", error=str(e))
                n = 0
            if n == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="outbox-relay")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
