"""canonicalize gold bill columns

Hand-written (autogenerate is unusable on this SQLite-ATTACH setup: it
emits spurious cross-schema FK diffs and has produced real bugs before).

Three parts, all reversible:
  1. Column renames on gold.bills / gold.recoveries / gold.lineage_docs —
     plain RENAME COLUMN, supported natively by SQLite >= 3.25 and
     Postgres, so no batch table-recreate is needed. Index names created
     for the old columns keep their old names pointing at the renamed
     columns (harmless drift; normalize on Postgres later if desired).
  2. extras JSON key rewrite: frame_from_gold restores extras keys
     verbatim as frame columns, so pre-migration rows must carry the
     canonical keys (recoveries/unparsed_header/bill_index) or the
     rebuilt pool frames silently show holes.
  3. match_rule_sets.field_map blob rewrite: saved per-customer field
     mappings reference engine column names; stale names would load
     silently via FieldMapping.from_dict and KeyError inside the matcher
     at run time. Unknown values pass through, so re-running is a no-op.

Revision ID: c7d1e4a90b23
Revises: 2a94c1e07f31
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op

revision = "c7d1e4a90b23"
down_revision = "2a94c1e07f31"
branch_labels = None
depends_on = None

BILLS_RENAMES = [
    ("co6_no", "submission_ref", sa.String(64)),
    ("co6_date", "submission_date", sa.Date()),
    ("co7_no", "payment_order_ref", sa.String(64)),
    ("co7_date", "payment_order_date", sa.Date()),
    ("party_code", "vendor_code", sa.String(64)),
    ("party_name", "vendor_name", sa.String(200)),
    ("accounting_unit", "org_unit", sa.String(100)),
    ("status", "bill_status", sa.String(64)),
    ("reason_for_return", "return_reason", sa.Text()),
    ("bill_amt", "gross_amount", sa.Float()),
    ("passed_amt", "approved_amount", sa.Float()),
    ("deducted_amt", "deduction_amount", sa.Float()),
    ("net_amt", "net_payable_amount", sa.Float()),
]
RECOVERIES_RENAMES = [("co6_no", "submission_ref", sa.String(64))]
LINEAGE_RENAMES = [
    ("co6_no", "submission_ref", sa.String(64)),
    ("co7_no", "payment_order_ref", sa.String(64)),
]

BILL_EXTRAS_KEYS = {"Recoveries": "recoveries",
                    "UnparsedHeader": "unparsed_header"}
RECOVERY_EXTRAS_KEYS = {"BillIndex": "bill_index"}

# engine-name rewrite for saved field_map blobs (old -> canonical)
FIELD_MAP_NAMES = {
    "BillNumber": "bill_number", "BillDate": "bill_date",
    "ContractNo": "contract_no", "ContractDate": "contract_date",
    "CO6No": "submission_ref", "CO6Date": "submission_date",
    "CO7No": "payment_order_ref", "CO7Date": "payment_order_date",
    "PaymentAdviceDateToBank": "payment_advice_date",
    "PartyCode": "vendor_code", "PartyName": "vendor_name",
    "AccountingUnit": "org_unit", "Zone": "zone", "Status": "bill_status",
    "ReasonForReturn": "return_reason", "BillAmt": "gross_amount",
    "PassedAmt": "approved_amount", "DeductedAmt": "deduction_amount",
    "NetAmt": "net_payable_amount", "Recoveries": "recoveries",
    "RecoveryCount": "recovery_count", "RecoverySum": "recovery_sum",
    "NetCheck": "net_check", "RecoveryCheck": "recovery_check",
    "Sheet": "sheet", "HeaderRow": "header_row", "DataRow": "data_row",
    "UnparsedHeader": "unparsed_header", "UsedInRecon": "used_in_recon",
}


def _rename_columns(table, renames, schema, reverse=False):
    for old, new, coltype in renames:
        src, dst = (new, old) if reverse else (old, new)
        op.alter_column(table, src, new_column_name=dst,
                        existing_type=coltype, schema=schema)


def _extras_table(name):
    return sa.table(name, sa.column("id", sa.String(32)),
                    sa.column("extras", sa.JSON()), schema="gold")


def _rewrite_extras(table_name, key_map):
    conn = op.get_bind()
    t = _extras_table(table_name)
    rows = conn.execute(
        sa.select(t.c.id, t.c.extras).where(t.c.extras.is_not(None))).all()
    for row_id, extras in rows:
        if not isinstance(extras, dict) or not (set(extras) & set(key_map)):
            continue
        conn.execute(t.update().where(t.c.id == row_id).values(
            extras={key_map.get(k, k): v for k, v in extras.items()}))


def _rewrite_field_maps(name_map):
    conn = op.get_bind()
    t = sa.table("match_rule_sets", sa.column("id", sa.Integer()),
                 sa.column("field_map", sa.JSON()))
    rows = conn.execute(
        sa.select(t.c.id, t.c.field_map)
        .where(t.c.field_map.is_not(None))).all()
    for row_id, fm in rows:
        if not isinstance(fm, dict):
            continue
        new = dict(fm)
        for key in ("bank_amount_field", "bill_amount_field",
                    "bank_date_field", "bill_date_primary",
                    "bill_date_fallback", "eligibility_field"):
            if isinstance(new.get(key), str):
                new[key] = name_map.get(new[key], new[key])
        if isinstance(new.get("exact_signals"), list):
            new["exact_signals"] = [
                {**s,
                 "bank_field": name_map.get(s.get("bank_field"), s.get("bank_field")),
                 "bill_field": name_map.get(s.get("bill_field"), s.get("bill_field"))}
                if isinstance(s, dict) else s
                for s in new["exact_signals"]]
        if new != fm:
            conn.execute(t.update().where(t.c.id == row_id)
                         .values(field_map=new))


def upgrade() -> None:
    _rename_columns("bills", BILLS_RENAMES, "gold")
    _rename_columns("recoveries", RECOVERIES_RENAMES, "gold")
    _rename_columns("lineage_docs", LINEAGE_RENAMES, "gold")
    _rewrite_extras("bills", BILL_EXTRAS_KEYS)
    _rewrite_extras("recoveries", RECOVERY_EXTRAS_KEYS)
    _rewrite_field_maps(FIELD_MAP_NAMES)


def downgrade() -> None:
    _rewrite_field_maps({v: k for k, v in FIELD_MAP_NAMES.items()})
    _rewrite_extras("recoveries", {v: k for k, v in RECOVERY_EXTRAS_KEYS.items()})
    _rewrite_extras("bills", {v: k for k, v in BILL_EXTRAS_KEYS.items()})
    _rename_columns("lineage_docs", LINEAGE_RENAMES, "gold", reverse=True)
    _rename_columns("recoveries", RECOVERIES_RENAMES, "gold", reverse=True)
    _rename_columns("bills", BILLS_RENAMES, "gold", reverse=True)
