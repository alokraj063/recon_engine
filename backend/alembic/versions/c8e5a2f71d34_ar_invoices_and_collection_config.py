"""gold.ar_invoices + match_rule_sets.collection_config + an AR slot per customer

Additive. gold.ar_invoices holds Oracle AR statements (one snapshot per
file) for the Daily Collection export; collection_config (NULL = the
defaults in db/collection.py) holds that export's fiscal calendar,
category master, branch codes and recipients. Every existing customer
gets an `ar_statement` source slot (oracle_ar) so the Ingest page can
take an AR statement without a manual setup step.

Hand-written for the reason in f1c6b2d84a95 (autogenerate proposes
re-creating every cross-schema FK against SQLite).

Revision ID: c8e5a2f71d34
Revises: b4e7c2d9a613
Create Date: 2026-09-30 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c8e5a2f71d34'
down_revision: Union[str, None] = 'b4e7c2d9a613'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'ar_invoices',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('bronze_file_id', sa.Integer(), nullable=False),
        sa.Column('row_seq', sa.Integer(), nullable=False),
        sa.Column('statement_date', sa.Date(), nullable=True),
        sa.Column('invoice_number', sa.String(length=64), nullable=True),
        sa.Column('invoice_date', sa.Date(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('customer_number', sa.String(length=64), nullable=True),
        sa.Column('customer_name', sa.String(length=300), nullable=True),
        sa.Column('operating_unit', sa.String(length=64), nullable=True),
        sa.Column('sales_rep', sa.String(length=200), nullable=True),
        sa.Column('sales_order_type', sa.String(length=200), nullable=True),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('subcategory', sa.String(length=64), nullable=True),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('functional_amount', sa.Float(), nullable=True),
        sa.Column('functional_amount_open', sa.Float(), nullable=True),
        sa.Column('extras', JSON, nullable=True),
        sa.ForeignKeyConstraint(['bronze_file_id'], ['bronze.files.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        schema='gold',
    )
    with op.batch_alter_table('ar_invoices', schema='gold') as batch_op:
        batch_op.create_index(batch_op.f('ix_gold_ar_invoices_customer_id'),
                              ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_gold_ar_invoices_bronze_file_id'),
                              ['bronze_file_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_gold_ar_invoices_statement_date'),
                              ['statement_date'], unique=False)
        batch_op.create_index('uq_ar_invoices_file_seq',
                              ['bronze_file_id', 'row_seq'], unique=True)
        batch_op.create_index('ix_ar_invoices_customer_invoice',
                              ['customer_id', 'invoice_number'], unique=False)

    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.add_column(sa.Column('collection_config', JSON, nullable=True))

    conn = op.get_bind()
    customers = [r[0] for r in conn.execute(sa.text("SELECT id FROM customers"))]
    have = {r[0] for r in conn.execute(sa.text(
        "SELECT customer_id FROM source_configs WHERE source_type = 'ar_statement'"))}
    sc = sa.table('source_configs',
                  sa.column('customer_id', sa.Integer),
                  sa.column('source_type', sa.String),
                  sa.column('role', sa.String),
                  sa.column('adapter_key', sa.String),
                  sa.column('params', sa.JSON),
                  sa.column('is_active', sa.Boolean))
    rows = [{"customer_id": cid, "source_type": "ar_statement",
             "role": "ar_statement", "adapter_key": "oracle_ar",
             "params": {}, "is_active": True}
            for cid in customers if cid not in have]
    if rows:
        op.bulk_insert(sc, rows)


def downgrade() -> None:
    op.execute("DELETE FROM source_configs WHERE source_type = 'ar_statement'")
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.drop_column('collection_config')
    with op.batch_alter_table('ar_invoices', schema='gold') as batch_op:
        batch_op.drop_index('ix_ar_invoices_customer_invoice')
        batch_op.drop_index('uq_ar_invoices_file_seq')
        batch_op.drop_index(batch_op.f('ix_gold_ar_invoices_statement_date'))
        batch_op.drop_index(batch_op.f('ix_gold_ar_invoices_bronze_file_id'))
        batch_op.drop_index(batch_op.f('ix_gold_ar_invoices_customer_id'))
    op.drop_table('ar_invoices', schema='gold')
