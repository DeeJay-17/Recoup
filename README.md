# Recoup — Autonomous Accounts Receivable & Dispute Resolution Agents

Recoup is a multi-agent platform that works the *order-to-cash tail*: overdue invoices, disputes,
short-payments and missing-PO holds. Agents triage, investigate, reconcile, negotiate and draft
outreach inside deterministic policy guardrails; humans approve, edit or take over from a React
ops console. Every action is auditable and replayable.

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
| 7 Analytics & polish | Analytics service, manager dashboard, Helm chart, load test | ⏳ next |
| 8 | launch: README, demo video, ADRs, blog post | planned |

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
frontend                 React console: work queue, case workspace, approval inbox
infra/                   postgres init, OTel collector, Tempo, Grafana provisioning
scripts/seed.py          one-shot demo seed
docs/adr                 architecture decision records
```

## How a side effect happens (phase 2)

```
agent ──POST /tools/create_credit_memo/invoke──> Tool Gateway
   validate args ─> rate limit ─> idempotency ─> Policy Service (facts computed by the gateway,
   e.g. reconciled credit, PO match, tone score) ─> ALLOW / REQUIRE_APPROVAL / DENY
   REQUIRE_APPROVAL ⇒ agent calls propose_action; a human approves/edits in the console;
   agent retries with approval_ref ⇒ gateway verifies the approval (status, action type, not yet
   executed), applies the human's edits over the model's args, runs the tool, marks it executed,
   writes tool_invocations + case timeline + tool.invoked event.
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
