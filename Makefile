SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE ?= docker compose
SERVICES := iam mock-erp case policy communication tool-gateway orchestrator
PY_PKGS := libs/recoup-common libs/recoup-erp-adapter services/iam services/mock-erp services/case services/gateway services/policy services/communication services/tool-gateway services/orchestrator services/realtime libs/recoup-llm

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install python workspace + frontend deps
	uv sync --all-packages
	cd frontend && pnpm install

env: ## Create .env from example if missing
	@test -f .env || cp .env.example .env

# ---------- infra ----------
infra-up: env ## Start only infrastructure containers (postgres, redpanda, temporal, redis, minio, mailpit, otel, grafana)
	$(COMPOSE) up -d postgres redpanda redpanda-console temporal temporal-ui redis minio mailpit otel-collector tempo grafana

run-agents: ## Start an agent run for every NEW case (cases created before the orchestrator existed)
	uv run python scripts/run_agents.py

up: env ## Start the full stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Tail service logs
	$(COMPOSE) logs -f iam mock-erp case gateway policy communication tool-gateway orchestrator realtime

# ---------- database ----------
migrate: ## Run alembic migrations for every service (uses DATABASE_URL from .env)
	@for s in $(SERVICES); do echo ">> migrating $$s"; (cd services/$$s && uv run alembic upgrade head) || exit 1; done

migrate-down: ## Roll back one revision per service
	@for s in $(SERVICES); do (cd services/$$s && uv run alembic downgrade -1); done

seed: ## Seed IAM (demo tenant + users) and Mock ERP (customers/invoices/scenarios)
	uv run python scripts/seed.py

simulate-reply: ## Send a simulated customer reply into Mailpit for the newest outbound email
	uv run python scripts/simulate_reply.py

ingest: ## Trigger one overdue-invoice ingestion pass on the case service
	curl -s -X POST $${CASE_URL:-http://localhost:8003}/internal/ingest | python3 -m json.tool

# ---------- dev servers (host) ----------
dev-iam: ## Run IAM service locally on :8001
	cd services/iam && uv run uvicorn recoup_iam.main:app --reload --port 8001
dev-erp: ## Run Mock ERP locally on :8002
	cd services/mock-erp && uv run uvicorn recoup_mock_erp.main:app --reload --port 8002
dev-case: ## Run Case service locally on :8003
	cd services/case && uv run uvicorn recoup_case.main:app --reload --port 8003
dev-policy: ## Run Policy service locally on :8004
	cd services/policy && uv run uvicorn recoup_policy.main:app --reload --port 8004
dev-comm: ## Run Communication service locally on :8005
	cd services/communication && uv run uvicorn recoup_communication.main:app --reload --port 8005
dev-tools: ## Run Tool Gateway locally on :8006
	cd services/tool-gateway && uv run uvicorn recoup_tool_gateway.main:app --reload --port 8006
dev-orchestrator: ## Run the agent orchestrator (API + Temporal worker) locally on :8007
	cd services/orchestrator && uv run uvicorn recoup_orchestrator.main:app --reload --port 8007
dev-realtime: ## Run the realtime WebSocket service locally on :8008
	cd services/realtime && uv run uvicorn recoup_realtime.main:app --reload --port 8008
dev-gateway: ## Run API gateway locally on :8000
	cd services/gateway && uv run uvicorn recoup_gateway.main:app --reload --port 8000
dev-web: ## Run the React console on :5173
	cd frontend && pnpm dev

# ---------- quality ----------
lint: ## Ruff + mypy + eslint + tsc
	uv run ruff check .
	uv run ruff format --check .
	@for p in $(PY_PKGS); do echo ">> mypy $$p"; (cd $$p && uv run --project ../.. mypy .) || exit 1; done
	cd frontend && pnpm lint && pnpm typecheck

fmt: ## Format python + frontend
	uv run ruff format .
	uv run ruff check --fix .
	cd frontend && pnpm format

test: ## Unit tests (no DB required)
	@for p in $(PY_PKGS); do echo ">> pytest $$p"; (cd $$p && uv run --project ../.. pytest -q -m "not integration" --rootdir=. -p no:cacheprovider) || exit 1; done

test-all: ## All tests incl. integration (requires TEST_DATABASE_URL)
	@for p in $(PY_PKGS); do echo ">> pytest $$p"; (cd $$p && uv run --project ../.. pytest -q --rootdir=. -p no:cacheprovider) || exit 1; done

.PHONY: help install env infra-up up down logs migrate migrate-down seed simulate-reply run-agents ingest dev-iam dev-orchestrator dev-realtime dev-erp dev-case dev-policy dev-comm dev-tools dev-gateway dev-web lint fmt test test-all
