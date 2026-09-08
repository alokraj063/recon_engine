"""ledger decisions: how an exception was resolved, manual matches

Two additive changes on the app-layer ledger tables:

* exception_ledger.resolved_by (RUN | USER_ACCEPT | USER_MANUAL |
  USER_REOPEN) + resolved_by_match_id -> match_ledger.id. Until now an
  exception could only be resolved by a later run; an analyst accepting
  or hand-pairing a match now resolves the linked rows too, and the
  ledger says which. Existing RESOLVED rows were all run-resolved and
  are backfilled to RUN.
* match_ledger.run_id becomes nullable and match_ledger.note is added:
  a MANUAL match is created by a user, not a run. For the same reason
  exception_ledger.first_seen_run_id becomes nullable: rejecting a manual
  match re-opens its credit and bills as exceptions with no run behind
  them (an earlier row's run is inherited when one exists).

Written by hand (see f1c6b2d84a95 for why autogenerate is not trusted
here: against SQLite it also proposes re-creating every cross-schema FK).
Batch mode on SQLite recreates match_ledger; the reflected copy drops the
unenforceable gold.bank_txns FK clause, which changes nothing SQLite
ever enforced. On Postgres these are plain ALTERs.

Revision ID: b2d7e9f4c1a8
Revises: a9e4d7c2b301
Create Date: 2026-09-07 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2d7e9f4c1a8'
down_revision: Union[str, None] = 'a9e4d7c2b301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('exception_ledger', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resolved_by', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('resolved_by_match_id', sa.String(length=32), nullable=True))
        batch_op.create_foreign_key('fk_exception_ledger_resolved_by_match',
                                    'match_ledger', ['resolved_by_match_id'], ['id'])
        batch_op.alter_column('first_seen_run_id', existing_type=sa.String(length=32),
                              nullable=True)
    op.execute("UPDATE exception_ledger SET resolved_by = 'RUN' "
               "WHERE status = 'RESOLVED' AND resolved_by IS NULL")

    with op.batch_alter_table('match_ledger', schema=None) as batch_op:
        batch_op.alter_column('run_id', existing_type=sa.String(length=32),
                              nullable=True)
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM match_ledger_bills WHERE match_ledger_id IN "
               "(SELECT id FROM match_ledger WHERE run_id IS NULL)")
    op.execute("DELETE FROM match_ledger WHERE run_id IS NULL")
    with op.batch_alter_table('match_ledger', schema=None) as batch_op:
        batch_op.drop_column('note')
        batch_op.alter_column('run_id', existing_type=sa.String(length=32),
                              nullable=False)
    op.execute("DELETE FROM exception_ledger WHERE first_seen_run_id IS NULL")
    with op.batch_alter_table('exception_ledger', schema=None) as batch_op:
        batch_op.alter_column('first_seen_run_id', existing_type=sa.String(length=32),
                              nullable=False)
        batch_op.drop_constraint('fk_exception_ledger_resolved_by_match', type_='foreignkey')
        batch_op.drop_column('resolved_by_match_id')
        batch_op.drop_column('resolved_by')
