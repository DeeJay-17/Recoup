"""Case lifecycle. Transitions are validated server-side; illegal ones raise."""

from __future__ import annotations

from enum import StrEnum

from recoup_common.errors import InvalidTransitionError


class CaseStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    NEGOTIATING = "NEGOTIATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTION_TAKEN = "ACTION_TAKEN"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    WRITTEN_OFF = "WRITTEN_OFF"


TERMINAL: frozenset[CaseStatus] = frozenset({CaseStatus.RESOLVED, CaseStatus.WRITTEN_OFF})

_ACTIVE_TARGETS = {
    CaseStatus.INVESTIGATING,
    CaseStatus.AWAITING_CUSTOMER,
    CaseStatus.NEGOTIATING,
    CaseStatus.PENDING_APPROVAL,
    CaseStatus.ESCALATED,
    CaseStatus.RESOLVED,
}

TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    # A proposal can arrive on a brand-new case (e.g. a human drafts one), hence PENDING_APPROVAL.
    CaseStatus.NEW: frozenset(
        {
            CaseStatus.TRIAGED,
            CaseStatus.PENDING_APPROVAL,
            CaseStatus.ESCALATED,
            CaseStatus.RESOLVED,
            CaseStatus.WRITTEN_OFF,
        }
    ),
    CaseStatus.TRIAGED: frozenset(_ACTIVE_TARGETS | {CaseStatus.WRITTEN_OFF}),
    CaseStatus.INVESTIGATING: frozenset(
        (_ACTIVE_TARGETS - {CaseStatus.INVESTIGATING})
        | {CaseStatus.TRIAGED, CaseStatus.WRITTEN_OFF}
    ),
    CaseStatus.AWAITING_CUSTOMER: frozenset(
        (_ACTIVE_TARGETS - {CaseStatus.AWAITING_CUSTOMER}) | {CaseStatus.WRITTEN_OFF}
    ),
    CaseStatus.NEGOTIATING: frozenset(
        (_ACTIVE_TARGETS - {CaseStatus.NEGOTIATING}) | {CaseStatus.WRITTEN_OFF}
    ),
    CaseStatus.PENDING_APPROVAL: frozenset(
        {
            CaseStatus.ACTION_TAKEN,
            CaseStatus.INVESTIGATING,
            CaseStatus.NEGOTIATING,
            CaseStatus.ESCALATED,
            CaseStatus.RESOLVED,
            CaseStatus.WRITTEN_OFF,
        }
    ),
    CaseStatus.ACTION_TAKEN: frozenset(
        (_ACTIVE_TARGETS - {CaseStatus.ESCALATED}) | {CaseStatus.ESCALATED, CaseStatus.WRITTEN_OFF}
    ),
    CaseStatus.ESCALATED: frozenset(
        {
            CaseStatus.INVESTIGATING,
            CaseStatus.NEGOTIATING,
            CaseStatus.PENDING_APPROVAL,
            CaseStatus.AWAITING_CUSTOMER,
            CaseStatus.RESOLVED,
            CaseStatus.WRITTEN_OFF,
        }
    ),
    CaseStatus.RESOLVED: frozenset(),
    CaseStatus.WRITTEN_OFF: frozenset(),
}


def can_transition(src: CaseStatus, dst: CaseStatus) -> bool:
    return dst in TRANSITIONS[src]


def assert_transition(src: CaseStatus, dst: CaseStatus) -> None:
    if src == dst:
        raise InvalidTransitionError(f"case is already {src}")
    if not can_transition(src, dst):
        raise InvalidTransitionError(
            f"illegal transition {src} -> {dst}",
            details={"from": src, "to": dst, "allowed": sorted(TRANSITIONS[src])},
        )


def is_terminal(status: CaseStatus) -> bool:
    return status in TERMINAL


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    AUTO_EXECUTED = "AUTO_EXECUTED"
    DENIED = "DENIED"


class ActionType(StrEnum):
    SEND_EMAIL = "SEND_EMAIL"
    CREATE_CREDIT_MEMO = "CREATE_CREDIT_MEMO"
    PAYMENT_PLAN = "PAYMENT_PLAN"
    REBILL = "REBILL"
    ESCALATE = "ESCALATE"
    CLOSE = "CLOSE"


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class AgentMode(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    HUMAN_CONTROL = "HUMAN_CONTROL"
