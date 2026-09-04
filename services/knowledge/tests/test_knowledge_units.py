from recoup_knowledge.chunking import chunk_text
from recoup_knowledge.ingest import resolution_text
from recoup_knowledge.retrieval import rrf


def test_chunking_respects_size_and_overlap() -> None:
    text = "\n\n".join(f"Paragraph {i} " + ("lorem ipsum " * 30) for i in range(8))
    chunks = chunk_text(text, size=600, overlap=100)
    assert len(chunks) >= 4
    assert all(len(c) <= 720 for c in chunks)
    assert chunk_text("", size=100, overlap=10) == []


def test_rrf_fuses_rankings() -> None:
    fused = rrf({"vector": [1, 2, 3], "fts": [3, 4]})
    assert fused[3][0] > fused[1][0]  # appears in both lists
    assert fused[3][1] == {"vector": 3, "fts": 1}
    assert 4 in fused and 2 in fused


def test_resolution_text_mentions_key_facts() -> None:
    detail = {
        "case": {
            "id": "c1",
            "customer_ref": "CUST-1",
            "customer_name": "Acme",
            "invoice_refs": ["INV-1"],
            "amount_open": "10.00",
            "currency": "USD",
            "days_overdue": 12,
            "root_cause": "DISPUTE_PRICING",
            "root_cause_conf": 0.9,
            "status": "RESOLVED",
            "resolution": {"credit_memo": "5.00"},
        },
        "actions": [
            {"action_type": "CREATE_CREDIT_MEMO", "status": "EXECUTED", "policy_decision": "ALLOW"}
        ],
        "timeline": [
            {"kind": "agent_step", "title": "Reconciler: credit memo 5.00 proposed", "payload": {}}
        ],
    }
    t = resolution_text(detail, [{"direction": "IN", "subject": "Re: x", "body_text": "thanks"}])
    assert "DISPUTE_PRICING" in t and "CREATE_CREDIT_MEMO EXECUTED" in t and "[Customer] Re: x" in t


async def test_heuristic_memory_writer_extracts_facts() -> None:
    from recoup_knowledge.memory import extract_facts
    from recoup_llm import HeuristicLLM, LLMSettings, build_router

    router = build_router(LLMSettings(provider="heuristic", _env_file=None))  # type: ignore[call-arg]
    out = await extract_facts(
        router.for_tier("fast"),
        narrative="Root cause: MISSING_PO. Customer accepted the proposed payment plan.",
        existing=[],
    )
    cats = {f.category for f in out.facts}
    assert "PREFERENCE" in cats and len(out.facts) >= 2
    assert isinstance(router.for_tier("fast"), HeuristicLLM)
