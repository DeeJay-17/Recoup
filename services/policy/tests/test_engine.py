from typing import Any

import pytest
from recoup_common.errors import ValidationError
from recoup_policy.defaults import DEFAULT_POLICIES
from recoup_policy.engine import Decision, PolicyRule, evaluate, matches, validate_rule


def _rules() -> list[PolicyRule]:
    return [
        PolicyRule(
            policy_id=str(i),
            name=d["name"],
            action_type=d["action_type"],
            priority=d["priority"],
            rule=d["rule"],
            decision=Decision(d["decision"]),
            required_role=d.get("required_role"),
            reason=d.get("reason"),
        )
        for i, d in enumerate(DEFAULT_POLICIES)
    ]


def test_leaf_ops() -> None:
    ctx = {"a": {"n": "12.50", "s": "hello", "l": ["x", "y"]}}
    assert matches({"fact": "a.n", "op": "<=", "value": 12.5}, ctx)
    assert not matches({"fact": "a.n", "op": ">", "value": 12.5}, ctx)
    assert matches({"fact": "a.s", "op": "matches", "value": "^hel"}, ctx)
    assert matches({"fact": "a.l", "op": "contains", "value": "x"}, ctx)
    assert matches({"fact": "a.s", "op": "in", "value": ["hello", "bye"]}, ctx)
    assert matches({"fact": "a.zzz", "op": "not_exists"}, ctx)
    # missing facts are never truthy comparisons
    assert not matches({"fact": "a.zzz", "op": "==", "value": None}, ctx)
    assert not matches({"fact": "a.zzz", "op": "<", "value": 1}, ctx)


def test_combinators() -> None:
    ctx = {"x": 1, "y": 2}
    assert matches(
        {"all": [{"fact": "x", "op": "==", "value": 1}, {"fact": "y", "op": "==", "value": 2}]}, ctx
    )
    assert not matches(
        {"all": [{"fact": "x", "op": "==", "value": 1}, {"fact": "y", "op": "==", "value": 3}]}, ctx
    )
    assert matches(
        {"any": [{"fact": "x", "op": "==", "value": 9}, {"fact": "y", "op": "==", "value": 2}]}, ctx
    )
    assert matches({"not": {"fact": "x", "op": "==", "value": 9}}, ctx)


def test_validate_rule_rejects_garbage() -> None:
    bad: Any
    for bad in (
        {},
        {"all": []},
        {"fact": "x"},
        {"fact": "x", "op": "??", "value": 1},
        {"fact": "x", "op": "<"},
    ):
        with pytest.raises(ValidationError):
            validate_rule(bad)
    for d in DEFAULT_POLICIES:
        validate_rule(d["rule"])


def test_small_credit_memo_autonomous() -> None:
    ctx = {
        "action": {"amount": "412.10"},
        "case": {"root_cause": "DISPUTE_PRICING", "root_cause_conf": 0.92},
        "evidence": {"has_po_match": True},
    }
    r = evaluate("CREATE_CREDIT_MEMO", ctx, _rules())
    assert r.decision is Decision.ALLOW
    assert "small_credit_memo_autonomy" in [m["name"] for m in r.matched]


def test_missing_evidence_falls_to_analyst_approval() -> None:
    ctx = {
        "action": {"amount": "412.10"},
        "case": {"root_cause": "DISPUTE_PRICING", "root_cause_conf": 0.92},
    }
    r = evaluate("CREATE_CREDIT_MEMO", ctx, _rules())
    assert r.decision is Decision.REQUIRE_APPROVAL
    assert r.required_role == "analyst"


def test_large_credit_memo_strictest_role_wins() -> None:
    ctx = {
        "action": {"amount": "7200"},
        "case": {"root_cause": "DISPUTE_PRICING", "root_cause_conf": 0.99},
        "evidence": {"has_po_match": True},
    }
    r = evaluate("CREATE_CREDIT_MEMO", ctx, _rules())
    assert r.decision is Decision.REQUIRE_APPROVAL
    assert r.required_role == "manager"


def test_deny_beats_everything() -> None:
    ctx = {
        "customer": {"credit_hold": True},
        "action": {"discount_pct": 0, "extension_days": 10, "installments": 1},
    }
    r = evaluate("PAYMENT_PLAN", ctx, _rules())
    assert r.decision is Decision.DENY
    assert "credit hold" in (r.reason or "")


def test_email_tone_gate_and_routine_allow() -> None:
    base = {"email": {"to": "ap@x.com", "template": "po_request", "recipient_known": True}}
    low = {"email": {**base["email"], "tone_score": 0.4}}
    ok = {"email": {**base["email"], "tone_score": 0.9}}
    assert evaluate("SEND_EMAIL", low, _rules()).decision is Decision.DENY
    assert evaluate("SEND_EMAIL", ok, _rules()).decision is Decision.ALLOW
    freeform = {"email": {"to": "ap@x.com", "tone_score": 0.9}}
    r = evaluate("SEND_EMAIL", freeform, _rules())
    assert r.decision is Decision.REQUIRE_APPROVAL and r.required_role == "analyst"


def test_no_match_uses_safe_default() -> None:
    r = evaluate("UNKNOWN_ACTION", {}, _rules())
    assert r.decision is Decision.REQUIRE_APPROVAL
    assert r.defaulted
    r2 = evaluate("UNKNOWN_ACTION", {}, _rules(), default_decision=Decision.DENY)
    assert r2.decision is Decision.DENY
