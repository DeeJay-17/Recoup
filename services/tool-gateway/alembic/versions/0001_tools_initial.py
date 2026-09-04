"""tools initial schema

Revision ID: 0001_tools
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_tools"
down_revision = None
branch_labels = None
depends_on = None

S = "tools"


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("args", pg.JSONB, nullable=False),
        sa.Column("result", pg.JSONB),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("policy_decision", sa.Text),
        sa.Column("policy_evaluation_id", sa.BigInteger),
        sa.Column("approval_ref", sa.Text),
        sa.Column("idempotency_key", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("trace_id", sa.Text),
        sa.Column(
            "invoked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index(
        "ix_tool_invocations_tenant_case",
        "tool_invocations",
        ["tenant_id", "case_id", "invoked_at"],
        schema=S,
    )
    op.create_index(
        "uq_tool_invocations_idem",
        "tool_invocations",
        ["tenant_id", "tool", "idempotency_key"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND status = 'SUCCESS'"),
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


def downgrade() -> None:
    for t in ("outbox", "tool_invocations"):
        op.drop_table(t, schema=S)
