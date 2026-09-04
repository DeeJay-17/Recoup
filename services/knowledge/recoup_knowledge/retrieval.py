"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with reciprocal rank fusion.
Optional LLM rerank over the fused top-N for the strongest results."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Hit:
    chunk_id: int
    document_id: str
    kind: str
    title: str
    customer_ref: str | None
    chunk_no: int
    content: str
    metadata: dict[str, Any]
    score: float
    vector_rank: int | None = None
    fts_rank: int | None = None
    sources: list[str] = field(default_factory=list)


def rrf(rankings: dict[str, list[int]], *, k: int = 60) -> dict[int, tuple[float, dict[str, int]]]:
    """Reciprocal rank fusion. Returns chunk_id -> (score, {source: rank})."""
    out: dict[int, tuple[float, dict[str, int]]] = {}
    for source, ids in rankings.items():
        for rank, cid in enumerate(ids, start=1):
            score, ranks = out.get(cid, (0.0, {}))
            out[cid] = (score + 1.0 / (k + rank), {**ranks, source: rank})
    return out


async def hybrid_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query: str,
    query_vec: list[float],
    customer_ref: str | None,
    kinds: list[str] | None,
    k: int,
    k_vector: int = 20,
    k_fts: int = 20,
) -> list[Hit]:
    filters = ["c.tenant_id = :tenant"]
    params: dict[str, Any] = {"tenant": str(tenant_id), "kv": k_vector, "kf": k_fts, "q": query}
    if customer_ref:
        filters.append("(c.customer_ref = :cust OR c.customer_ref IS NULL)")
        params["cust"] = customer_ref
    if kinds:
        filters.append("c.kind = ANY(:kinds)")
        params["kinds"] = kinds
    where = " AND ".join(filters)
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
    params["qv"] = vec_literal

    vec_rows = (
        await session.execute(
            text(
                f"SELECT c.id FROM knowledge.chunks c WHERE {where} ORDER BY c.embedding <=> CAST(:qv AS vector) LIMIT :kv"
            ),
            params,
        )
    ).all()
    fts_rows = (
        await session.execute(
            text(
                f"SELECT c.id FROM knowledge.chunks c WHERE {where} AND c.tsv @@ websearch_to_tsquery('english', :q) "
                "ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('english', :q)) DESC LIMIT :kf"
            ),
            params,
        )
    ).all()
    fused = rrf({"vector": [r[0] for r in vec_rows], "fts": [r[0] for r in fts_rows]})
    if not fused:
        return []
    top = sorted(fused.items(), key=lambda kv: -kv[1][0])[: max(k * 2, k)]
    ids = [cid for cid, _ in top]
    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.document_id, c.kind, d.title, c.customer_ref, c.chunk_no, c.content, c.metadata "
                "FROM knowledge.chunks c JOIN knowledge.documents d ON d.id = c.document_id WHERE c.id = ANY(:ids)"
            ),
            {"ids": ids},
        )
    ).all()
    by_id = {r[0]: r for r in rows}
    hits: list[Hit] = []
    for cid, (score, ranks) in top:
        r = by_id.get(cid)
        if not r:
            continue
        hits.append(
            Hit(
                chunk_id=r[0],
                document_id=str(r[1]),
                kind=r[2],
                title=r[3],
                customer_ref=r[4],
                chunk_no=r[5],
                content=r[6],
                metadata=dict(r[7] or {}),
                score=round(score, 5),
                vector_rank=ranks.get("vector"),
                fts_rank=ranks.get("fts"),
                sources=sorted(ranks),
            )
        )
    # one chunk per document unless we have room
    seen_docs: set[str] = set()
    deduped: list[Hit] = []
    for h in hits:
        if h.document_id in seen_docs and len(hits) > k:
            continue
        seen_docs.add(h.document_id)
        deduped.append(h)
    return deduped[:k]
