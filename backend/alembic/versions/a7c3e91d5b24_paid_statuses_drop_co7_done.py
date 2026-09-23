"""paid statuses: drop CO7 DONE (a payment order does not guarantee payment)

Data-only. Every match_rule_sets row was seeded with paid_statuses
["PAYMENT MADE", "CO7 DONE"]; the rule changed, so CO7 DONE is removed
from every row that carries it (other statuses a customer added are kept).
A NULL row already means the dataclass default, which no longer has it.
Downgrade puts it back on rows that were left with PAYMENT MADE.

Revision ID: a7c3e91d5b24
Revises: f4b8d2a6c913
Create Date: 2026-09-22 18:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c3e91d5b24'
down_revision: Union[str, None] = 'f4b8d2a6c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CO7 = "CO7 DONE"


def _rewrite(fn) -> None:
    conn = op.get_bind()
    # JSONB on Postgres (models.JSONVariant) needs an explicit cast from the
    # bound text; SQLite stores JSON as text as-is
    value = "CAST(:v AS JSONB)" if conn.dialect.name == "postgresql" else ":v"
    rows = conn.execute(sa.text(
        "SELECT id, paid_statuses FROM match_rule_sets "
        "WHERE paid_statuses IS NOT NULL")).fetchall()
    for rid, raw in rows:
        vals = json.loads(raw) if isinstance(raw, str) else raw
        new = fn(list(vals or []))
        if new is not None and new != vals:
            conn.execute(sa.text(
                f"UPDATE match_rule_sets SET paid_statuses = {value} WHERE id = :i"),
                {"v": json.dumps(new), "i": rid})


def upgrade() -> None:
    _rewrite(lambda v: [s for s in v if s != CO7] if CO7 in v else None)


def downgrade() -> None:
    _rewrite(lambda v: v + [CO7] if "PAYMENT MADE" in v and CO7 not in v else None)
