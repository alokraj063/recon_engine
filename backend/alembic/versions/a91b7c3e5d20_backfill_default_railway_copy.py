"""backfill default customer's railway advisory copy

The engine's DEFAULT_COPY became SOURCE-NEUTRAL (generic wording); the
historical IREPS/railway sentences move to the seeded `default`
customer's copy_overrides so its analysts keep seeing exactly what they
always saw. One-time backfill: only where copy_overrides IS NULL (a
later deliberate clear sticks — seeds only set copy at row creation).

Text is duplicated here on purpose: migrations freeze data and must not
import application code.

Revision ID: a91b7c3e5d20
Revises: e5a2c8f41d07
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a91b7c3e5d20'
down_revision: Union[str, None] = 'e5a2c8f41d07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RAILWAY_COPY = {
    "gap_type": {
        "ZONE_BILL_NOT_FOUND":
            "Zone identified from narrative but no bill in the export. "
            "Check whether the bill sits under a different IREPS module "
            "or a later export.",
        "NON_IREPS_OR_UNRECOGNISED":
            "No railway zone in the narrative. Likely intercompany, "
            "metro, customs or a direct customer receipt. Route to the "
            "relevant sub-ledger.",
    },
    "expected_basis": {
        "ADVICE_DATE":
            "IREPS advised the bank but no credit landed. Chase the "
            "railway or check the next statement.",
        "CO7_ISSUED_NO_ADVICE":
            "CO7 raised, advice not yet issued. Expected to settle in a "
            "later statement, monitor only.",
    },
    "labels": {
        "ZONE_BILL_NOT_FOUND": "Zone found, bill missing",
        "NON_IREPS_OR_UNRECOGNISED": "Non-IREPS receipt",
        "CO7_ISSUED_NO_ADVICE": "CO7 issued, no advice",
    },
}


def _tables():
    customers = sa.table("customers", sa.column("id", sa.Integer()),
                         sa.column("key", sa.String(64)))
    rules = sa.table("match_rule_sets", sa.column("id", sa.Integer()),
                     sa.column("customer_id", sa.Integer()),
                     sa.column("is_default", sa.Boolean()),
                     sa.column("copy_overrides", sa.JSON()))
    return customers, rules


def upgrade() -> None:
    conn = op.get_bind()
    customers, rules = _tables()
    row = conn.execute(sa.select(customers.c.id)
                       .where(customers.c.key == "default")).first()
    if row is None:
        return
    conn.execute(rules.update()
                 .where(rules.c.customer_id == row[0],
                        rules.c.is_default.is_(True),
                        rules.c.copy_overrides.is_(None))
                 .values(copy_overrides=RAILWAY_COPY))


def downgrade() -> None:
    conn = op.get_bind()
    customers, rules = _tables()
    row = conn.execute(sa.select(customers.c.id)
                       .where(customers.c.key == "default")).first()
    if row is None:
        return
    # only unset if it still holds exactly what upgrade wrote
    for rid, copy in conn.execute(
            sa.select(rules.c.id, rules.c.copy_overrides)
            .where(rules.c.customer_id == row[0],
                   rules.c.is_default.is_(True))):
        if copy == RAILWAY_COPY:
            conn.execute(rules.update().where(rules.c.id == rid)
                         .values(copy_overrides=None))
