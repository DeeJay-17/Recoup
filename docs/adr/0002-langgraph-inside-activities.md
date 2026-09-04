# ADR-0002: LangGraph for agent graphs, executed inside Temporal activities

**Status:** accepted (phase 3) · **Date:** 2026-09-04

## Context
We want explicit multi-agent hand-offs, structured state, and checkpointing without inventing a
graph runtime. We also want durability at the *case* level (ADR-0001).

## Decision
Each specialist (Triage, Investigator, …) is a LangGraph subgraph with Pydantic-typed state and
structured outputs. A Temporal activity runs one subgraph invocation; LangGraph's Postgres saver
checkpoints inside the activity so a retried activity resumes mid-graph.

## Consequences
+ Supervisor/specialist topology is declarative and inspectable; tool calls are typed.
+ Two-level durability: Temporal across steps, LangGraph within a step.
− Two state stores to reason about; we keep the activity payload small (ids, not blobs).
