"""Kafka topic names. One topic per bounded context; the event ``type`` disambiguates."""

from __future__ import annotations

TOPIC_PREFIX = "recoup"

IAM = f"{TOPIC_PREFIX}.iam"
CASE = f"{TOPIC_PREFIX}.case"
ERP = f"{TOPIC_PREFIX}.erp"
AGENT = f"{TOPIC_PREFIX}.agent"
COMM = f"{TOPIC_PREFIX}.comm"
POLICY = f"{TOPIC_PREFIX}.policy"
TOOL = f"{TOPIC_PREFIX}.tool"

ALL_TOPICS = [IAM, CASE, ERP, AGENT, COMM, POLICY, TOOL]


def topic_for(event_type: str) -> str:
    """``case.state_changed`` -> ``recoup.case``."""
    context = event_type.split(".", 1)[0]
    return f"{TOPIC_PREFIX}.{context}"


# Event type constants (keep in sync with docs/architecture.md)
class EventTypes:
    IAM_TENANT_CREATED = "iam.tenant.created"
    IAM_USER_CREATED = "iam.user.created"

    ERP_INVOICE_OVERDUE = "erp.invoice.overdue"

    CASE_CREATED = "case.created"
    CASE_STATE_CHANGED = "case.state_changed"
    CASE_ACTION_PROPOSED = "case.action.proposed"
    CASE_ACTION_APPROVED = "case.action.approved"
    CASE_ACTION_REJECTED = "case.action.rejected"
    CASE_ACTION_EDITED = "case.action.edited"
    CASE_RESOLVED = "case.resolved"
    CASE_TAKEOVER = "case.takeover"
    CASE_RELEASED = "case.released"
    CASE_NOTE_ADDED = "case.note.added"

    COMM_EMAIL_SENT = "comm.email.sent"
    COMM_EMAIL_RECEIVED = "comm.email.received"

    AGENT_RUN_STARTED = "agent.run.started"
    AGENT_RUN_WAITING = "agent.run.waiting"
    AGENT_RUN_COMPLETED = "agent.run.completed"
    AGENT_STEP_STARTED = "agent.step.started"
    AGENT_STEP_COMPLETED = "agent.step.completed"
