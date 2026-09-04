# Recoup — Autonomous Accounts Receivable & Dispute Resolution Agents

Recoup is a multi-agent platform that works the *order-to-cash tail*: overdue invoices, disputes,
short-payments and missing-PO holds. Agents triage, investigate, reconcile, negotiate and draft
outreach inside deterministic policy guardrails; humans approve, edit or take over from a React
ops console. Every action is auditable and replayable.

## Demo

[![Recoup demo](docs/media/recoup-demo-poster.jpg)](docs/media/recoup-demo.mp4)

**[Watch the 3-minute walkthrough](docs/media/recoup-demo.mp4)** — recorded against the running
stack, captioned rather than narrated. It follows a pricing dispute from ingestion to a credit memo
the customer confirms, then shows the policy engine, the approval inbox, the eval scorecard and the
manager dashboard. [Segment index](docs/demo-video.md).

> Full project plan and architecture: [`recoup-project-plan.md`](./recoup-project-plan.md) ·
> Decisions: [`docs/adr/`](./docs/adr/) · Architecture notes: [`docs/architecture.md`](./docs/architecture.md)

**Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 (async) / Alembic · PostgreSQL 16 + pgvector ·
Kafka (Redpanda) · Temporal · Redis · MinIO · Mailpit · OpenTelemetry → Tempo/Grafana ·
LangGraph + LangChain (any provider: Gemini, OpenAI-compatible, Anthropic, ...) ·
React 18 + TypeScript + TanStack + Tailwind.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 Foundations | monorepo, `recoup-common`, compose infra, CI, IAM, schemas + Alembic, OTel | ✅ |
| 1 Domain core | Mock ERP + scenario generator, Case service (state machine, outbox, timeline, approvals), ingestion, console | ✅ |
| 2 Tools & Policy | Policy Service (rule engine + simulator), Communication Service (Mailpit in/out, threading, templates), Tool Gateway (typed manifest, policy/approval gate, reconciliation, audit) | ✅ |
| 3 First agents | Provider-agnostic LLM layer (Gemini default), Temporal `CaseWorkflow`, LangGraph Supervisor + Triage + Investigator, prompt versions, model routing, Realtime WebSocket stream, Agents console | ✅ |
| 4 Resolution agents + HITL | Reconciler, Negotiator, Communicator, intent extractor; deterministic executor; approval and customer-wait paths with follow-up cadence; approval inbox with field editors, diff view, feedback codes, keyboard shortcuts; scripted customer persona | ✅ |
| 5 Memory & RAG | Knowledge Service: provider-agnostic embeddings (Gemini default, hashing fallback), pgvector HNSW + Postgres FTS with reciprocal rank fusion, SOP/contract/email/resolution indexing, per-customer memory with provenance written after every case, knowledge tools for agents, Customer 360 page | ✅ |
| 6 Evals & observability | Shadow mode (agents run with side effects disabled), Eval Service with golden datasets from ground truth, deterministic + LLM-judged scoring, adversarial red-team probes, run comparison, CI gate, OpenTelemetry GenAI spans for every model call | ✅ |
| 7 Analytics & polish | Analytics service projecting the event stream into a read model, manager dashboard (exposure, aging heatmap, funnel, agent latency, escalation reasons), Helm chart, read-path load test | ✅ |
| 8 Launch | architecture diagrams, [demo video](docs/media/recoup-demo.mp4), ADRs, load-test results | ✅ |

Not done on purpose: the public deploy and the blog post. The Helm chart renders and validates
against the Kubernetes schemas but has never been applied to a live cluster.

## Architecture

Thirteen FastAPI services behind one gateway, one Postgres cluster with a schema per service, and
every state change leaving the database through a transactional outbox.

```mermaid
flowchart TB
  UI["React ops console :3000"]
  GW["gateway :8000<br/>JWT · rate limit · routes /api/* to every service"]
  RT["realtime :8008<br/>WebSocket fan-out"]
  ORC["orchestrator :8007<br/>Supervisor + six LangGraph specialists"]
  TMP["Temporal<br/>CaseWorkflow: signals, timers, durable retries"]
  TG["tool-gateway :8006<br/>every side effect goes through here"]
  POL["policy :8004<br/>ALLOW / REQUIRE_APPROVAL / DENY"]
  SVC["case :8003 · mock-erp :8002 · communication :8005<br/>knowledge :8009 · iam :8001"]
  PG[("PostgreSQL 16 + pgvector<br/>one schema and one outbox table per service")]
  BUS[["Redpanda<br/>recoup.iam · case · erp · agent · comm · policy · tool"]]
  AN["analytics :8011 · evals :8010"]

  UI --> GW
  RT -. "live agent timeline" .-> UI
  GW --> ORC
  GW --> SVC
  ORC <--> TMP
  ORC -- "the only tool call it can make" --> TG
  TG --> POL
  TG --> SVC
  SVC --> PG
  PG -- "outbox relay" --> BUS
  BUS --> ORC
  BUS --> RT
  BUS --> AN
```

| Service | Port | Owns |
|---|---|---|
| gateway | 8000 | JWT verification, per-tenant rate limit, routing |
| iam | 8001 | tenants, users, roles, tokens |
| mock-erp | 8002 | invoices, payments, credit memos, POs, contracts |
| case | 8003 | case state machine, timeline, approvals, outbox |
| policy | 8004 | deterministic rule engine and simulator |
| communication | 8005 | outbound and inbound email, threading, templates |
| tool-gateway | 8006 | typed tool registry, policy and approval gate, audit |
| orchestrator | 8007 | Temporal workflows, LangGraph agents, prompts, models |
| realtime | 8008 | WebSocket stream of agent and case events |
| knowledge | 8009 | hybrid retrieval, customer memory |
| evals | 8010 | golden datasets, judges, red-team probes |
| analytics | 8011 | event-sourced read model and metrics |
| frontend | 3000 | React ops console |

A case moves through a server-validated state machine. Active states can move between each other
as the supervisor changes its mind; the authoritative matrix is
[`state_machine.py`](./services/case/recoup_case/state_machine.py).

```mermaid
stateDiagram-v2
  [*] --> NEW: erp.invoice.overdue
  NEW --> TRIAGED: root cause + confidence + evidence
  TRIAGED --> INVESTIGATING: needs ERP or document evidence
  TRIAGED --> NEGOTIATING: cash-flow, offer a plan
  INVESTIGATING --> PENDING_APPROVAL: proposal needs a human
  NEGOTIATING --> PENDING_APPROVAL
  INVESTIGATING --> AWAITING_CUSTOMER: question sent
  NEGOTIATING --> AWAITING_CUSTOMER: offer sent
  PENDING_APPROVAL --> ACTION_TAKEN: approved or edited, then executed
  AWAITING_CUSTOMER --> INVESTIGATING: reply parsed by the intent extractor
  ACTION_TAKEN --> RESOLVED: balance cleared or offer accepted
  AWAITING_CUSTOMER --> ESCALATED: three follow-ups, no reply
  INVESTIGATING --> ESCALATED: policy denied or evidence exhausted
  ESCALATED --> RESOLVED: a human finishes it
  ESCALATED --> WRITTEN_OFF: uncollectable
  RESOLVED --> [*]
  WRITTEN_OFF --> [*]
```

## Choosing an LLM provider

Recoup is provider-agnostic. Every model call goes through `libs/recoup-llm`, which wraps
LangChain's `init_chat_model`, so any provider LangChain supports works once its package is
installed (Gemini, OpenAI and OpenAI-compatible servers such as vLLM/Ollama/OpenRouter,
Anthropic, Azure OpenAI, Groq, Mistral, ...). Configure it in `.env`:

```bash
LLM_PROVIDER=google_genai          # Gemini (default)
LLM_API_KEY=<your Gemini API key>  # from Google AI Studio
LLM_MODEL_FAST=gemini-2.5-flash    # triage, tone checks
LLM_MODEL_STRONG=gemini-2.5-pro    # supervisor, investigator, reconciliation, negotiation
# OpenAI-compatible example:  LLM_PROVIDER=openai LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL_FAST=llama3.1
# No key at all:              LLM_PROVIDER=heuristic  (deterministic rule-based agents; what CI uses)
```

Managers can also override provider/model/key per tenant from **Agents → Models** in the
console (tenant BYO-key). Prices per model live in `LLM_PRICES` for cost-per-case reporting.

## Quick start (docker compose)

```bash
cp .env.example .env          # defaults work with compose as-is
make up                       # builds + starts infra and services
make seed                     # demo tenant/users, 400 scripted invoices, policies, first ingestion pass
                              # every new case starts a CaseWorkflow automatically (AUTOSTART_ON_CASE_CREATED)
open http://localhost:3000    # ava@acme-demo.com / password
```

Useful UIs: Redpanda console `:8090` · Temporal `:8233` · Mailpit `:8025` · Grafana/Tempo `:3001` · MinIO `:9001`.

```bash
make demo                     # scripted customer persona: answers every agent email per scenario, so cases run to RESOLVED
make simulate-reply           # one-off customer reply to the newest outbound email
```

## Quick start (host, against your own Postgres)

```bash
cp .env.example .env
# edit DATABASE_URL, e.g. postgresql+asyncpg://user:pass@localhost:5432/recoup
# (the compose Postgres is published on host port 5433, not 5432)
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
services/gateway         edge: JWT check, routing, rate limit, BFF, blocks /internal, tools read-only
services/policy          deterministic rule engine: (action, context) -> ALLOW | REQUIRE_APPROVAL(role) | DENY; versions, simulator, audit
services/communication   SMTP out via Mailpit, inbound polling, threading by Message-ID / invoice number, Jinja templates, PDF text
services/tool-gateway    the only path to side effects: typed manifest, policy + approval gate, idempotency, redaction, audit
services/orchestrator    Temporal CaseWorkflow + worker, LangGraph tool-loop agents (supervisor, triage, investigator), prompts, model routing
services/realtime        Kafka -> WebSocket fan-out for the live console
libs/recoup-llm          provider-agnostic LLM client (LangChain init_chat_model), tiers, pricing, heuristic stand-in, embeddings
services/knowledge       hybrid retrieval (pgvector + FTS, RRF), document ingestion, customer memory writer, SOPs
services/evals           golden datasets, shadow replay, deterministic + LLM-judged scoring, red-team probes, CI gate
services/analytics       Kafka -> read model (dim_case, fact_agent_run/step/action/tool/email) and the dashboard metrics
infra/helm/recoup        Helm chart for the stateless services (bring your own Postgres/Kafka/Temporal/Redis)
frontend                 React console: work queue, case workspace, approval inbox
infra/                   postgres init, OTel collector, Tempo, Grafana provisioning
scripts/seed.py          one-shot demo seed
docs/adr                 architecture decision records
```

## How a side effect happens (phase 2)

```mermaid
sequenceDiagram
  autonumber
  participant A as Specialist agent
  participant TG as Tool Gateway
  participant P as Policy Service
  participant H as Human (console)
  participant X as ERP / SMTP

  A->>TG: POST /tools/create_credit_memo/invoke
  TG->>TG: validate args · rate limit · idempotency key
  TG->>P: evaluate(facts the gateway computed:<br/>reconciled credit, PO match, tone score)

  alt DENY
    P-->>TG: DENY
    TG-->>A: policy_denied (no side effect)
  else REQUIRE_APPROVAL
    P-->>TG: REQUIRE_APPROVAL
    TG-->>A: approval_required
    A->>TG: propose_action(...)
    H->>TG: approve or edit → approval_ref
    A->>TG: invoke again with approval_ref
    TG->>TG: verify approval, apply the human's edits<br/>over the model's args, mark it executed
    TG->>X: run the tool
  else ALLOW
    P-->>TG: ALLOW
    TG->>X: run the tool
  end

  TG->>TG: tool_invocations row + case timeline + tool.invoked event
```

Nothing the model says can skip this path: the LLM never talks to the ERP or SMTP directly.

## How a case gets worked (phases 3–4)

```
case.created (Kafka) ──> orchestrator starts CaseWorkflow(case-<id>) on Temporal
  loop:  Supervisor (LLM, structured decision)
           ──> Triage | Investigator | Reconciler | Negotiator | Communicator
           ──> AWAIT_APPROVAL | WAIT_FOR_CUSTOMER | ESCALATED | RESOLVED
         specialist = LangGraph tool loop: model ⇄ Tool Gateway until it calls submit_<agent>
         specialists never execute side effects: they call propose_action and the Policy Service
         answers ALLOW / REQUIRE_APPROVAL / DENY
         executor (plain code) runs allowed/approved proposals through the Tool Gateway with the
         approval_ref; a human's edits override the model's payload
         customer replies are read by a constrained Intent extractor (claims, never instructions)
         every step: agent_steps row, agent.step.* event, case timeline entry, tokens + cost
  signals: customer_replied / action_decided / human_takeover / human_release (from Kafka)
  waits:   WAIT_FOR_CUSTOMER (72h → 168h → 336h follow-ups) · AWAIT_APPROVAL · HUMAN_CONTROL
  end:     RESOLVED only when secured (zero balance, confirmed payment, accepted offer, PO
           received + invoice re-sent); otherwise an escalation brief for a human
```

The console's **Agents** page shows every run's trace (steps, tool calls, tokens, models,
prompt versions), lets managers edit and version prompts, and set per-tenant models.

## Measuring the agents (phase 6)

```bash
make eval-build            # golden set from Mock ERP ground truth, balanced across root causes
make eval                  # replay it in shadow mode and print the scorecard  (JUDGE=1 adds the LLM judge)
make eval-redteam          # adversarial probes; must report zero unauthorized mutations
make eval-gate             # smoke suite + red-team, exits non-zero below the thresholds (CI uses this)
```

Eval runs are **shadow runs**: the Tool Gateway refuses to execute any side-effecting tool when a
run's mode is not `LIVE`, and the workflow skips the executor, case writes, escalation and memory
writer. Policy is still evaluated, so a shadow run measures exactly what would have happened.
Scores come from Mock ERP ground truth (root cause, expected credit memo) plus the tool-invocation
audit (a `SUCCESS` on a money or outbound tool inside a shadow run is an unauthorized mutation),
with an optional LLM judge for rationale faithfulness and email quality.

### Measured baseline

12-case judged suite, shadow mode, Gemini (`gemini-2.5-flash` fast / `gemini-3-flash-preview`
strong), 2026-09-05. Small sample: treat it as a starting point, not a published benchmark.

| metric | result | plan target |
|---|---|---|
| root-cause accuracy (Investigator-confirmed) | 83% | ≥ 85% |
| credit memo within $1 of ground truth | 100% | ≥ 90% |
| unauthorized mutations | 0 | 0 |
| cost per case | $0.079 | < $0.40 |
| p95 case latency | 156 s | — |
| judge: rationale faithfulness | 4.75 / 5 | — |
| judge: email quality | 4.0 / 5 | — |
| red-team probes refused | 8 / 8 | 8 / 8 |

Triage's own top-1 accuracy is 42%: it proposes hypotheses from the invoice and customer record
alone, and the Investigator corrects it once reconciliation and delivery evidence are in. The two
misses were a short payment read as a pricing dispute and a missing-PO case read as cash flow.

Every model call is an OpenTelemetry span (`gen_ai.*` attributes, tokens and cost), so a case shows
up in Grafana/Tempo as one trace from the UI click through the workflow, tools and model calls.
Point `OTEL_EXPORTER_OTLP_ENDPOINT` at Langfuse's OTLP endpoint instead of the collector to get the
same data there.

## Operating numbers

```bash
make loadtest      # read-path load: work queue, case workspace, dashboard
make helm-lint     # lint and render the chart
```

The dashboard at `/dashboard` is built only from the event stream: the analytics service consumes
every topic into its own read model, so no query reaches into another service's tables. It shows
open exposure and an amount-weighted age (an honest DSO proxy, not textbook DSO, which needs
credit sales), an aging heatmap by root cause, the case funnel, autonomy and approval-without-edit
rates, cost per closed case, agent step latency, and why cases reach a human.

Read-path load on a laptop Docker stack, 16 workers for 15 seconds: **151 req/s, all 200s, p95
245-308 ms** across the work queue, the aggregated case workspace and the dashboard. The gateway's
per-tenant limiter (`GATEWAY_RATE_LIMIT`, 600/min by default) is the first ceiling you will hit;
raise it to measure service capacity rather than the limiter.

## Development

```bash
make lint      # ruff + mypy (strict) + eslint + tsc
make test      # unit tests (no DB)
TEST_DATABASE_URL=postgresql+asyncpg://recoup:recoup@localhost:5433/recoup make test-all
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
