# ADR-0009: Analytics reads the event stream, not other services' tables

**Status:** accepted (implemented in phase 7) · **Date:** 2026-09-05

## Context
The manager dashboard needs numbers that span every service: cases, agent runs, proposals, tool
calls, emails. All of them already live in one PostgreSQL cluster, one schema per service, so the
fastest thing to write would be a query that joins across schemas.

## Options
1. Cross-schema SQL from the analytics service.
2. Call each service's API and aggregate in the dashboard.
3. Consume the domain events into an analytics-owned read model.

## Decision
Option 3. Analytics joins one consumer group across every topic and projects events into its own
tables. `fact_events` keeps the raw envelopes, and every projection is an idempotent upsert, so
the read model can be rebuilt from the topic at any time.

## Consequences
+ The database-per-service boundary holds: no service's schema is another's API, so schemas stay
  free to change behind their events.
+ Dashboard queries are single-table scans over shapes chosen for reading, not for writing.
+ A new metric is usually a new projection over history, not a migration of live data.
− The read model is eventually consistent, and a metric can only be as good as the event payload;
  adding a field means emitting it first and backfilling by replay.
− Events must stay idempotent and carry enough context, which is a real constraint on producers.
