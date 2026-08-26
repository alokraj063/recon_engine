"""
All tables, snake_case, `customer_id` everywhere.

Real per-layer schema separation, not just a naming convention: every
model declares `schema="bronze"|"silver"|"gold"` (app/control-plane
tables omit it, landing in the default schema). On Postgres this is a
native `CREATE SCHEMA`. On SQLite — which has no schema concept — the
same declarations resolve via `ATTACH DATABASE` (db/base.py), so locally
each layer is genuinely a separate physical file: data/bronze.db,
data/silver.db, data/gold.db, joined to the main data/app.db connection.
One DATABASE_URL either way; the multi-file split is purely how SQLite
fakes what Postgres does natively.

Layers: bronze.files (raw file registry; bytes live on disk via
db.storage) -> silver.records (per-source parsed rows, JSON payload) ->
gold.* (common schema reconciliation runs on). Runs, per-customer config
and the Phase-6 ledgers live in the default (app) schema. Gold rows are
immutable facts; settlement state lives in match_ledger, never as gold
mutations.

FK target strings must be schema-qualified whenever the TARGET table has
a named schema (e.g. ForeignKey("gold.bills.id")) — this is required even
for same-schema references (gold -> gold), and unrelated to the
referencing table's own schema. Bare strings (e.g. ForeignKey("runs.id"))
are correct exactly when the target is app/default-schema, from any
referencer. SQLite additionally cannot enforce FK constraints that cross
ATTACHed files, so those edges are declared but unenforced locally
(harmless: no model uses relationship(), every join is explicit) while
becoming genuinely enforced on Postgres for the first time.

Every model is declared here from day one so alembic's initial revision
covers the full schema; later phases populate tables they need.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSON everywhere, JSONB on Postgres.
JSONVariant = JSON().with_variant(postgresql.JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# --- configuration -----------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourceConfig(Base):
    """Which adapter parses which input kind for a customer."""
    __tablename__ = "source_configs"
    __table_args__ = (UniqueConstraint("customer_id", "source_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))   # bank_statement | bill_status | lineage_rnote | lineage_crn
    adapter_key: Mapped[str] = mapped_column(String(64))   # hsbc | ireps | ireps_rnote | ireps_crn
    params: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MatchRuleSetRow(Base):
    """Per-customer matching tolerances; the API form tunables override."""
    __tablename__ = "match_rule_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="default")
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    date_tolerance_days: Mapped[int] = mapped_column(Integer, default=2)
    amount_tolerance: Mapped[float] = mapped_column(Float, default=0.0)
    window_days: Mapped[int] = mapped_column(Integer, default=0)
    co7_lookback_days: Mapped[int] = mapped_column(Integer, default=5)
    allow_batched: Mapped[bool] = mapped_column(Boolean, default=True)
    max_batch_size: Mapped[int] = mapped_column(Integer, default=3)
    paid_statuses: Mapped[list] = mapped_column(JSONVariant, default=list)
    weights: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    # which gold columns drive the match signals (recon.rules.FieldMapping
    # as a dict); NULL -> historical defaults
    field_map: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


# --- bronze / silver ---------------------------------------------------

class BronzeFile(Base):
    """Raw input file registry; bytes live on disk (db.storage)."""
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("customer_id", "sha256"),
        {"schema": "bronze"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    adapter_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SilverRecord(Base):
    """One parsed row in the source's native shape. One JSON table for
    every adapter: silver exists for audit and re-transform, and this way
    a new source needs zero migrations."""
    __tablename__ = "records"
    __table_args__ = {"schema": "silver"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    frame_name: Mapped[str] = mapped_column(String(64))
    row_seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONVariant)


# --- gold --------------------------------------------------------------
# uuid PKs are assigned app-side (pandas column) before insert so match
# results can reference gold rows without any DB read-back.
# run_id is nullable: Phase 6 makes gold rows ingestion-owned.

class GoldBankTxn(Base):
    __tablename__ = "bank_txns"
    __table_args__ = (
        # historical 3-col key (kept: unnamed, hard to drop on SQLite);
        # the named 2-col unique index below is the real key since
        # gold rows became ingestion-owned (one row per file row, ever)
        UniqueConstraint("bronze_file_id", "row_seq", "run_id"),
        Index("uq_bank_txns_file_seq", "bronze_file_id", "row_seq", unique=True),
        {"schema": "gold"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"), index=True)
    row_seq: Mapped[int] = mapped_column(Integer)
    bank_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    customer_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    txn_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    supplementary: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    txn_timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zone_guess: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    used_in_recon: Mapped[bool] = mapped_column(Boolean, default=False)
    extras: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


class GoldBill(Base):
    __tablename__ = "bills"
    __table_args__ = (
        # historical 3-col key (kept: unnamed, hard to drop on SQLite);
        # the named 2-col unique index below is the real key since
        # gold rows became ingestion-owned (one row per file row, ever)
        UniqueConstraint("bronze_file_id", "row_seq", "run_id"),
        Index("uq_bills_file_seq", "bronze_file_id", "row_seq", unique=True),
        {"schema": "gold"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"), index=True)
    row_seq: Mapped[int] = mapped_column(Integer)
    bill_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    contract_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contract_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    bill_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    zone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    vendor_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    vendor_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_advice_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    org_unit: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    submission_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    submission_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    bill_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gross_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    approved_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deduction_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_payable_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payment_order_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_order_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    recovery_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    return_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recovery_sum: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_check: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    recovery_check: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    sheet: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    header_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extras: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


class GoldRecovery(Base):
    __tablename__ = "recoveries"
    __table_args__ = (
        # historical 3-col key (kept: unnamed, hard to drop on SQLite);
        # the named 2-col unique index below is the real key since
        # gold rows became ingestion-owned (one row per file row, ever)
        UniqueConstraint("bronze_file_id", "row_seq", "run_id"),
        Index("uq_recoveries_file_seq", "bronze_file_id", "row_seq", unique=True),
        {"schema": "gold"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"), index=True)
    row_seq: Mapped[int] = mapped_column(Integer)
    gold_bill_id: Mapped[Optional[str]] = mapped_column(ForeignKey("gold.bills.id"), nullable=True, index=True)
    bill_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    submission_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sheet: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    recovery_head: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    recovery_amt: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recovery_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extras: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


class GoldLineageDoc(Base):
    """RNOTE + CRN unified with a doc_type discriminator. Storage mapping
    only — in-memory frames keep their RN_/CR_ shapes for attach_lineage."""
    __tablename__ = "lineage_docs"
    __table_args__ = (
        # historical 3-col key (kept: unnamed, hard to drop on SQLite);
        # the named 2-col unique index below is the real key since
        # gold rows became ingestion-owned (one row per file row, ever)
        UniqueConstraint("bronze_file_id", "row_seq", "run_id"),
        Index("uq_lineage_docs_file_seq", "bronze_file_id", "row_seq", unique=True),
        {"schema": "gold"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"), index=True)
    row_seq: Mapped[int] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(String(8))          # RNOTE | CRN
    doc_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    doc_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    submission_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payment_order_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    po_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    po_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    # string, not float: IREPS writes quantities like "3 Set"
    receipt_qty: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    drr_or_challan_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bill_reg_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    extras: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


# --- runs --------------------------------------------------------------

class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        # one incremental run in flight per customer (Phase 6 guard)
        Index(
            "uq_runs_one_running_per_customer", "customer_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    rule_set_id: Mapped[Optional[int]] = mapped_column(ForeignKey("match_rule_sets.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | succeeded | failed
    mode: Mapped[str] = mapped_column(String(16), default="snapshot")   # snapshot | incremental
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    params: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)     # effective config
    payload: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)    # exact POST response body
    selfcheck: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)
    workbook_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)


class RunFrame(Base):
    __tablename__ = "run_frames"
    __table_args__ = (UniqueConstraint("run_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    rows: Mapped[list] = mapped_column(JSONVariant)   # df_to_records() output


class RunMatchBill(Base):
    """Durable lineage: which gold bills each match consumed/considered."""
    __tablename__ = "run_match_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    match_id: Mapped[str] = mapped_column(String(16))         # "m{n}"
    gold_bill_id: Mapped[str] = mapped_column(ForeignKey("gold.bills.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))             # picked | candidate


# --- Phase 6 ledgers ---------------------------------------------------

class MatchLedger(Base):
    """One row per durable credit<->bill(s) match across runs."""
    __tablename__ = "match_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)   # run that created it
    match_id: Mapped[str] = mapped_column(String(16))
    gold_bank_txn_id: Mapped[str] = mapped_column(ForeignKey("gold.bank_txns.id"), index=True)
    confidence: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="OPEN")   # OPEN | LOCKED | REJECTED
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # AUTO_HIGH | USER
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MatchLedgerBill(Base):
    __tablename__ = "match_ledger_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_ledger_id: Mapped[str] = mapped_column(ForeignKey("match_ledger.id"), index=True)
    gold_bill_id: Mapped[str] = mapped_column(ForeignKey("gold.bills.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="picked")


class ExceptionLedger(Base):
    """Exceptions persist across runs until a later run matches them."""
    __tablename__ = "exception_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    exception_type: Mapped[str] = mapped_column(String(16))   # BANK_ONLY | BILL_ONLY
    gold_bank_txn_id: Mapped[Optional[str]] = mapped_column(ForeignKey("gold.bank_txns.id"), nullable=True, index=True)
    gold_bill_id: Mapped[Optional[str]] = mapped_column(ForeignKey("gold.bills.id"), nullable=True, index=True)
    first_seen_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(16), default="OPEN")   # OPEN | RESOLVED
    resolved_by_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IngestConflict(Base):
    """A newer export tried to change a bill consumed by a LOCKED match.
    The change is recorded here and the gold row left untouched."""
    __tablename__ = "ingest_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    gold_bill_id: Mapped[str] = mapped_column(ForeignKey("gold.bills.id"), index=True)
    bronze_file_id: Mapped[int] = mapped_column(ForeignKey("bronze.files.id"))
    changed_fields: Mapped[dict] = mapped_column(JSONVariant)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- audit trail ---------------------------------------------------------

class AuditLog(Base):
    """General-purpose, consistently-shaped event stream tying every layer
    together — not a replacement for match_ledger/exception_ledger/
    ingest_conflicts, which remain the detailed, domain-specific trails
    for their own concerns. Default/app schema: audit events describe
    actions across bronze/silver/gold/app, they don't belong to one layer.

    entity_type/entity_id are a polymorphic reference, not a real FK —
    they can point at bronze/silver/gold tables living in different
    ATTACHed files, where cross-schema FKs are unenforced on SQLite
    anyway (see the module docstring above). Written only via
    db/audit.py:record_event(), inside the caller's existing transaction
    — never a separate commit — so a log line and its audit row can never
    drift from each other, and a rollback erases both together."""
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_customer_created", "customer_id", "created_at"),
        Index("ix_audit_log_event_created", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="INFO")
    entity_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
