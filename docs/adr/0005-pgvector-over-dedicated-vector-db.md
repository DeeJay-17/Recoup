# ADR-0005: pgvector + Postgres FTS instead of a dedicated vector database

**Status:** accepted (implemented in phase 5) · **Date:** 2026-09-04

## Context
Retrieval corpus is modest (contracts, email threads, past resolutions per tenant) and must be
filtered by `tenant_id` / `customer_ref` and joined with relational data.

## Decision
`knowledge.chunks(embedding vector, tsv tsvector)` with an HNSW index and a GIN index; hybrid
retrieval via reciprocal rank fusion, then a cross-encoder rerank. Same cluster, same backups,
same RLS.

## Consequences
+ One database, transactional ingestion, trivial tenant isolation.
− Scale ceiling is lower than purpose-built stores; acceptable for this workload, and the
  Knowledge Service API hides the store so it can be swapped.
