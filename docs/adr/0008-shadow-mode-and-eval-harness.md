# ADR-0008: Shadow mode as the eval primitive

**Status:** accepted (implemented in phase 6) · **Date:** 2026-09-04

## Context
We need to measure agent quality on realistic cases, repeatedly, including against a stack that
holds live data. Replaying a case must not email a customer, issue a credit memo or move a case's
state, but it must still exercise the real prompts, tools and policy so the numbers mean something.

## Options
1. A separate "eval" deployment with its own database and a copy of the data.
2. Mock the tools inside the eval harness.
3. A mode flag enforced at the single side-effect chokepoint.

## Decision
Option 3. Runs carry a `mode`; the Tool Gateway (ADR-0006) refuses to execute any `side_effect`
tool unless the mode is `LIVE`, after evaluating policy exactly as it would live. The workflow
skips the executor, case writes, escalation and the memory writer for non-live runs. The eval
service therefore drives the *real* agents through the *real* gateway.

## Consequences
+ One code path for live and evaluated behaviour: no mock drift, and policy decisions are measured.
+ "Zero unauthorized mutations" is directly observable: any `SUCCESS` on a money or outbound tool
  inside a shadow run is a defect, and the red-team suite exists to keep that number at zero.
+ Shadow runs are cheap to re-run against new prompts or models for comparison.
− Shadow runs consume tokens like real runs, so the CI gate uses a small smoke suite and the
  deterministic provider; the full suite runs nightly.
− A run that parks on a customer reply has no counterpart in shadow, so the harness cancels it and
  scores the work done up to that point.
