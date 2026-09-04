from __future__ import annotations

from typing import Protocol

from recoup_common.config import BaseServiceSettings
from recoup_common.events.envelope import DomainEvent
from recoup_common.events.topics import topic_for
from recoup_common.logging import get_logger

log = get_logger(__name__)


class EventPublisher(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, event: DomainEvent, *, key: str | None = None) -> None: ...


class LoggingPublisher:
    """Fallback publisher used when no Kafka bootstrap is configured (unit tests, laptops)."""

    def __init__(self) -> None:
        self.published: list[DomainEvent] = []

    async def start(self) -> None:
        log.info("event publisher: logging mode (no KAFKA_BOOTSTRAP_SERVERS)")

    async def stop(self) -> None:
        return None

    async def publish(self, event: DomainEvent, *, key: str | None = None) -> None:
        self.published.append(event)
        log.info(
            "event.published", topic=topic_for(event.type), type=event.type, key=key, id=event.id
        )


class KafkaPublisher:
    def __init__(self, bootstrap_servers: str, client_id: str) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
            acks="all",
            enable_idempotence=True,
        )

    async def start(self) -> None:
        await self._producer.start()
        log.info("event publisher: kafka connected")

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, event: DomainEvent, *, key: str | None = None) -> None:
        headers = [("ce_type", event.type.encode()), ("ce_tenantid", event.tenantid.encode())]
        if event.traceparent:
            headers.append(("traceparent", event.traceparent.encode()))
        await self._producer.send_and_wait(
            topic_for(event.type),
            value=event.to_bytes(),
            key=(key or event.tenantid).encode(),
            headers=headers,
        )


def build_publisher(settings: BaseServiceSettings) -> EventPublisher:
    if settings.kafka_bootstrap_servers:
        return KafkaPublisher(settings.kafka_bootstrap_servers, client_id=settings.service_name)
    return LoggingPublisher()
