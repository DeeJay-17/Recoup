"""Deterministic rule evaluator.

Rule grammar (JSON):
    {"all": [<rule>, ...]}                      every sub-rule must hold
    {"any": [<rule>, ...]}                      at least one must hold
    {"not": <rule>}                             negation
    {"fact": "action.amount", "op": "<=", "value": 1000}
    {"fact": "case.root_cause", "op": "in", "value": ["DISPUTE_PRICING"]}
    {"fact": "evidence.has_po_match", "op": "exists"}

``fact`` is a dotted path into the evaluation context. Missing facts never raise: they make the
comparison false (and ``exists`` false), so an absent value can never accidentally ALLOW.
Numbers are compared as Decimals so money is exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from recoup_common.errors import ValidationError

_MISSING = object()
ROLE_RANK = {"viewer": 0, "analyst": 1, "manager": 2, "admin": 3}


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


def get_fact(context: dict[str, Any], path: str) -> Any:
    cur: Any = context
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _num(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float | Decimal):
        return Decimal(str(v))
    if isinstance(v, str):
        try:
            return Decimal(v)
        except InvalidOperation:
            return None
    return None


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return actual is not _MISSING and actual is not None
    if op == "not_exists":
        return actual is _MISSING or actual is None
    if actual is _MISSING:
        return False
    if op in ("==", "!="):
        a, e = _num(actual), _num(expected)
        eq = (a == e) if (a is not None and e is not None) else (actual == expected)
        return eq if op == "==" else not eq
    if op in ("<", "<=", ">", ">="):
        a, e = _num(actual), _num(expected)
        if a is None or e is None:
            return False
        return {"<": a < e, "<=": a <= e, ">": a > e, ">=": a >= e}[op]
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "contains":
        return isinstance(actual, list | str) and expected in actual
    if op == "matches":
        return isinstance(actual, str) and re.search(str(expected), actual) is not None
    if op == "startswith":
        return isinstance(actual, str) and actual.startswith(str(expected))
    raise ValidationError(f"unknown operator '{op}'")


OPS = {
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "contains",
    "matches",
    "startswith",
    "exists",
    "not_exists",
}


def validate_rule(rule: Any) -> None:
    """Raise ValidationError if the rule is malformed. Cheap; run on every save."""
    if not isinstance(rule, dict):
        raise ValidationError("rule must be an object")
    if "all" in rule or "any" in rule:
        key = "all" if "all" in rule else "any"
        if not isinstance(rule[key], list) or not rule[key]:
            raise ValidationError(f"'{key}' must be a non-empty list")
        for sub in rule[key]:
            validate_rule(sub)
        return
    if "not" in rule:
        validate_rule(rule["not"])
        return
    if "fact" not in rule or "op" not in rule:
        raise ValidationError("leaf rule needs 'fact' and 'op'")
    if rule["op"] not in OPS:
        raise ValidationError(f"unknown operator '{rule['op']}'")
    if rule["op"] not in ("exists", "not_exists") and "value" not in rule:
        raise ValidationError(f"operator '{rule['op']}' needs a 'value'")


def matches(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    if "all" in rule:
        return all(matches(r, context) for r in rule["all"])
    if "any" in rule:
        return any(matches(r, context) for r in rule["any"])
    if "not" in rule:
        return not matches(rule["not"], context)
    return _compare(get_fact(context, rule["fact"]), rule["op"], rule.get("value"))


@dataclass
class PolicyRule:
    """A compiled, enabled policy version ready for evaluation."""

    policy_id: str
    name: str
    action_type: str  # concrete type or "*"
    priority: int
    rule: dict[str, Any]
    decision: Decision
    required_role: str | None = None
    reason: str | None = None
    version: int = 1


@dataclass
class EvaluationResult:
    decision: Decision
    required_role: str | None
    reason: str | None
    matched: list[dict[str, Any]] = field(default_factory=list)
    defaulted: bool = False


def evaluate(
    action_type: str,
    context: dict[str, Any],
    rules: list[PolicyRule],
    *,
    default_decision: Decision = Decision.REQUIRE_APPROVAL,
    default_role: str = "analyst",
) -> EvaluationResult:
    """A DENY anywhere wins. Otherwise the highest-priority (lowest number) matching rule decides.

    That lets tenants layer specific ALLOW rules above catch-all REQUIRE_APPROVAL fallbacks. If
    nothing matches, fall back to the (safe) default. Tools without side effects are never
    evaluated, so the default only ever gates mutations.
    """
    applicable = sorted(
        (r for r in rules if r.action_type in (action_type, "*")),
        key=lambda r: (r.priority, r.name),
    )
    hits = [r for r in applicable if matches(r.rule, context)]
    matched = [
        {
            "policy_id": r.policy_id,
            "name": r.name,
            "version": r.version,
            "decision": r.decision.value,
        }
        for r in hits
    ]
    denies = [r for r in hits if r.decision is Decision.DENY]
    if denies:
        r = denies[0]
        return EvaluationResult(Decision.DENY, None, r.reason or f"denied by {r.name}", matched)
    if hits:
        r = hits[0]
        if r.decision is Decision.REQUIRE_APPROVAL:
            return EvaluationResult(
                Decision.REQUIRE_APPROVAL,
                r.required_role or "analyst",
                r.reason or f"approval required by {r.name}",
                matched,
            )
        return EvaluationResult(Decision.ALLOW, None, r.reason or f"allowed by {r.name}", matched)
    return EvaluationResult(
        default_decision,
        default_role if default_decision is Decision.REQUIRE_APPROVAL else None,
        "no policy matched; default applied",
        matched,
        defaulted=True,
    )
