"""gold.bills / gold.recoveries: `sheet` -> `operating_unit`, backfilled

The Bill Status export is now a single worksheet, so the worksheet name
no longer identifies the operating unit a bill belongs to. The gold
column is renamed to say what it now holds and backfilled from the
IREPS PartyCode (gold vendor_code) suffix — the same rule the IREPS
bills adapter applies from here on (recon/sources/ireps_bills.py
OPERATING_UNIT_SUFFIXES): ...1065309 = Friction, ...60828 = Rohtak,
...833 = Hosur; anything else = NULL. Recovery lines inherit their
bill's unit via gold_bill_id.

Hand-written (see f1c6b2d84a95 for why autogenerate is not trusted
against SQLite here). Downgrade restores the column name only — the old
worksheet-name values are not recoverable and were never meaningful.

Revision ID: a9e4d7c2b301
Revises: f1c6b2d84a95
Create Date: 2026-09-07 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a9e4d7c2b301'
down_revision: Union[str, None] = 'f1c6b2d84a95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# longest suffix first: a code can only ever match one unit
SUFFIX_UNITS = (("1065309", "Friction"), ("60828", "Rohtak"), ("833", "Hosur"))


def upgrade() -> None:
    for table in ("bills", "recoveries"):
        op.alter_column(table, "sheet", new_column_name="operating_unit",
                        existing_type=sa.String(64), schema="gold")

    bills = sa.table("bills", sa.column("id", sa.String(32)),
                     sa.column("vendor_code", sa.String(64)),
                     sa.column("operating_unit", sa.String(64)), schema="gold")
    case = sa.case(
        *[(bills.c.vendor_code.like(f"%{suffix}"), unit)
          for suffix, unit in SUFFIX_UNITS],
        else_=None)
    op.execute(bills.update().values(operating_unit=case))

    recoveries = sa.table("recoveries", sa.column("id", sa.String(32)),
                          sa.column("gold_bill_id", sa.String(32)),
                          sa.column("operating_unit", sa.String(64)),
                          schema="gold")
    unit_of_bill = (sa.select(bills.c.operating_unit)
                    .where(bills.c.id == recoveries.c.gold_bill_id)
                    .scalar_subquery())
    op.execute(recoveries.update().values(operating_unit=unit_of_bill))


def downgrade() -> None:
    for table in ("bills", "recoveries"):
        op.alter_column(table, "operating_unit", new_column_name="sheet",
                        existing_type=sa.String(64), schema="gold")
