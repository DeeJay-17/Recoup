# ADR-0003: Deterministic policy engine outside the LLM loop

**Status:** accepted · **Date:** 2026-09-04

## Context
Agents propose credit memos, discounts and outreach. Prompt injection via customer emails is a
real threat, and finance requires that money-moving decisions be explainable.

## Decision
The LLM never makes the final call. Every mutating action passes through a Policy Service that
evaluates `(actor, action, context) → ALLOW | REQUIRE_APPROVAL(role) | DENY(reason)` with plain
rules (JSON-logic style, versioned, auditable). The Tool Gateway rejects mutating calls that lack a
matching `approval_ref` when the decision was `REQUIRE_APPROVAL`.

Phase 1 already reserves this shape: `proposed_actions.policy_decision/policy_rule/required_role`
and the case service enforces `required_role` on approvals.

## Consequences
+ "Zero unauthorized mutations" is testable with red-team fixtures.
+ Managers edit policy without touching prompts.
− Rules need care to avoid over-blocking; we ship a dry-run simulator.
