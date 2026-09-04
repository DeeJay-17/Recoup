# ADR-0004: Transactional outbox instead of direct Kafka publish

**Status:** accepted (implemented in phase 0/1) · **Date:** 2026-09-04

## Context
Dual writes (commit DB, then publish) lose events on crash between the two, or publish events
for rolled-back transactions.

## Decision
Each service has an `outbox` table (`recoup_common.events.outbox.OutboxMixin`). Domain code
appends events in the same transaction as the state change. `OutboxRelay` drains rows in id
order with `FOR UPDATE SKIP LOCKED`, publishes, and marks `published_at`. Delivery is
at-least-once; consumers dedupe on `event_id`.

Without `KAFKA_BOOTSTRAP_SERVERS` the publisher logs instead, so laptops and unit tests need no broker.

## Consequences
+ No lost or phantom events; ordering per aggregate preserved by the id-ordered single drainer.
− An extra table and a poll loop per service; relay stops the batch on first failure by design.
