# Recoup — Autonomous Accounts Receivable & Dispute Resolution Agents

Recoup is a multi-agent platform that works the *order-to-cash tail*: overdue invoices, disputes,
short-payments and missing-PO holds. Agents triage, investigate, reconcile, negotiate and draft
outreach inside deterministic policy guardrails; humans approve, edit or take over from a React
ops console. Every action is auditable and replayable.

> Full project plan and architecture: [`recoup-project-plan.md`](./recoup-project-plan.md) ·
> Decisions: [`docs/adr/`](./docs/adr/) · Architecture notes: [`docs/architecture.md`](./docs/architecture.md)

**Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 (async) / Alembic · PostgreSQL 16 + pgvector ·
Kafka (Redpanda) · Temporal · Redis · MinIO · Mailpit · OpenTelemetry → Tempo/Grafana ·
React 18 + TypeScript + TanStack + Tailwind.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 Foundations | monorepo, `recoup-common`, compose infra, CI, IAM, schemas + Alembic, OTel | ✅ |
| 1 Domain core | Mock ERP + scenario generator, Case service (state machine, outbox, timeline, approvals), ingestion, console | ✅ |
| 2 Tools & Policy | Tool Gateway, Policy Service, Communication Service | ⏳ next |
| 3–8 | Agents (LangGraph + Temporal), HITL, RAG/memory, evals, analytics, launch | planned |

## Quick start (docker compose)

```bash
cp .env.example .env          # defaults work with compose as-is
make up                       # builds + starts infra and services
make seed                     # demo tenant/users, 400 scripted invoices, first ingestion pass
open http://localhost:3000    # ava@acme-demo.com / password
```

Useful UIs: Redpanda console `:8090` · Temporal `:8233` · Mailpit `:8025` · Grafana/Tempo `:3001` · MinIO `:9001`.

## Quick start (host, against your own Postgres)

```bash
cp .env.example .env
# edit DATABASE_URL, e.g. postgresql+asyncpg://user:pass@localhost:5432/recoup
# leave KAFKA_BOOTSTRAP_SERVERS empty to log events instead of publishing
make install && make migrate
make dev-iam & make dev-erp & make dev-case & make dev-gateway & make dev-web
make seed
```

Each service owns one schema in the same database (`iam`, `mockerp`, `cases`, …); migrations
create the schema on first run. No pgvector is needed until the Knowledge service (phase 5).

## Repository layout

```
libs/recoup-common       settings · async db · JWT auth/RBAC · CloudEvents envelope · outbox relay · OTel · structlog
libs/recoup-erp-adapter  ERPAdapter protocol + canonical models + Mock ERP HTTP client
services/iam             tenants, users, roles, login → JWT (Keycloak/OIDC later)
services/mock-erp        realistic AR data with 8 scripted scenarios and hidden ground truth
services/case            case state machine, timeline (append-only), proposed actions + approvals, ingestion
services/gateway         edge: JWT check, routing, rate limit, BFF, blocks /internal
frontend                 React console: work queue, case workspace, approval inbox
infra/                   postgres init, OTel collector, Tempo, Grafana provisioning
scripts/seed.py          one-shot demo seed
docs/adr                 architecture decision records
```

## Development

```bash
make lint      # ruff + mypy (strict) + eslint + tsc
make test      # unit tests (no DB)
TEST_DATABASE_URL=postgresql+asyncpg://recoup:recoup@localhost:5432/recoup make test-all
```

## Scripted scenarios (Mock ERP)

| Scenario | What the data looks like | Ground truth |
|---|---|---|
| `DISPUTE_PRICING` | 1–2 invoice lines priced above PO/contract | credit memo = overcharge (+tax) |
| `DISPUTE_QUANTITY` | delivery proof shows fewer units than invoiced | credit for the short units; maybe rebill |
| `MISSING_PO` | customer requires PO; invoice has none/invalid | obtain PO, re-send |
| `WRONG_CONTACT` | invoice emailed to a contact who left | re-send to active AP contact |
| `SHORT_PAY` | remittance deducts freight | credit if contract says freight non-billable, else collect |
| `DUPLICATE_INVOICE` | same PO already invoiced and paid | void in full |
| `CASH_FLOW` | everything matches; slow payer | payment plan / extension within policy |
| `CLEAN` | paid or not yet due | never becomes a case |

`POST /admin/seed` is deterministic for a given seed, so eval runs are reproducible.
