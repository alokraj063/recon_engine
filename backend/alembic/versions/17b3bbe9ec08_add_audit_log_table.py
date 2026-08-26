"""add audit_log table

Purely additive: one new table, default/app schema, no changes to any
existing model. Autogenerate also proposed re-creating every cross-schema
foreign key already declared in db/models.py (exception_ledger, bills,
bank_txns, etc.) — that's expected SQLite-only noise (SQLite can't
enforce FKs across ATTACHed files, so a fresh SQLite reflection never
"sees" those constraints and autogenerate wants to "add" them every time,
even though the Python models and the Postgres DDL are already correct).
Hand-stripped: those ops would only trigger SQLite's batch-mode table
recreate-and-copy for tables that are already correct on both dialects.

Revision ID: 17b3bbe9ec08
Revises: bfc9078a3cc6
Create Date: 2026-08-24 12:41:07.491119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '17b3bbe9ec08'
down_revision: Union[str, None] = 'bfc9078a3cc6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.String(length=32), nullable=True),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_audit_log_customer_created', ['customer_id', 'created_at'], unique=False)
        batch_op.create_index('ix_audit_log_event_created', ['event_type', 'created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_log_event_created')
        batch_op.drop_index('ix_audit_log_customer_created')
        batch_op.drop_index(batch_op.f('ix_audit_log_created_at'))

    op.drop_table('audit_log')
