"""add credit_sources (analyst IREPS / non-IREPS decision per credit)

Purely additive: one new app-schema table. Written by hand for the same
reason as e6c12a7b940f — autogenerate against SQLite also proposes
re-creating every cross-schema FK. No backfill: an absent row means "no
decision", and the engine's own reading applies.

Revision ID: f4b8d2a6c913
Revises: e6c12a7b940f
Create Date: 2026-09-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4b8d2a6c913'
down_revision: Union[str, None] = 'e6c12a7b940f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'credit_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('gold_bank_txn_id', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['gold_bank_txn_id'], ['gold.bank_txns.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('customer_id', 'gold_bank_txn_id'),
    )
    with op.batch_alter_table('credit_sources') as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_sources_customer_id'),
                              ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_sources_gold_bank_txn_id'),
                              ['gold_bank_txn_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('credit_sources') as batch_op:
        batch_op.drop_index(batch_op.f('ix_credit_sources_gold_bank_txn_id'))
        batch_op.drop_index(batch_op.f('ix_credit_sources_customer_id'))
    op.drop_table('credit_sources')
