"""evals initial schema

Revision ID: 0001_evals
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_evals"
down_revision = None
branch_labels = None
depends_on = None

S = "evals"


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("case_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("spec", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_eval_datasets_tenant_name"),
        schema=S,
    )
    op.create_table(
        "eval_cases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dataset_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.eval_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", pg.UUID(as_uuid=True)),
        sa.Column("invoice_ref", sa.Text, nullable=False),
        sa.Column("customer_ref", sa.Text, nullable=False),
        sa.Column("scenario", sa.Text, nullable=False),
        sa.Column("expected_root_cause", sa.Text, nullable=False),
        sa.Column("expected_credit_memo", sa.Numeric(14, 2)),
        sa.Column("expected_final_state", sa.Text),
        sa.Column("ground_truth", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("probe", pg.JSONB),
        sa.UniqueConstraint("dataset_id", "invoice_ref", name="uq_eval_cases_dataset_invoice"),
        schema=S,
    )
    op.create_index("ix_eval_cases_dataset", "eval_cases", ["dataset_id"], schema=S)
    op.create_table(
        "eval_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "dataset_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.eval_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("judge", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("concurrency", sa.Integer, nullable=False, server_default="4"),
        sa.Column("prompt_bundle", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("models", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("cases_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cases_done", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_index("ix_eval_runs_tenant", "eval_runs", ["tenant_id", "started_at"], schema=S)
    op.create_table(
        "eval_results",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("eval_case_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("expected_root_cause", sa.Text),
        sa.Column("triage_root_cause", sa.Text),
        sa.Column("predicted_root_cause", sa.Text),
        sa.Column("expected_credit_memo", sa.Numeric(14, 2)),
        sa.Column("predicted_credit_memo", sa.Numeric(14, 2)),
        sa.Column("credit_delta", sa.Numeric(14, 2)),
        sa.Column("terminal_status", sa.Text),
        sa.Column("steps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("policy_denials", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approval_gates", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unauthorized_mutations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("scores", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("failures", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("detail", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_index("ix_eval_results_run", "eval_results", ["run_id"], schema=S)


def downgrade() -> None:
    for t in ("eval_results", "eval_runs", "eval_cases", "eval_datasets"):
        op.drop_table(t, schema=S)
