"""knowledge initial schema (vector dim from EMBED_DIM, default 768)

Revision ID: 0001_knowledge
Revises:
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_knowledge"
down_revision = None
branch_labels = None
depends_on = None

S = "knowledge"
DIM = int(os.environ.get("EMBED_DIM", "768"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("customer_ref", sa.Text),
        sa.Column("source_ref", sa.Text),
        sa.Column("content_text", sa.Text, nullable=False),
        sa.Column("metadata", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_documents_tenant_kind", "documents", ["tenant_id", "kind"], schema=S)
    op.create_index(
        "ix_documents_tenant_customer", "documents", ["tenant_id", "customer_ref"], schema=S
    )
    op.create_index(
        "uq_documents_source",
        "documents",
        ["tenant_id", "kind", "source_ref"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.execute(f"""
        CREATE TABLE {S}.chunks (
            id bigserial PRIMARY KEY,
            tenant_id uuid NOT NULL,
            document_id uuid NOT NULL REFERENCES {S}.documents(id) ON DELETE CASCADE,
            kind text NOT NULL,
            customer_ref text,
            chunk_no int NOT NULL,
            content text NOT NULL,
            embedding vector({DIM}) NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb
        )
    """)
    op.execute(f"CREATE INDEX ix_chunks_tenant_customer ON {S}.chunks (tenant_id, customer_ref)")
    op.execute(f"CREATE INDEX ix_chunks_document ON {S}.chunks (document_id)")
    op.execute(
        f"CREATE INDEX ix_chunks_embedding ON {S}.chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(f"CREATE INDEX ix_chunks_tsv ON {S}.chunks USING gin (tsv)")
    op.execute(f"""
        CREATE TABLE {S}.customer_memories (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            customer_ref text NOT NULL,
            fact text NOT NULL,
            category text NOT NULL,
            confidence numeric(4,3) NOT NULL DEFAULT 0.7,
            source_case_id uuid,
            source_ref text,
            created_by text NOT NULL,
            embedding vector({DIM}),
            created_at timestamptz NOT NULL DEFAULT now(),
            superseded_by uuid,
            superseded_at timestamptz
        )
    """)
    op.execute(
        f"CREATE INDEX ix_memories_tenant_customer ON {S}.customer_memories (tenant_id, customer_ref) WHERE superseded_by IS NULL"
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
    for t in ("outbox", "customer_memories", "chunks", "documents"):
        op.execute(f"DROP TABLE IF EXISTS {S}.{t} CASCADE")
