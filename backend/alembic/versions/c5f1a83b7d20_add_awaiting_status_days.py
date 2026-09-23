"""match_rule_sets.awaiting_status_days (how long a status lag is excused)

Purely additive, one nullable column: NULL means the MatchRuleSet
dataclass default (7 days), like every other scalar knob on this table.
Hand-written for the reason in e6c12a7b940f (autogenerate proposes
re-creating every cross-schema FK against SQLite).

Revision ID: c5f1a83b7d20
Revises: a7c3e91d5b24
Create Date: 2026-09-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5f1a83b7d20'
down_revision: Union[str, None] = 'a7c3e91d5b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.add_column(sa.Column('awaiting_status_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('match_rule_sets') as batch_op:
        batch_op.drop_column('awaiting_status_days')
