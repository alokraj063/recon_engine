"""rename IREPS-flavoured exception codes to source-neutral ones

The three IREPS-ish machine codes were renamed once, deliberately:

    ZONE_BILL_NOT_FOUND       -> SIGNAL_BILL_NOT_FOUND
    NON_IREPS_OR_UNRECOGNISED -> UNRECOGNISED_RECEIPT
    CO7_ISSUED_NO_ADVICE      -> PAYMENT_ORDER_NO_ADVICE

This migration rewrites the KEYS of every stored
match_rule_sets.copy_overrides blob (gap_type / expected_basis / labels
sections) so per-customer text — notably the default customer's railway
wording — keeps applying under the new codes. Old PERSISTED RUN payloads
are deliberately untouched (immutable run artifacts); the frontend's
normalizeLegacy translates their gap_type/ExpectedBasis values at render
time. Downgrade reverses the key map.

Revision ID: d7e3f5a18c42
Revises: c4d8e2f96b31
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e3f5a18c42'
down_revision: Union[str, None] = 'c4d8e2f96b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODE_RENAMES = {
    "ZONE_BILL_NOT_FOUND": "SIGNAL_BILL_NOT_FOUND",
    "NON_IREPS_OR_UNRECOGNISED": "UNRECOGNISED_RECEIPT",
    "CO7_ISSUED_NO_ADVICE": "PAYMENT_ORDER_NO_ADVICE",
}


def _rewrite_copy_keys(code_map):
    conn = op.get_bind()
    t = sa.table("match_rule_sets", sa.column("id", sa.Integer()),
                 sa.column("copy_overrides", sa.JSON()))
    rows = conn.execute(
        sa.select(t.c.id, t.c.copy_overrides)
        .where(t.c.copy_overrides.is_not(None))).all()
    for row_id, copy in rows:
        if not isinstance(copy, dict):
            continue
        new = {section: ({code_map.get(k, k): v for k, v in entries.items()}
                         if isinstance(entries, dict) else entries)
               for section, entries in copy.items()}
        if new != copy:
            conn.execute(t.update().where(t.c.id == row_id)
                         .values(copy_overrides=new))


def upgrade() -> None:
    _rewrite_copy_keys(CODE_RENAMES)


def downgrade() -> None:
    _rewrite_copy_keys({v: k for k, v in CODE_RENAMES.items()})
