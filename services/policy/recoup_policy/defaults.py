"""Starter policy set installed per tenant. Managers edit these in the console."""

from __future__ import annotations

from typing import Any

DISPUTE_CAUSES = ["DISPUTE_PRICING", "DISPUTE_QUANTITY", "DUPLICATE_INVOICE", "SHORT_PAY"]

DEFAULT_POLICIES: list[dict[str, Any]] = [
    # ---- credit memos ----
    {
        "name": "small_credit_memo_autonomy",
        "description": "Agents may issue small credit memos on confirmed disputes with PO proof.",
        "action_type": "CREATE_CREDIT_MEMO",
        "priority": 10,
        "rule": {
            "all": [
                {"fact": "action.amount", "op": "<=", "value": 1000},
                {"fact": "case.root_cause", "op": "in", "value": DISPUTE_CAUSES},
                {"fact": "case.root_cause_conf", "op": ">=", "value": 0.85},
                {"fact": "evidence.has_po_match", "op": "==", "value": True},
            ]
        },
        "decision": "ALLOW",
    },
    {
        "name": "credit_memo_over_5000_manager",
        "description": "Large credit memos need a manager.",
        "action_type": "CREATE_CREDIT_MEMO",
        "priority": 20,
        "rule": {"fact": "action.amount", "op": ">", "value": 5000},
        "decision": "REQUIRE_APPROVAL",
        "required_role": "manager",
    },
    {
        "name": "credit_memo_default_analyst",
        "description": "Every other credit memo is reviewed by an analyst.",
        "action_type": "CREATE_CREDIT_MEMO",
        "priority": 900,
        "rule": {"fact": "action.amount", "op": ">", "value": 0},
        "decision": "REQUIRE_APPROVAL",
        "required_role": "analyst",
    },
    # ---- payment plans / extensions ----
    {
        "name": "credit_hold_blocks_payment_plan",
        "description": "No payment plans for customers on credit hold.",
        "action_type": "PAYMENT_PLAN",
        "priority": 1,
        "rule": {"fact": "customer.credit_hold", "op": "==", "value": True},
        "decision": "DENY",
        "reason": "customer is on credit hold",
    },
    {
        "name": "payment_plan_within_limits",
        "description": "Up to 2% discount, 30-day extension, 3 installments is autonomous.",
        "action_type": "PAYMENT_PLAN",
        "priority": 10,
        "rule": {
            "all": [
                {"fact": "action.discount_pct", "op": "<=", "value": 2},
                {"fact": "action.extension_days", "op": "<=", "value": 30},
                {"fact": "action.installments", "op": "<=", "value": 3},
            ]
        },
        "decision": "ALLOW",
    },
    {
        "name": "payment_plan_default_manager",
        "description": "Anything more generous needs a manager.",
        "action_type": "PAYMENT_PLAN",
        "priority": 900,
        "rule": {"fact": "action.installments", "op": "exists"},
        "decision": "REQUIRE_APPROVAL",
        "required_role": "manager",
    },
    # ---- email ----
    {
        "name": "email_low_tone_deny",
        "description": "Never send an email that fails the tone check.",
        "action_type": "SEND_EMAIL",
        "priority": 1,
        "rule": {"fact": "email.tone_score", "op": "<", "value": 0.7},
        "decision": "DENY",
        "reason": "tone score below 0.7",
    },
    {
        "name": "email_routine_templates_allow",
        "description": "Routine templated outreach to a known active contact is autonomous.",
        "action_type": "SEND_EMAIL",
        "priority": 10,
        "rule": {
            "all": [
                {
                    "fact": "email.template",
                    "op": "in",
                    "value": ["po_request", "resend_invoice", "payment_reminder"],
                },
                {"fact": "email.tone_score", "op": ">=", "value": 0.7},
                {"fact": "email.recipient_known", "op": "==", "value": True},
            ]
        },
        "decision": "ALLOW",
    },
    {
        "name": "email_default_analyst",
        "description": "Free-form emails are reviewed by an analyst before sending.",
        "action_type": "SEND_EMAIL",
        "priority": 900,
        "rule": {"fact": "email.to", "op": "exists"},
        "decision": "REQUIRE_APPROVAL",
        "required_role": "analyst",
    },
    # ---- rebill / escalate ----
    {
        "name": "rebill_manager",
        "description": "Re-bills are always manager approved.",
        "action_type": "REBILL",
        "priority": 10,
        "rule": {"fact": "action.invoice_ref", "op": "exists"},
        "decision": "REQUIRE_APPROVAL",
        "required_role": "manager",
    },
    {
        "name": "escalate_allow",
        "description": "Agents may always hand a case to a human.",
        "action_type": "ESCALATE",
        "priority": 10,
        "rule": {"fact": "action.reason", "op": "exists"},
        "decision": "ALLOW",
    },
]
