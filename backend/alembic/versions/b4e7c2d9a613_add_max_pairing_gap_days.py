"""match_rule_sets.max_pairing_gap_days (the pairing window)

Purely additive, one nullable column: NULL means the MatchRuleSet
dataclass default — None, no pairing window, the historical behaviour.
Hand-written for the reason in e6c12a7b940f (autogenerate proposes
re-creating every cross-schema FK against SQLite).

Revision ID: b4e7c2d9a613
Revises: f3b8d1a6c4e2
Create Date: 2026-09-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4e7c2d9a613'
down_revision: Union[str, None] = 'f3b8d1a6c4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.add_column(sa.Column('max_pairing_gap_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.drop_column('max_pairing_gap_days')
