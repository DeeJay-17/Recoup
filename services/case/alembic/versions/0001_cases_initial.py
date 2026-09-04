"""cases initial schema

Revision ID: 0001_cases
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_cases"
down_revision = None
branch_labels = None
depends_on = None

S = "cases"


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_ref", sa.Text, nullable=False),
        sa.Column("customer_name", sa.Text),
        sa.Column("invoice_refs", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("root_cause", sa.Text),
        sa.Column("root_cause_conf", sa.Numeric(4, 3)),
        sa.Column("priority", sa.SmallInteger, nullable=False, server_default="3"),
        sa.Column("amount_open", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("days_overdue", sa.Integer, nullable=False, server_default="0"),
        sa.Column("assignee_id", pg.UUID(as_uuid=True)),
        sa.Column("agent_mode", sa.Text, nullable=False, server_default="AUTONOMOUS"),
        sa.Column("workflow_id", sa.Text),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", pg.JSONB),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        schema=S,
    )
    op.create_index(
        "ix_cases_tenant_status_priority", "cases", ["tenant_id", "status", "priority"], schema=S
    )
    op.create_index("ix_cases_tenant_customer", "cases", ["tenant_id", "customer_ref"], schema=S)
    op.create_index("ix_cases_tenant_assignee", "cases", ["tenant_id", "assignee_id"], schema=S)

    op.create_table(
        "case_invoices",
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_ref", sa.Text, nullable=False),
        sa.Column(
            "case_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "invoice_ref", name="pk_case_invoices"),
        schema=S,
    )
    op.create_index("ix_case_invoices_case", "case_invoices", ["case_id"], schema=S)

    op.create_table(
        "timeline_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("actor_type", sa.Text, nullable=False),
        sa.Column("actor_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.Text),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index(
        "ix_timeline_case_time", "timeline_events", ["case_id", "occurred_at"], schema=S
    )

    op.create_table(
        "proposed_actions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("evidence_refs", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("policy_decision", sa.Text, nullable=False),
        sa.Column("policy_rule", sa.Text),
        sa.Column("required_role", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("proposed_by", sa.Text, nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("human_final", pg.JSONB),
        sa.Column("decided_by", pg.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("feedback_code", sa.Text),
        sa.Column("feedback_note", sa.Text),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("execution_result", pg.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_actions_case", "proposed_actions", ["case_id", "created_at"], schema=S)
    op.create_index(
        "ix_actions_tenant_status", "proposed_actions", ["tenant_id", "status"], schema=S
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("aggregate_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("traceparent", sa.Text),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        schema=S,
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["id"],
        schema=S,
        postgresql_where=sa.text("published_at IS NULL"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        schema=S,
    )


def downgrade() -> None:
    for t in (
        "ingestion_runs",
        "outbox",
        "proposed_actions",
        "timeline_events",
        "case_invoices",
        "cases",
    ):
        op.drop_table(t, schema=S)
