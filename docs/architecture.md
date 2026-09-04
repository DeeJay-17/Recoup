# Architecture notes (living document)

See the full plan in `../recoup-project-plan.md`. This file records what is *actually built* and
how it deviates.

## Runtime topology (phase 1)

```
browser ──> frontend (nginx/vite) ──> gateway :8000 ──┬──> iam :8001      (schema iam)
                                                      ├──> case :8003     (schema cases) ──> mock-erp
                                                      └──> mock-erp :8002 (schema mockerp)
case ──(outbox relay)──> Kafka topics recoup.case / recoup.iam
all services ──OTLP──> otel-collector ──> Tempo ──> Grafana
```

## Conventions every service follows

* `recoup_common.http.create_app()` – structlog, OTel, `/healthz`, `/readyz`, `DomainError` → 4xx JSON.
* Settings via `pydantic-settings`; every field is an env var (`DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, …).
* One SQLAlchemy `Base` per service bound to its own schema; Alembic `env.py` is three lines thanks to
  `recoup_common.alembic_support.run_migrations`. Migrations create the schema.
* Every state change writes a `DomainEvent` (CloudEvents 1.0 + `tenantid` + `traceparent`) into the
  service's `outbox` table in the same transaction. `OutboxRelay` publishes in id order, stops the
  batch on first failure (ordering), and falls back to logging when Kafka is not configured.
* Topics: one per bounded context (`recoup.case`, `recoup.iam`, …); the event `type` disambiguates.
* Auth: HS256 JWT with `tenant_id` + `roles` claims (dev). Services re-validate the token; the
  gateway also validates and rate-limits per tenant. Roles rank viewer < analyst < manager < admin.
* `/internal/*` routes are for agents/tools/ops only; the gateway refuses to proxy them.

## Case service

* State machine in `recoup_case/state_machine.py`; illegal transitions raise `409 invalid_transition`
  with the allowed set in `details`.
* `cases.version` is an optimistic lock (SQLAlchemy `version_id_col`); clients may pass
  `expected_version` and get `409 conflict` if the case moved underneath them.
* `timeline_events` is append-only; the UI is rendered from it.
* `proposed_actions` carry the policy decision. `REQUIRE_APPROVAL` → `PENDING` and the case moves to
  `PENDING_APPROVAL`; `ALLOW` → `APPROVED` (auto); `DENY` → `DENIED`. Human decisions record
  `human_final`, `feedback_code/note` for the eval flywheel and require `required_role`.
* Ingestion polls the ERP adapter for invoices overdue ≥ N days and opens one case per invoice.
  `case_invoices (tenant_id, invoice_ref)` PK makes this idempotent.

## Mock ERP

Deterministic `ScenarioGenerator(seed, as_of)` produces customers, contracts (price lists,
freight billability, discount limits), POs, invoices, deliveries and remittances. Each invoice
stores `scenario` and `ground_truth` (root cause, expected credit memo, expected final state,
acceptable offers) that only `/admin/*` exposes — never the adapter surface the agents use.

## Policy service

`(action_type, context) -> ALLOW | REQUIRE_APPROVAL(role) | DENY(reason)`. Rules are JSON
(`all` / `any` / `not` / `{fact, op, value}`) evaluated over a context the *gateway* builds:
`case.*` and `customer.*` facts from the case service and ERP, `action.*` from the tool args,
`evidence.*` / `email.*` computed deterministically (reconciled credit, PO match, tone score).
Missing facts never satisfy a comparison. A DENY anywhere wins; otherwise the highest-priority
matching rule decides; no match falls back to `REQUIRE_APPROVAL(analyst)`. Every evaluation is
recorded and the simulator replays history against a candidate rule before it is saved as a
new version.

## Communication service

Outbound: `POST /internal/emails/send` (idempotency_key required, approval_ref recorded) →
SMTP → Mailpit. Inbound: a poller lists Mailpit messages addressed to the AR mailbox, threads by
`In-Reply-To`/`References` against our stored Message-IDs, falls back to `INV-nnnnnn` numbers in
subject/body/attachments → case lookup, else stores the message as `UNLINKED` for a human to
link. PDF attachments are text-extracted (pdfplumber) so remittance advices become evidence.
Sent/received emails are mirrored onto the case timeline.

## Tool gateway

`registry.py` holds `ToolSpec`s (Pydantic args/result, `side_effect`, `requires_policy_check`,
`action_type`, `policy_facts`, `visible`). `GET /tools/manifest?case_id=` returns JSON Schema
for the tools the case context warrants (`create_credit_memo` only on dispute root causes,
`apply_payment_plan` never on credit hold). `POST /tools/{name}/invoke` runs the pipeline in
`service.py`: validate → rate limit (Redis) → idempotency (durable, `tool_invocations`) →
policy + approval gate → execute (timeout) → mark approval executed → redact → audit +
`tool.invoked` event + timeline. Money math (`reconcile_lines`, `calculate_credit_memo`,
`simulate_payment_plan`) is plain code; the gateway recomputes it when checking a credit memo.

## Deviations from the plan (so far)

* ERP adapter is a library (`libs/recoup-erp-adapter`) rather than a network service; a real
  connector implements the same `ERPAdapter` protocol. Revisit when tool-gateway lands.
* Ingestion lives inside the case service instead of a separate job + `erp.invoice.overdue` topic.
* Keycloak is deferred; IAM issues its own JWTs. Interface (`Principal`, `get_principal`) stays.
* Tone classification is a deterministic lexicon scorer for now; an LLM classifier replaces it in
  phase 4 behind the same `classify_tone` tool (the *decision* stays in the policy engine).
* PII redaction is regex-based (phones, SSN/card-like numbers); Presidio can replace `redact_text`.
* Cedar was not used; the JSON rule grammar in `recoup_policy.engine` is small enough to own.
