"""Per-customer episodic memory: durable facts with provenance, written after each case."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from recoup_common.db import utcnow
from recoup_common.errors import NotFoundError
from recoup_common.events.outbox import enqueue_event
from recoup_llm import AssistantTurn, EmbeddingClient, HeuristicLLM, LLMClient, Message, ToolSchema
from recoup_llm.heuristic import last_tool_results  # noqa: F401  (re-exported for policies)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_knowledge.models import CustomerMemory, Outbox

Category = Literal["CONTACT", "PREFERENCE", "PATTERN", "RISK"]
SOURCE = "knowledge-service"


class Fact(BaseModel):
    fact: str = Field(min_length=8, max_length=300)
    category: Category
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(max_length=300, description="What in the case supports this, briefly")

    @field_validator("fact", "evidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> Any:
        # Models happily over-explain; clamp instead of rejecting a whole batch of facts.
        return v[:300].rstrip() if isinstance(v, str) and len(v) > 300 else v


class ExtractedFacts(BaseModel):
    facts: list[Fact] = Field(max_length=8)


EXTRACT_PROMPT = """You maintain a short memory about a B2B customer for an accounts-receivable team.
From the case narrative below, extract durable facts that will help the next case with this customer:
who the working AP contact is (CONTACT), how they like to be handled and what they require, e.g. a PO on every invoice (PREFERENCE),
recurring behaviours such as disputing freight or paying after the first reminder (PATTERN), and credit or relationship risks (RISK).
Rules: facts must be supported by the narrative (cite it in evidence), be specific and reusable, and never restate one-off amounts or dates.
Skip anything already in the existing memory. Return at most 6 facts via submit_facts; return an empty list if nothing durable was learned."""


async def list_memory(
    session: AsyncSession, tenant_id: uuid.UUID, customer_ref: str
) -> list[CustomerMemory]:
    rows = await session.scalars(
        select(CustomerMemory)
        .where(
            CustomerMemory.tenant_id == tenant_id,
            CustomerMemory.customer_ref == customer_ref,
            CustomerMemory.superseded_by.is_(None),
        )
        .order_by(CustomerMemory.category, CustomerMemory.created_at.desc())
    )
    return list(rows.all())


async def add_fact(
    session: AsyncSession,
    embedder: EmbeddingClient,
    *,
    tenant_id: uuid.UUID,
    customer_ref: str,
    fact: str,
    category: str,
    confidence: float,
    created_by: str,
    source_case_id: uuid.UUID | None,
    source_ref: str | None,
) -> CustomerMemory:
    existing = await list_memory(session, tenant_id, customer_ref)
    vec = await embedder.embed_query(fact)
    # near-duplicate check: supersede a very similar fact instead of stacking
    for m in existing:
        if m.embedding is not None and _cos(m.embedding, vec) > 0.93 and m.category == category:
            row = CustomerMemory(
                tenant_id=tenant_id,
                customer_ref=customer_ref,
                fact=fact,
                category=category,
                confidence=Decimal(str(round(max(confidence, float(m.confidence)), 3))),
                created_by=created_by,
                source_case_id=source_case_id,
                source_ref=source_ref,
                embedding=vec,
                created_at=utcnow(),
            )
            session.add(row)
            await session.flush()
            m.superseded_by, m.superseded_at = row.id, utcnow()
            return row
    row = CustomerMemory(
        tenant_id=tenant_id,
        customer_ref=customer_ref,
        fact=fact,
        category=category,
        confidence=Decimal(str(round(confidence, 3))),
        created_by=created_by,
        source_case_id=source_case_id,
        source_ref=source_ref,
        embedding=vec,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant_id,
        aggregate_id=source_case_id or row.id,
        event_type="knowledge.memory.added",
        payload={
            "memory_id": str(row.id),
            "customer_ref": customer_ref,
            "category": category,
            "fact": fact,
            "created_by": created_by,
        },
    )
    return row


async def supersede(
    session: AsyncSession, tenant_id: uuid.UUID, memory_id: uuid.UUID, *, by: str
) -> CustomerMemory:
    m = await session.get(CustomerMemory, memory_id)
    if not m or m.tenant_id != tenant_id:
        raise NotFoundError("memory not found")
    m.superseded_by, m.superseded_at = uuid.UUID(int=0), utcnow()
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=tenant_id,
        aggregate_id=m.id,
        event_type="knowledge.memory.retired",
        payload={"memory_id": str(m.id), "customer_ref": m.customer_ref, "by": by},
    )
    return m


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


async def extract_facts(llm: LLMClient, *, narrative: str, existing: list[str]) -> ExtractedFacts:
    tool = ToolSchema(
        name="submit_facts",
        description="Submit the extracted durable customer facts.",
        parameters=ExtractedFacts.model_json_schema(),
    )
    msgs = [
        Message(role="system", content=EXTRACT_PROMPT),
        Message(
            role="user",
            content="Existing memory:\n- "
            + ("\n- ".join(existing) if existing else "(none)")
            + "\n\nCase narrative:\n"
            + narrative[:12000]
            + "\n\nCall submit_facts.",
        ),
    ]
    for _attempt in range(3):
        turn = await llm.chat(msgs, tools=[tool], task="memory_writer")
        call = next((c for c in turn.tool_calls if c.name == "submit_facts"), None)
        if call is None:
            msgs += [
                turn.as_message(),
                Message(role="user", content="Call submit_facts now (an empty list is fine)."),
            ]
            continue
        try:
            return ExtractedFacts.model_validate(call.args)
        except ValidationError as e:
            msgs += [
                turn.as_message(),
                Message(
                    role="tool",
                    name="submit_facts",
                    tool_call_id=call.id,
                    content=json.dumps(
                        {"accepted": False, "errors": e.errors(include_url=False)[:4]}
                    ),
                ),
            ]
    return ExtractedFacts(facts=[])


# ---------- heuristic memory writer (key-less mode) ----------
def _heuristic_memory(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    from recoup_llm import ToolCall

    text = messages[-1].content if messages else ""
    narrative = text.split("Case narrative:", 1)[-1].lower()
    facts: list[dict[str, Any]] = []
    if (
        "missing_po" in narrative
        or "requires a valid po" in narrative
        or "po number needed" in narrative
    ):
        facts.append(
            {
                "fact": "Requires a purchase-order number on every invoice before AP will pay.",
                "category": "PREFERENCE",
                "confidence": 0.8,
                "evidence": "MISSING_PO root cause",
            }
        )
    if "wrong_contact" in narrative or "left the company" in narrative:
        facts.append(
            {
                "fact": "Invoices must go to the currently active AP contact; a former contact's mailbox is dead.",
                "category": "CONTACT",
                "confidence": 0.75,
                "evidence": "WRONG_CONTACT root cause",
            }
        )
    if "short_pay" in narrative or "less freight" in narrative:
        facts.append(
            {
                "fact": "Tends to deduct freight from remittances; check the contract's freight clause first.",
                "category": "PATTERN",
                "confidence": 0.7,
                "evidence": "SHORT_PAY root cause",
            }
        )
    if "dispute_pricing" in narrative:
        facts.append(
            {
                "fact": "Disputes invoices when unit prices deviate from the PO; reconcile prices before outreach.",
                "category": "PATTERN",
                "confidence": 0.7,
                "evidence": "DISPUTE_PRICING root cause",
            }
        )
    if "cash_flow" in narrative or "payment plan" in narrative:
        facts.append(
            {
                "fact": "Slow payer; responds to a short payment plan offer rather than reminders.",
                "category": "RISK",
                "confidence": 0.65,
                "evidence": "CASH_FLOW / plan accepted",
            }
        )
    if "accepts_offer" in narrative or "accepted the proposed payment plan" in narrative:
        facts.append(
            {
                "fact": "Has accepted a 3-installment payment plan in the past; open to plans.",
                "category": "PREFERENCE",
                "confidence": 0.7,
                "evidence": "ACCEPTS_OFFER",
            }
        )
    return AssistantTurn(
        tool_calls=[ToolCall(id="m1", name="submit_facts", args={"facts": facts[:6]})]
    )


HeuristicLLM.register("memory_writer", _heuristic_memory)
