"""analytics initial schema

Revision ID: 0001_analytics
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_analytics"
down_revision = None
branch_labels = None
depends_on = None

S = "analytics"
NUM = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "fact_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index(
        "ix_fact_events_tenant_type_time",
        "fact_events",
        ["tenant_id", "type", "occurred_at"],
        schema=S,
    )
    op.create_table(
        "dim_case",
        sa.Column("case_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_ref", sa.Text),
        sa.Column("invoice_refs", pg.ARRAY(sa.Text)),
        sa.Column("currency", sa.String(3)),
        sa.Column("amount_open", NUM),
        sa.Column("days_overdue", sa.Integer),
        sa.Column("priority", sa.SmallInteger),
        sa.Column("status", sa.Text),
        sa.Column("root_cause", sa.Text),
        sa.Column("agent_mode", sa.Text),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("first_agent_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.Text),
        sa.Column("escalated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("human_touched", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("emails_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_dim_case_tenant_status", "dim_case", ["tenant_id", "status"], schema=S)
    op.create_index("ix_dim_case_tenant_closed", "dim_case", ["tenant_id", "closed_at"], schema=S)
    op.create_table(
        "fact_agent_run",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("mode", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("steps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("root_cause", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index(
        "ix_fact_run_tenant_time", "fact_agent_run", ["tenant_id", "started_at"], schema=S
    )
    op.create_table(
        "fact_agent_step",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("agent", sa.Text, nullable=False),
        sa.Column("kind", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index(
        "ix_fact_step_tenant_agent",
        "fact_agent_step",
        ["tenant_id", "agent", "occurred_at"],
        schema=S,
    )
    op.create_table(
        "fact_action",
        sa.Column("action_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("policy_decision", sa.Text),
        sa.Column("required_role", sa.Text),
        sa.Column("outcome", sa.Text),
        sa.Column("proposed_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index(
        "ix_fact_action_tenant_type", "fact_action", ["tenant_id", "action_type"], schema=S
    )
    op.create_table(
        "fact_tool_call",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("actor", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("policy_decision", sa.Text),
        sa.Column("side_effect", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index(
        "ix_fact_tool_tenant_time", "fact_tool_call", ["tenant_id", "occurred_at"], schema=S
    )
    op.create_table(
        "fact_email",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("template", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema=S,
    )
    op.create_index(
        "ix_fact_email_tenant_time", "fact_email", ["tenant_id", "occurred_at"], schema=S
    )


def downgrade() -> None:
    for t in (
        "fact_email",
        "fact_tool_call",
        "fact_action",
        "fact_agent_step",
        "fact_agent_run",
        "dim_case",
        "fact_events",
    ):
        op.drop_table(t, schema=S)
