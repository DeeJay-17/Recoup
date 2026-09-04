"""comm initial schema

Revision ID: 0001_comm
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_comm"
down_revision = None
branch_labels = None
depends_on = None

S = "comm"


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("customer_ref", sa.Text),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("invoice_refs", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_threads_tenant_case", "threads", ["tenant_id", "case_id"], schema=S)
    op.create_table(
        "messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("message_id", sa.Text, nullable=False, unique=True),
        sa.Column("in_reply_to", sa.Text),
        sa.Column("external_id", sa.Text, unique=True),
        sa.Column("from_addr", sa.Text, nullable=False),
        sa.Column("to_addrs", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("cc_addrs", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("body_html", sa.Text),
        sa.Column("template", sa.Text),
        sa.Column("attachments", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("invoice_refs", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("link_method", sa.Text),
        sa.Column("idempotency_key", sa.Text, unique=True),
        sa.Column("approval_ref", sa.Text),
        sa.Column("sent_by", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_messages_thread", "messages", ["thread_id", "created_at"], schema=S)
    op.create_index("ix_messages_tenant_case", "messages", ["tenant_id", "case_id"], schema=S)
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
    for t in ("outbox", "messages", "threads"):
        op.drop_table(t, schema=S)
