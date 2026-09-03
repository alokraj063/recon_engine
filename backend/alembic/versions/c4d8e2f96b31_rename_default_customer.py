"""rename default customer's display name to Wabtec

One-time data fix: the seeded `default` customer's display name becomes
"Wabtec" (the key stays `default` — it is load-bearing for the
repo-sample fallback and tests). Guarded on the old seeded name so a
user-customised name is never clobbered.

Revision ID: c4d8e2f96b31
Revises: a91b7c3e5d20
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4d8e2f96b31'
down_revision: Union[str, None] = 'a91b7c3e5d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_NAME = "Default (HSBC / IREPS)"
NEW_NAME = "Wabtec"


def _customers():
    return sa.table("customers", sa.column("key", sa.String(64)),
                    sa.column("name", sa.String(200)))


def upgrade() -> None:
    t = _customers()
    op.get_bind().execute(
        t.update().where(t.c.key == "default", t.c.name == OLD_NAME)
        .values(name=NEW_NAME))


def downgrade() -> None:
    t = _customers()
    op.get_bind().execute(
        t.update().where(t.c.key == "default", t.c.name == NEW_NAME)
        .values(name=OLD_NAME))
