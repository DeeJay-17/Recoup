"""Documents in, chunks + embeddings out. Idempotent on (tenant, kind, source_ref)."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from recoup_common.db import utcnow
from recoup_common.events.outbox import enqueue_event
from recoup_llm import EmbeddingClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_knowledge.chunking import chunk_text
from recoup_knowledge.models import Chunk, Document, Outbox

SOURCE = "knowledge-service"


async def upsert_document(
    session: AsyncSession,
    embedder: EmbeddingClient,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    title: str,
    text: str,
    customer_ref: str | None,
    source_ref: str | None,
    metadata: dict[str, Any] | None = None,
    chunk_chars: int = 1200,
    overlap: int = 150,
) -> Document:
    source_ref = source_ref or hashlib.sha1(text.encode()).hexdigest()
    existing = await session.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.kind == kind,
            Document.source_ref == source_ref,
        )
    )
    if existing and existing.content_text == text:
        return existing
    if existing:
        await session.execute(delete(Chunk).where(Chunk.document_id == existing.id))
        doc = existing
        doc.title, doc.content_text, doc.customer_ref, doc.metadata_ = (
            title,
            text,
            customer_ref,
            metadata or {},
        )
    else:
        doc = Document(
            tenant_id=tenant_id,
            kind=kind,
            title=title,
            customer_ref=customer_ref,
            source_ref=source_ref,
            content_text=text,
            metadata_=metadata or {},
            created_at=utcnow(),
        )
        session.add(doc)
        await session.flush()
    pieces = chunk_text(text, size=chunk_chars, overlap=overlap) or [text[:chunk_chars]]
    vectors = await embedder.embed([f"{title}\n\n{p}" for p in pieces])
    for i, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
        session.add(
            Chunk(
                tenant_id=tenant_id,
                document_id=doc.id,
                kind=kind,
                customer_ref=customer_ref,
                chunk_no=i,
                content=piece,
                embedding=vec,
                metadata_={"title": title, **(metadata or {})},
            )
        )
    doc.chunk_count = len(pieces)
    await session.flush()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant_id,
        aggregate_id=doc.id,
        event_type="knowledge.document.indexed",
        payload={
            "document_id": str(doc.id),
            "kind": kind,
            "title": title,
            "customer_ref": customer_ref,
            "chunks": len(pieces),
        },
    )
    return doc


def resolution_text(detail: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """Render a resolved/escalated case into a searchable narrative for 'similar cases'."""
    c = detail["case"]
    lines = [
        f"Case {c['id']} for customer {c.get('customer_name') or c['customer_ref']} ({c['customer_ref']}), "
        f"invoice(s) {', '.join(c['invoice_refs'])}, {c['amount_open']} {c['currency']} open, {c['days_overdue']} days overdue.",
        f"Root cause: {c.get('root_cause')} (confidence {c.get('root_cause_conf')}). Final status: {c['status']}.",
    ]
    res = c.get("resolution") or {}
    if res:
        lines.append(f"Resolution: {res}")
    acts = [
        f"{a['action_type']} {a['status']} ({a['policy_decision']})"
        for a in detail.get("actions", [])
    ]
    if acts:
        lines.append("Actions: " + "; ".join(acts))
    steps = [
        e["title"]
        for e in detail.get("timeline", [])
        if e["kind"] in ("agent_step", "escalation_brief", "approval", "state_change")
    ]
    if steps:
        lines.append("What happened:\n- " + "\n- ".join(steps[-25:]))
    briefs = [
        e["payload"].get("summary")
        for e in detail.get("timeline", [])
        if e["kind"] == "escalation_brief" and e.get("payload", {}).get("summary")
    ]
    if briefs:
        lines.append("Escalation brief:\n" + briefs[-1])
    if messages:
        lines.append("Correspondence:")
        for m in messages[-6:]:
            who = "Customer" if m["direction"] == "IN" else "Us"
            lines.append(f"[{who}] {m['subject']}: {m['body_text'][:600]}")
    return "\n\n".join(lines)
