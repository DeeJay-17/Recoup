"""Kafka consumer loop with per-event handler. Handlers must be idempotent (at-least-once)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from recoup_common.events.envelope import DomainEvent
from recoup_common.logging import get_logger

log = get_logger(__name__)
Handler = Callable[[DomainEvent], Awaitable[None]]


class KafkaConsumerLoop:
    def __init__(
        self,
        bootstrap_servers: str,
        *,
        group_id: str,
        topics: list[str],
        handler: Handler,
        client_id: str = "recoup",
    ) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            client_id=client_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self._handler = handler
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.topics = topics

    async def _run(self) -> None:
        started = False
        while not started and not self._stop.is_set():
            try:
                await self._consumer.start()
                started = True
            except Exception as e:
                log.warning("kafka.consumer_start_retry", error=str(e))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=3)
        if not started:
            return
        log.info("kafka.consumer_started", topics=self.topics)
        try:
            while not self._stop.is_set():
                batch = await self._consumer.getmany(timeout_ms=500, max_records=50)
                for _tp, records in batch.items():
                    for rec in records:
                        try:
                            event = DomainEvent.from_bytes(rec.value)
                        except Exception as e:
                            log.warning("kafka.bad_event", error=str(e))
                            continue
                        try:
                            await self._handler(event)
                        except Exception as e:
                            # Poison-pill protection: log and move on; the outbox keeps the event.
                            log.error(
                                "kafka.handler_failed", type=event.type, id=event.id, error=str(e)
                            )
                if batch:
                    await self._consumer.commit()
        finally:
            await self._consumer.stop()

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="kafka-consumer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
