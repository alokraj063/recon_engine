"""exception_ledger.gap_type — the bank-side gap code on the ledger

Additive: the frozen gap code the engine assigned to a BANK_ONLY credit
(SIGNAL_BILL_NOT_FOUND | UNRECOGNISED_RECEIPT) so the Command Center /
AR figures can keep unrecognised receipts (no match signal in the
narrative — never matchable) out of the match rate and count them
separately. NULL on rows written before this revision: readers derive
the code from the credit's zone_guess (blank -> unrecognised), the same
rule the default field mapping applies (db/overview.unrecognised_clause).

Written by hand (autogenerate against SQLite also proposes re-creating
every cross-schema FK — see f1c6b2d84a95).

Revision ID: c3f8a1d27e64
Revises: b2d7e9f4c1a8
Create Date: 2026-09-08 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3f8a1d27e64'
down_revision: Union[str, None] = 'b2d7e9f4c1a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('exception_ledger', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gap_type', sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('exception_ledger', schema=None) as batch_op:
        batch_op.drop_column('gap_type')
