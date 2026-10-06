"""users: role + okta_sub, password optional

Okta sign-in is an ADD-ON to the password login, so a user may now have no
password (an Okta-only account) and carries the app-side role that decides
what they may do. `okta_sub` is the stable Okta subject, bound on a user's
first Okta sign-in (matched by email that once) and matched on thereafter,
so a later email change in Okta cannot hand the row to someone else.

Written by hand, like e6c12a7b940f: autogenerate against SQLite also
proposes spurious cross-schema FK diffs.

Backfill: every EXISTING user becomes `admin`, so no current login loses
access when role checks start applying. New users get no role until an
admin assigns one (role NULL = "contact your administrator").

Revision ID: d2f7a9c1be53
Revises: c8e5a2f71d34
Create Date: 2026-10-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd2f7a9c1be53'
down_revision: Union[str, None] = 'c8e5a2f71d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('password_hash',
                              existing_type=sa.String(length=255),
                              nullable=True)
        batch_op.add_column(sa.Column('role', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('okta_sub', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_okta_sub'), ['okta_sub'],
                              unique=True)
    op.execute(sa.text("UPDATE users SET role = 'admin' WHERE role IS NULL"))


def downgrade() -> None:
    # an Okta-only user has no password; the old column was NOT NULL
    op.execute(sa.text("DELETE FROM users WHERE password_hash IS NULL"))
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_okta_sub'))
        batch_op.drop_column('okta_sub')
        batch_op.drop_column('role')
        batch_op.alter_column('password_hash',
                              existing_type=sa.String(length=255),
                              nullable=False)
