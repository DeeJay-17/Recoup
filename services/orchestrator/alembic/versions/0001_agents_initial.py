"""agents initial schema

Revision ID: 0001_agents
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_agents"
down_revision = None
branch_labels = None
depends_on = None

S = "agents"


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("phase", sa.Text),
        sa.Column("prompt_bundle", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("model_config", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("steps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("outcome", pg.JSONB),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index(
        "ix_agent_runs_tenant_case", "agent_runs", ["tenant_id", "case_id", "started_at"], schema=S
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("input_summary", pg.JSONB),
        sa.Column("output", pg.JSONB),
        sa.Column("tool_calls", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("messages", pg.JSONB),
        sa.Column("provider", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("trace_id", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index("ix_agent_steps_run", "agent_steps", ["run_id", "step_no"], schema=S)
    op.create_table(
        "prompt_versions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("name", "version", name="uq_prompt_versions_name_version"),
        schema=S,
    )
    op.create_table(
        "model_configs",
        sa.Column("tenant_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("overrides", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.Text),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
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
    for t in ("outbox", "model_configs", "prompt_versions", "agent_steps", "agent_runs"):
        op.drop_table(t, schema=S)
