import itertools

import pytest
from recoup_case.state_machine import (
    TRANSITIONS,
    CaseStatus,
    assert_transition,
    can_transition,
    is_terminal,
)
from recoup_common.errors import InvalidTransitionError


def test_happy_path_dispute_flow() -> None:
    path = [
        CaseStatus.NEW,
        CaseStatus.TRIAGED,
        CaseStatus.INVESTIGATING,
        CaseStatus.PENDING_APPROVAL,
        CaseStatus.ACTION_TAKEN,
        CaseStatus.AWAITING_CUSTOMER,
        CaseStatus.RESOLVED,
    ]
    for a, b in itertools.pairwise(path):
        assert_transition(a, b)


def test_terminal_states_have_no_exits() -> None:
    for s in (CaseStatus.RESOLVED, CaseStatus.WRITTEN_OFF):
        assert is_terminal(s)
        assert TRANSITIONS[s] == frozenset()
        with pytest.raises(InvalidTransitionError):
            assert_transition(s, CaseStatus.NEW)


def test_illegal_transitions_rejected() -> None:
    assert not can_transition(CaseStatus.NEW, CaseStatus.ACTION_TAKEN)
    assert not can_transition(CaseStatus.NEW, CaseStatus.INVESTIGATING)
    assert can_transition(CaseStatus.NEW, CaseStatus.PENDING_APPROVAL)
    with pytest.raises(InvalidTransitionError) as ei:
        assert_transition(CaseStatus.NEW, CaseStatus.ACTION_TAKEN)
    assert ei.value.status_code == 409
    assert "allowed" in ei.value.details


def test_self_transition_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition(CaseStatus.TRIAGED, CaseStatus.TRIAGED)


def test_every_status_reaches_escalated_or_terminal() -> None:
    for s, targets in TRANSITIONS.items():
        if not is_terminal(s):
            assert CaseStatus.ESCALATED in targets or CaseStatus.RESOLVED in targets, s
