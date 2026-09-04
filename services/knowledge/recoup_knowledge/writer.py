"""Event-driven indexing and memory writing (the 'MemoryWriter' step of the plan)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from recoup_common.db import Database
from recoup_common.events import DomainEvent
from recoup_common.logging import get_logger
from recoup_erp import MockERPAdapter
from recoup_llm import EmbeddingClient, ModelRouter

from recoup_knowledge import ingest, memory
from recoup_knowledge.clients import CaseClient, CommClient, resolve_tenant_id
from recoup_knowledge.settings import Settings

log = get_logger(__name__)
SOP_DIR = Path(__file__).parent / "sops"


class Writer:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        embedder: EmbeddingClient,
        router: ModelRouter,
        cases: CaseClient,
        comm: CommClient,
        erp: MockERPAdapter,
    ) -> None:
        self.db, self.s, self.embedder, self.router = db, settings, embedder, router
        self.cases, self.comm, self.erp = cases, comm, erp

    async def handle_event(self, event: DomainEvent) -> None:
        tenant_id = uuid.UUID(event.tenantid)
        d = event.data
        if event.type == "case.resolved" and d.get("case_id"):
            await self.write_for_case(tenant_id, uuid.UUID(d["case_id"]))
        elif event.type in ("comm.email.received", "comm.email.sent") and d.get("case_id"):
            await self.index_email(tenant_id, uuid.UUID(d["case_id"]), d.get("message_id"))

    async def index_email(
        self, tenant_id: uuid.UUID, case_id: uuid.UUID, message_id: str | None
    ) -> None:
        msgs = await self.comm.messages(tenant_id, case_id)
        m = next((x for x in msgs if x["message_id"] == message_id), None)
        if not m:
            return
        detail = await self.cases.detail(tenant_id, case_id)
        cust = detail["case"]["customer_ref"] if detail else None
        who = "Customer" if m["direction"] == "IN" else "Us"
        text = f"{who} wrote on case {case_id} ({', '.join(m.get('invoice_refs') or [])}):\nSubject: {m['subject']}\n\n{m['body_text']}"
        async with self.db.session() as session:
            await ingest.upsert_document(
                session,
                self.embedder,
                tenant_id=tenant_id,
                kind="email",
                title=m["subject"][:200],
                text=text,
                customer_ref=cust,
                source_ref=f"email:{m['message_id']}",
                metadata={
                    "case_id": str(case_id),
                    "direction": m["direction"],
                    "from": m["from_addr"],
                },
                chunk_chars=self.s.chunk_chars,
                overlap=self.s.chunk_overlap,
            )

    async def write_for_case(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
        detail = await self.cases.detail(tenant_id, case_id)
        if not detail:
            return {"indexed": False, "facts": 0}
        msgs = await self.comm.messages(tenant_id, case_id)
        narrative = ingest.resolution_text(detail, msgs)
        c = detail["case"]
        async with self.db.session() as session:
            await ingest.upsert_document(
                session,
                self.embedder,
                tenant_id=tenant_id,
                kind="resolution",
                title=f"{c.get('root_cause') or 'case'} · {c['customer_ref']} · {', '.join(c['invoice_refs'])} · {c['status']}",
                text=narrative,
                customer_ref=c["customer_ref"],
                source_ref=f"case:{case_id}",
                metadata={
                    "case_id": str(case_id),
                    "root_cause": c.get("root_cause"),
                    "status": c["status"],
                    "amount_open": str(c["amount_open"]),
                },
                chunk_chars=self.s.chunk_chars,
                overlap=self.s.chunk_overlap,
            )
            existing = [
                m.fact for m in await memory.list_memory(session, tenant_id, c["customer_ref"])
            ]
        extracted = await memory.extract_facts(
            self.router.for_tier("fast"), narrative=narrative, existing=existing
        )
        written = 0
        async with self.db.session() as session:
            for f in extracted.facts:
                if f.confidence < self.s.memory_min_confidence:
                    continue
                await memory.add_fact(
                    session,
                    self.embedder,
                    tenant_id=tenant_id,
                    customer_ref=c["customer_ref"],
                    fact=f.fact,
                    category=f.category,
                    confidence=f.confidence,
                    created_by="agent:memory_writer",
                    source_case_id=case_id,
                    source_ref=f.evidence[:200],
                )
                written += 1
        log.info("knowledge.case_written", case_id=str(case_id), facts=written)
        return {
            "indexed": True,
            "facts": written,
            "facts_detail": [f.model_dump() for f in extracted.facts],
        }

    async def seed(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """SOPs from the repo + a contract summary per ERP customer."""
        n_sop = n_contract = 0
        async with self.db.session() as session:
            for f in sorted(SOP_DIR.glob("*.md")):
                await ingest.upsert_document(
                    session,
                    self.embedder,
                    tenant_id=tenant_id,
                    kind="sop",
                    title=f.stem.replace("-", " ").title(),
                    text=f.read_text(),
                    customer_ref=None,
                    source_ref=f"sop:{f.stem}",
                    metadata={"file": f.name},
                    chunk_chars=self.s.chunk_chars,
                    overlap=self.s.chunk_overlap,
                )
                n_sop += 1
        invoices = await self.erp.list_open_invoices(limit=2000)
        refs = sorted({i.customer_ref for i in invoices})
        for ref in refs:
            try:
                cust = await self.erp.get_customer(ref)
                terms = await self.erp.get_contract_terms(ref)
            except Exception as e:
                log.warning("knowledge.seed_customer_failed", customer_ref=ref, error=str(e))
                continue
            active = [c for c in cust.contacts if c.active]
            text = (
                f"Contract summary for {cust.name} ({ref}), effective {terms.effective_from}.\n\n"
                f"Payment terms: net {terms.payment_terms_days} days. Purchase order required on every invoice: {'yes' if cust.requires_po else 'no'}. "
                f"Freight is {'billable' if terms.freight_billable else 'NOT billable'} to the customer. Early-payment discount: {terms.early_pay_discount_pct}%. "
                f"Maximum discount agents may offer: {terms.max_discount_pct}%. Dispute window: {terms.dispute_window_days} days from invoice date.\n\n"
                f"Credit hold: {'yes' if cust.credit_hold else 'no'}. Credit risk score: {cust.credit_risk_score:.2f}.\n\n"
                "Accounts payable contacts: "
                + "; ".join(f"{c.name} <{c.email}> ({c.role})" for c in active)
                + ".\n\n"
                "Negotiated price list (SKU: unit price): "
                + ", ".join(f"{k}: {v}" for k, v in sorted(terms.price_list.items()))
                + "."
            )
            async with self.db.session() as session:
                await ingest.upsert_document(
                    session,
                    self.embedder,
                    tenant_id=tenant_id,
                    kind="contract",
                    title=f"Contract · {cust.name}",
                    text=text,
                    customer_ref=ref,
                    source_ref=f"contract:{ref}",
                    metadata={"customer_name": cust.name},
                    chunk_chars=self.s.chunk_chars,
                    overlap=self.s.chunk_overlap,
                )
            n_contract += 1
        return {"sops": n_sop, "contracts": n_contract}


async def default_tenant(settings: Settings) -> uuid.UUID:
    return await resolve_tenant_id(settings)
