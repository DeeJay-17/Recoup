# ADR-0001: Temporal for durable agent workflows

**Status:** accepted (phase 3 implements) · **Date:** 2026-09-04

## Context
A case can live for weeks: it waits on customer email replies and human approvals, and agent
steps call flaky external systems. We need execution that survives process restarts, supports
timers measured in days, and can be signalled from outside.

## Options
1. Celery/RQ + DB-persisted state machine + cron for timers.
2. Hand-rolled loop on Kafka with checkpoints in Postgres.
3. Temporal workflows with activities, signals, timers and built-in retries.

## Decision
Temporal. `CaseWorkflow` holds the supervisor loop; each agent step is an activity with a
retry policy; customer replies and human decisions are signals; follow-up cadence is a timer.

## Consequences
+ Waiting is free and durable; no polling; replay gives auditability for free.
+ Retries/timeouts/heartbeats are declarative.
− Extra infra (server + Postgres schemas) and determinism constraints in workflow code.
− Team must learn workflow vs. activity boundaries; LangGraph runs *inside* activities only.
