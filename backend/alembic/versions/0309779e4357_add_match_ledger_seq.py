"""add match_ledger seq — durable per-customer match number (UI "M-{seq}")

The engine's match_id (m0, m1, …) restarts every run, so it repeats in
the Analyst queue; seq is assigned once per ledger row and never reused.
Backfills existing rows per customer in created_at order.

(Autogenerate also proposed the usual spurious cross-schema FK adds —
SQLite/ATTACH noise per CLAUDE.md — stripped by hand.)

Revision ID: 0309779e4357
Revises: c7d1e4a90b23
Create Date: 2026-08-26 14:39:18.557349

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0309779e4357'
down_revision: Union[str, None] = 'c7d1e4a90b23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('match_ledger', schema=None) as batch_op:
        batch_op.add_column(sa.Column('seq', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_match_ledger_seq'), ['seq'],
                              unique=False)

    # backfill: number existing matches per customer, oldest first
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, customer_id FROM match_ledger "
        "ORDER BY customer_id, created_at, id")).fetchall()
    counters: dict = {}
    for row_id, customer_id in rows:
        counters[customer_id] = counters.get(customer_id, 0) + 1
        conn.execute(sa.text(
            "UPDATE match_ledger SET seq = :seq WHERE id = :id"),
            {"seq": counters[customer_id], "id": row_id})


def downgrade() -> None:
    with op.batch_alter_table('match_ledger', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_ledger_seq'))
        batch_op.drop_column('seq')
