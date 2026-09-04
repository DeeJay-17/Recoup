"""mockerp initial schema

Revision ID: 0001_mockerp
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_mockerp"
down_revision = None
branch_labels = None
depends_on = None

S = "mockerp"
NUM = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("customer_ref", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("payment_terms_days", sa.Integer, nullable=False),
        sa.Column("requires_po", sa.Boolean, nullable=False),
        sa.Column("credit_hold", sa.Boolean, nullable=False),
        sa.Column("credit_risk_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("contacts", pg.JSONB, nullable=False),
        sa.Column("billing_address", sa.Text),
        schema=S,
    )
    op.create_table(
        "contracts",
        sa.Column(
            "customer_ref", sa.Text, sa.ForeignKey(f"{S}.customers.customer_ref"), primary_key=True
        ),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("payment_terms_days", sa.Integer, nullable=False),
        sa.Column("price_list", pg.JSONB, nullable=False),
        sa.Column("freight_billable", sa.Boolean, nullable=False),
        sa.Column("early_pay_discount_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("max_discount_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("dispute_window_days", sa.Integer, nullable=False),
        schema=S,
    )
    op.create_table(
        "purchase_orders",
        sa.Column("po_number", sa.Text, primary_key=True),
        sa.Column(
            "customer_ref", sa.Text, sa.ForeignKey(f"{S}.customers.customer_ref"), nullable=False
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("issued_at", sa.Date, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("lines", pg.JSONB, nullable=False),
        sa.Column("total", NUM, nullable=False),
        schema=S,
    )
    op.create_table(
        "invoices",
        sa.Column("invoice_ref", sa.Text, primary_key=True),
        sa.Column(
            "customer_ref", sa.Text, sa.ForeignKey(f"{S}.customers.customer_ref"), nullable=False
        ),
        sa.Column("po_number", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", NUM, nullable=False),
        sa.Column("freight", NUM, nullable=False),
        sa.Column("tax", NUM, nullable=False),
        sa.Column("total", NUM, nullable=False),
        sa.Column("amount_paid", NUM, nullable=False),
        sa.Column("amount_open", NUM, nullable=False),
        sa.Column("billed_to_email", sa.Text),
        sa.Column("lines", pg.JSONB, nullable=False),
        sa.Column("scenario", sa.Text, nullable=False),
        sa.Column("ground_truth", pg.JSONB, nullable=False),
        schema=S,
    )
    op.create_index("ix_invoices_status_due", "invoices", ["status", "due_date"], schema=S)
    op.create_index("ix_invoices_customer", "invoices", ["customer_ref"], schema=S)
    op.create_table(
        "deliveries",
        sa.Column(
            "invoice_ref", sa.Text, sa.ForeignKey(f"{S}.invoices.invoice_ref"), primary_key=True
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("signed_by", sa.Text),
        sa.Column("carrier", sa.Text),
        sa.Column("tracking_number", sa.Text),
        sa.Column("lines", pg.JSONB, nullable=False),
        schema=S,
    )
    op.create_table(
        "remittances",
        sa.Column("remittance_id", sa.Text, primary_key=True),
        sa.Column(
            "customer_ref", sa.Text, sa.ForeignKey(f"{S}.customers.customer_ref"), nullable=False
        ),
        sa.Column("invoice_ref", sa.Text),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("memo", sa.Text),
        schema=S,
    )
    op.create_index("ix_remittances_customer", "remittances", ["customer_ref"], schema=S)
    op.create_table(
        "credit_memos",
        sa.Column("credit_memo_ref", sa.Text, primary_key=True),
        sa.Column(
            "invoice_ref", sa.Text, sa.ForeignKey(f"{S}.invoices.invoice_ref"), nullable=False
        ),
        sa.Column("customer_ref", sa.Text, nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("memo", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_table(
        "payment_plans",
        sa.Column("plan_ref", sa.Text, primary_key=True),
        sa.Column("customer_ref", sa.Text, nullable=False),
        sa.Column("invoice_refs", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("installments", pg.JSONB, nullable=False),
        sa.Column("total", NUM, nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )
    op.create_table(
        "seed_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("seed", sa.Integer, nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("customers", sa.Integer, nullable=False),
        sa.Column("invoices", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=S,
    )


def downgrade() -> None:
    for t in (
        "seed_runs",
        "payment_plans",
        "credit_memos",
        "remittances",
        "deliveries",
        "invoices",
        "purchase_orders",
        "contracts",
        "customers",
    ):
        op.drop_table(t, schema=S)
