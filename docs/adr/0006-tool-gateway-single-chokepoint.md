# ADR-0006: Tool Gateway as the single side-effect chokepoint

**Status:** accepted (implemented in phase 2) · **Date:** 2026-09-04

## Context
Agents call 20+ tools. Policy, idempotency, rate limiting, PII redaction and audit must be
applied uniformly, and must not be bypassable by prompt.

## Decision
Agents receive a tool manifest (JSON Schema generated from Pydantic) and can only invoke tools via
`POST /tools/{name}/invoke`. The gateway checks policy for mutating tools, requires
`idempotency_key` + `approval_ref` where needed, redacts responses, and appends to
`tool_invocations`. Backing services are unreachable from the agent runtime network.

## Consequences
+ One place to audit and rate-limit; least-privilege manifests per tenant/case.
− Extra hop per tool call; mitigated with connection pooling and small payloads.
