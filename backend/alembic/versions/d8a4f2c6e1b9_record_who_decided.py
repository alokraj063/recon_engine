"""record WHO: the user behind audit events and ledger decisions, + notes

Purely additive, all nullable:

* audit_log.actor_user_id — the signed-in user whose request caused the
  event (NULL = system, or an event from before this revision).
* match_ledger.decided_by_user_id / decided_at / decision_note — the
  latest user decision on a match and the optional note left with it
  (match_ledger.note stays the MANUAL match's creation note).
* exception_ledger.resolved_by_user_id — the user behind a USER_* resolution.
* credit_sources.decided_by_user_id / note — who made the IREPS /
  non-IREPS call, and why.

The user columns are soft references to users.id (no FK): a user is
deactivated, never deleted, and an FK would make SQLite batch-recreate
match_ledger / exception_ledger, dropping their cross-schema edges.
Plain ADD COLUMNs on both dialects. Hand-written for the reason in
e6c12a7b940f. No backfill: history before this revision has no user.

Revision ID: d8a4f2c6e1b9
Revises: c5f1a83b7d20
Create Date: 2026-09-23 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8a4f2c6e1b9'
down_revision: Union[str, None] = 'c5f1a83b7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    'audit_log': [sa.Column('actor_user_id', sa.Integer(), nullable=True)],
    'match_ledger': [sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
                     sa.Column('decided_at', sa.DateTime(), nullable=True),
                     sa.Column('decision_note', sa.Text(), nullable=True)],
    'exception_ledger': [sa.Column('resolved_by_user_id', sa.Integer(), nullable=True)],
    'credit_sources': [sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
                       sa.Column('note', sa.Text(), nullable=True)],
}


def upgrade() -> None:
    for table, cols in _COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for col in cols:
                batch_op.add_column(col)


def downgrade() -> None:
    for table, cols in _COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for col in reversed(cols):
                batch_op.drop_column(col.name)
