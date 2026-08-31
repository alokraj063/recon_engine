"""configurability columns (plan phases 1-4)

Hand-written (autogenerate is unusable on this SQLite-ATTACH setup: it
emits spurious cross-schema FK diffs). Plain ADD COLUMNs — no batch
table-recreates needed — plus one backfill:

  1. match_rule_sets.copy_overrides (JSON, NULL = default advisory text)
     and three nullable scalar knobs (batch_amount_slack, amount_decimals,
     ar_overdue_days; NULL = MatchRuleSet dataclass defaults).
  2. gold.lineage_docs.invoice_date / bill_reg_date — two previously
     extras-only dates promoted to typed columns for the canonical
     lineage trail (they feed the Invoice_Date / Bill_Reg_Date trail
     columns). Pre-migration rows are backfilled FROM their extras keys
     (RN_InvoiceDate/CR_InvoiceDate, RN_BillRegDate/CR_BillRegDate).
  3. source_configs.role (bank_statement | bill_status | lineage),
     backfilled from source_type so pre-migration rows keep working.

Revision ID: e5a2c8f41d07
Revises: 0309779e4357
Create Date: 2026-08-31
"""
from datetime import date, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5a2c8f41d07'
down_revision: Union[str, None] = '0309779e4357'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


# extras keys that feed each new lineage date column, in coalesce order
LINEAGE_DATE_BACKFILL = {
    "invoice_date": ("RN_InvoiceDate", "CR_InvoiceDate"),
    "bill_reg_date": ("RN_BillRegDate", "CR_BillRegDate"),
}


def _parse_date(v):
    if v is None or isinstance(v, (date, datetime)):
        return v.date() if isinstance(v, datetime) else v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat", "----"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:  # ISO with time/fraction
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def upgrade() -> None:
    op.add_column('match_rule_sets',
                  sa.Column('copy_overrides', _json(), nullable=True))
    op.add_column('match_rule_sets',
                  sa.Column('batch_amount_slack', sa.Float(), nullable=True))
    op.add_column('match_rule_sets',
                  sa.Column('amount_decimals', sa.Integer(), nullable=True))
    op.add_column('match_rule_sets',
                  sa.Column('ar_overdue_days', sa.Integer(), nullable=True))

    op.add_column('lineage_docs',
                  sa.Column('invoice_date', sa.Date(), nullable=True),
                  schema='gold')
    op.add_column('lineage_docs',
                  sa.Column('bill_reg_date', sa.Date(), nullable=True),
                  schema='gold')

    op.add_column('source_configs',
                  sa.Column('role', sa.String(16), nullable=True))

    conn = op.get_bind()

    # backfill lineage dates from extras (attach_lineage now reads the
    # typed columns; pre-migration rows would otherwise lose these dates)
    t = sa.table('lineage_docs', sa.column('id', sa.String(32)),
                 sa.column('extras', sa.JSON()),
                 sa.column('invoice_date', sa.Date()),
                 sa.column('bill_reg_date', sa.Date()),
                 schema='gold')
    for row_id, extras in conn.execute(
            sa.select(t.c.id, t.c.extras).where(t.c.extras.is_not(None))):
        if not isinstance(extras, dict):
            continue
        values = {}
        for col, keys in LINEAGE_DATE_BACKFILL.items():
            for key in keys:
                d = _parse_date(extras.get(key))
                if d is not None:
                    values[col] = d
                    break
        if values:
            conn.execute(t.update().where(t.c.id == row_id).values(**values))

    # backfill source_configs.role from source_type
    sc = sa.table('source_configs', sa.column('id', sa.Integer()),
                  sa.column('source_type', sa.String(32)),
                  sa.column('role', sa.String(16)))
    conn.execute(sc.update()
                 .where(sc.c.source_type.like('lineage%'))
                 .values(role='lineage'))
    for st in ('bank_statement', 'bill_status'):
        conn.execute(sc.update()
                     .where(sc.c.source_type == st).values(role=st))


def downgrade() -> None:
    op.drop_column('source_configs', 'role')
    op.drop_column('lineage_docs', 'bill_reg_date', schema='gold')
    op.drop_column('lineage_docs', 'invoice_date', schema='gold')
    op.drop_column('match_rule_sets', 'ar_overdue_days')
    op.drop_column('match_rule_sets', 'amount_decimals')
    op.drop_column('match_rule_sets', 'batch_amount_slack')
    op.drop_column('match_rule_sets', 'copy_overrides')
