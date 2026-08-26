"""
Gold persistence. The in-memory gold frames and the DB tables share ONE
canonical vocabulary, so the source-frame maps below are identity — kept
as explicit dicts because they still decide which columns are typed DB
columns (everything else lands in extras) and because RNOTE/CRN collapse
the engine-internal RN_*/CR_* lineage shapes into the unified table.

uuid ids are assigned app-side per row, so match results can reference
gold bills without any DB read-back; the {row_seq: id} maps returned here
feed run_match_bills.
"""

import uuid
from datetime import date, datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy import Boolean, Date, Float, Integer, String, Text

from logging_setup import get_logger

from .audit import record_event
from .models import GoldBankTxn, GoldBill, GoldLineageDoc, GoldRecovery

logger = get_logger(__name__)

BANK_MAP = {
    "bank_ref": "bank_ref", "customer_ref": "customer_ref",
    "txn_type": "txn_type", "supplementary": "supplementary",
    "narrative": "narrative", "value_date": "value_date", "amount": "amount",
    "timestamp": "txn_timestamp", "page": "page", "zone_guess": "zone_guess",
    "used_in_recon": "used_in_recon",
}

BILLS_MAP = {c: c for c in (
    "bill_number", "contract_no", "contract_date", "bill_date", "zone",
    "vendor_name", "vendor_code", "payment_advice_date", "org_unit",
    "submission_ref", "submission_date", "bill_status", "gross_amount",
    "approved_amount", "deduction_amount", "net_payable_amount",
    "payment_order_ref", "payment_order_date", "recovery_count",
    "return_reason", "recovery_sum", "net_check", "recovery_check",
    "sheet", "header_row", "data_row",
)}

RECOVERIES_MAP = {c: c for c in (
    "bill_number", "submission_ref", "sheet", "recovery_head",
    "recovery_amt", "recovery_text",
)}

RNOTE_MAP = {
    "RNoteNo": "doc_no", "RNoteDate": "doc_date", "InvoiceNo": "invoice_no",
    "CO6No": "submission_ref", "CO7No": "payment_order_ref",
    "RN_PONo": "po_no", "RN_PODate": "po_date", "RNoteQty": "receipt_qty",
    "RN_DRRNo": "drr_or_challan_no", "RN_BillRegNo": "bill_reg_no",
}

CRN_MAP = {
    "CRNNo": "doc_no", "CRNDate": "doc_date", "InvoiceNo": "invoice_no",
    "CO6No": "submission_ref", "CO7No": "payment_order_ref",
    "CR_PONo": "po_no", "CR_PODate": "po_date", "CR_Qty": "receipt_qty",
    "CR_ChallanNo": "drr_or_challan_no", "CR_BillRegNo": "bill_reg_no",
}

_HELPER_COLS = ("row_seq", "bill_row_seq")


def _is_na(v) -> bool:
    return v is None or (pd.api.types.is_scalar(v) and pd.isna(v))


def _json_clean(v):
    """JSON-safe value for the extras column. Deliberately mirrors
    app.serialize.clean without importing it (db never imports app)."""
    if isinstance(v, dict):
        return {str(k): _json_clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, np.ndarray)):
        return [_json_clean(x) for x in v]
    if _is_na(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _coerce(v, sa_type):
    """One DB-ready python value for a typed column."""
    if _is_na(v):
        return None
    if isinstance(sa_type, Date):
        if isinstance(v, (pd.Timestamp, datetime)):
            return v.date()
        if isinstance(v, date):
            return v
        return None
    if isinstance(sa_type, (String, Text)):
        return str(v)
    if isinstance(sa_type, Float):
        return float(v)
    if isinstance(sa_type, Integer):
        return int(v)
    if isinstance(sa_type, Boolean):
        return bool(v)
    return v


def _persist_frame(session, model, df: pd.DataFrame, colmap: dict,
                   base: dict, resolve=None) -> Dict[int, str]:
    """Insert one gold frame; returns {row_seq: id}. Columns not in the
    map (and not bookkeeping) go into extras verbatim."""
    columns = model.__table__.columns
    extras_cols = [c for c in df.columns
                   if c not in colmap and c not in _HELPER_COLS]
    mappings, ids = [], {}
    for rec in df.to_dict(orient="records"):
        row_id = uuid.uuid4().hex
        seq = int(rec["row_seq"])
        ids[seq] = row_id
        m = {"id": row_id, "row_seq": seq, **base}
        for src, dst in colmap.items():
            m[dst] = _coerce(rec.get(src), columns[dst].type)
        if extras_cols:
            extras = {c: _json_clean(rec[c]) for c in extras_cols if c in rec}
            m["extras"] = extras or None
        if resolve is not None:
            m.update(resolve(rec))
        mappings.append(m)
    if mappings:
        session.bulk_insert_mappings(model, mappings)
    return ids



# engine frame columns that must come back as Timestamps when a frame is
# rebuilt from gold rows (Phase 6 incremental pool)
FRAME_DATE_COLS = {
    "bank_txns": ["value_date"],
    "bills": ["contract_date", "bill_date", "payment_advice_date",
              "submission_date", "payment_order_date"],
    "lineage_rnote": ["RNoteDate", "RN_PODate"],
    "lineage_crn": ["CRNDate", "CR_PODate"],
    # canonical browse view of gold.lineage_docs (db/reconcile_gold.py)
    "lineage_view": ["doc_date", "po_date"],
}


def frame_from_gold(rows, colmap: dict, frame_name: str,
                    ensure=None) -> "tuple[pd.DataFrame, list]":
    """Rebuild an engine-shaped frame from gold ORM rows. Returns
    (frame, ids) where ids[i] is the gold id of frame row i — positional,
    exactly like the matcher's bill_indices. extras columns are restored
    verbatim; canonical columns win over extras on collision."""
    recs, ids = [], []
    for r in rows:
        d = dict(r.extras) if r.extras else {}
        for src, dst in colmap.items():
            d[src] = getattr(r, dst)
        recs.append(d)
        ids.append(r.id)
    df = pd.DataFrame(recs)
    if ensure is not None:
        df = ensure(df, frame_name)
    for c in FRAME_DATE_COLS.get(frame_name, []):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c])
    return df.reset_index(drop=True), ids


def persist_gold(session, customer_id: int, run_id: Optional[str],
                 gold_frames: Dict[str, pd.DataFrame],
                 bronze_ids: Dict[str, int]) -> Dict[str, Dict[int, str]]:
    """Persist every gold frame of a run. gold_frames/bronze_ids are keyed
    by gold frame name. Returns {frame_name: {row_seq: gold id}}."""
    def base(frame):
        return {"customer_id": customer_id, "run_id": run_id,
                "bronze_file_id": bronze_ids[frame]}

    ids: Dict[str, Dict[int, str]] = {}
    if "bank_txns" in gold_frames:
        ids["bank_txns"] = _persist_frame(
            session, GoldBankTxn, gold_frames["bank_txns"], BANK_MAP,
            base("bank_txns"))
    if "bills" in gold_frames:
        ids["bills"] = _persist_frame(
            session, GoldBill, gold_frames["bills"], BILLS_MAP, base("bills"))
    if "recoveries" in gold_frames:
        bill_ids = ids.get("bills", {})

        def link_bill(rec):
            seq = rec.get("bill_row_seq")
            return {"gold_bill_id": bill_ids.get(int(seq))
                    if not _is_na(seq) else None}

        ids["recoveries"] = _persist_frame(
            session, GoldRecovery, gold_frames["recoveries"], RECOVERIES_MAP,
            base("recoveries"), resolve=link_bill)
    if "lineage_rnote" in gold_frames:
        ids["lineage_rnote"] = _persist_frame(
            session, GoldLineageDoc, gold_frames["lineage_rnote"], RNOTE_MAP,
            {**base("lineage_rnote"), "doc_type": "RNOTE"})
    if "lineage_crn" in gold_frames:
        ids["lineage_crn"] = _persist_frame(
            session, GoldLineageDoc, gold_frames["lineage_crn"], CRN_MAP,
            {**base("lineage_crn"), "doc_type": "CRN"})
    record_event(session, logger, event_type="gold.rows_persisted",
                 customer_id=customer_id, run_id=run_id,
                 details={frame: len(ids_) for frame, ids_ in ids.items()})
    return ids
