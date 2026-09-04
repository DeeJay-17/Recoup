"""policy initial schema

Revision ID: 0001_policy
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_policy"
down_revision = None
branch_labels = None
depends_on = None

S = "policy"


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_policies_tenant_name"),
        schema=S,
    )
    op.create_index(
        "ix_policies_tenant_action", "policies", ["tenant_id", "action_type", "enabled"], schema=S
    )
    op.create_table(
        "policy_versions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("rule", pg.JSONB, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("required_role", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_versions_policy_version"),
        schema=S,
    )
    op.create_table(
        "policy_evaluations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("context", pg.JSONB, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("required_role", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("matched", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index(
        "ix_policy_evaluations_tenant_case",
        "policy_evaluations",
        ["tenant_id", "case_id"],
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
    for t in ("outbox", "policy_evaluations", "policy_versions", "policies"):
        op.drop_table(t, schema=S)
