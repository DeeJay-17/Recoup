from decimal import Decimal
from typing import Any

from recoup_evals.scoring import aggregate, compare, predicted_credit, score_case, triage_top_cause

STEPS: list[dict[str, Any]] = [
    {
        "agent_name": "supervisor",
        "status": "SUCCESS",
        "output": {"effective_next": "Triage"},
        "tool_calls": [],
    },
    {
        "agent_name": "triage",
        "status": "SUCCESS",
        "tool_calls": [{"tool": "get_invoice", "status": "SUCCESS"}],
        "output": {
            "root_cause_hypotheses": [
                {"cause": "CASH_FLOW", "confidence": 0.3},
                {"cause": "DISPUTE_PRICING", "confidence": 0.8},
            ]
        },
    },
    {
        "agent_name": "investigator",
        "status": "SUCCESS",
        "tool_calls": [{"tool": "reconcile_lines", "status": "SUCCESS"}],
        "output": {
            "confirmed_cause": "DISPUTE_PRICING",
            "confidence": 0.9,
            "proposed_credit_memo": "107.00",
            "gaps": [],
        },
    },
    {
        "agent_name": "reconciler",
        "status": "SUCCESS",
        "tool_calls": [{"tool": "propose_action", "status": "SUCCESS"}],
        "output": {"proposed_credit_memo": "107.40", "nothing_owed": False},
    },
]
RUN = {
    "status": "ESCALATED",
    "steps": 6,
    "tokens_in": 100,
    "tokens_out": 50,
    "cost_usd": "0.02",
    "started_at": "2026-09-04T10:00:00+00:00",
    "ended_at": "2026-09-04T10:00:30+00:00",
    "outcome": {"status": "ESCALATED", "reason": "handed to a human"},
}


def test_triage_top_cause_takes_highest_confidence() -> None:
    assert triage_top_cause(STEPS) == ("DISPUTE_PRICING", 0.8)


def test_reconciler_owns_the_credit_number() -> None:
    assert predicted_credit(STEPS) == Decimal("107.40")
    no_rec = [s for s in STEPS if s["agent_name"] != "reconciler"]
    assert predicted_credit(no_rec) == Decimal("107.00")
    nothing = [
        *no_rec,
        {
            "agent_name": "reconciler",
            "status": "SUCCESS",
            "output": {"nothing_owed": True},
            "tool_calls": [],
        },
    ]
    assert predicted_credit(nothing) == Decimal("0")


def test_score_case_passes_within_tolerance() -> None:
    s = score_case(
        expected_root_cause="DISPUTE_PRICING",
        expected_credit=Decimal("107.00"),
        run=RUN,
        steps=STEPS,
        invocations=[],
    )
    assert s.passed and s.credit_delta == Decimal("0.40") and s.latency_ms == 30000
    assert s.tool_calls == 3 and s.unauthorized_mutations == 0


def test_score_case_flags_wrong_cause_and_credit() -> None:
    s = score_case(
        expected_root_cause="MISSING_PO",
        expected_credit=Decimal("0"),
        run=RUN,
        steps=STEPS,
        invocations=[],
    )
    assert not s.passed and len(s.failures) == 2


def test_executed_mutation_in_shadow_is_a_failure() -> None:
    s = score_case(
        expected_root_cause="DISPUTE_PRICING",
        expected_credit=Decimal("107.00"),
        run=RUN,
        steps=STEPS,
        invocations=[
            {"tool": "create_credit_memo", "status": "SUCCESS"},
            {"tool": "get_invoice", "status": "SUCCESS"},
            {"tool": "send_email", "status": "SHADOW"},
        ],
    )
    assert s.unauthorized_mutations == 1 and not s.passed


def test_aggregate_and_compare() -> None:
    results: list[dict[str, Any]] = [
        {
            "status": "OK",
            "passed": True,
            "expected_root_cause": "A",
            "triage_root_cause": "A",
            "predicted_root_cause": "A",
            "credit_delta": Decimal("0.10"),
            "steps": 5,
            "tool_calls": 4,
            "policy_denials": 0,
            "approval_gates": 1,
            "unauthorized_mutations": 0,
            "tokens": 100,
            "cost_usd": Decimal("0.01"),
            "latency_ms": 1000,
            "scores": {"faithfulness": 5},
        },
        {
            "status": "OK",
            "passed": False,
            "expected_root_cause": "B",
            "triage_root_cause": "A",
            "predicted_root_cause": "A",
            "credit_delta": Decimal("9.00"),
            "steps": 7,
            "tool_calls": 6,
            "policy_denials": 1,
            "approval_gates": 0,
            "unauthorized_mutations": 0,
            "tokens": 200,
            "cost_usd": Decimal("0.03"),
            "latency_ms": 3000,
            "scores": {"faithfulness": 3},
        },
        {"status": "ERROR", "passed": False, "unauthorized_mutations": 0},
    ]
    m = aggregate(results)
    assert m["cases"] == 3 and m["scored"] == 2 and m["errors"] == 1
    assert m["root_cause_accuracy"] == 0.5 and m["credit_accuracy"] == 0.5
    assert m["judge_faithfulness"] == 4.0 and m["by_root_cause"]["A"]["accuracy"] == 1.0
    d = compare(
        {"root_cause_accuracy": 0.5, "avg_cost_usd": 0.02},
        {"root_cause_accuracy": 0.75, "avg_cost_usd": 0.01},
    )
    assert d["root_cause_accuracy"]["delta_points"] == 25.0 and d["avg_cost_usd"]["delta"] == -0.01


async def test_heuristic_judge_scores_evidence() -> None:
    from recoup_evals.judge import build_evidence, judge_run
    from recoup_llm import LLMSettings, build_router

    llm = build_router(LLMSettings(provider="heuristic", _env_file=None)).for_tier("strong")  # type: ignore[call-arg]
    steps = [
        {
            "agent_name": "investigator",
            "status": "SUCCESS",
            "output": {
                "summary": "priced above PO",
                "evidence": [{"source": "reconcile_lines", "ref": "INV-1", "finding": "6 > 5"}],
            },
            "messages": [
                {"role": "tool", "name": "reconcile_lines", "content": "{...}"},
                {"role": "tool", "name": "get_invoice", "content": "{...}"},
            ],
        },
    ]
    assert "cited:" in build_evidence(steps)
    j = await judge_run(llm, steps)
    assert j is not None and 1 <= j.faithfulness <= 5
    assert await judge_run(llm, []) is None
