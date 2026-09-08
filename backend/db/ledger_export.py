"""
Ledger -> plain DataFrames for the Excel deliveries of item 3.3.

A manual match belongs to no run and every decision happens after a
run's workbook was written, so neither can live in the stored file.
Both deliveries are composed at DOWNLOAD time from the ledger:

* run_ledger_frames(run_id): the sheets appended to a run's workbook —
  Manual_Matches (manual matches touching this run's credits or bills)
  and Decisions (live status of every match this run created). Empty
  for a snapshot run (nothing in the ledger) -> the caller returns the
  stored file unchanged, byte-identical.
* customer_ledger_frames(customer_pk): the durable customer-level
  export — Matches, Manual_Matches, Exceptions.

recon/report.py does the writing; this module only knows the tables.
"""

from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import select

from .base import SessionLocal
from .incremental import MANUAL_CONFIDENCE
from .models import (ExceptionLedger, GoldBankTxn, GoldBill, MatchLedger,
                     MatchLedgerBill, Run, RunFrame)


def _label(m: MatchLedger) -> str:
    return f"M-{m.seq}" if m.seq is not None else m.id[:8]


def _iso(d):
    return d.isoformat() if d else None


def _load(session, customer_pk: int, match_rows: List[MatchLedger]):
    """Bulk-load links + the gold rows behind a list of matches."""
    links = list(session.execute(
        select(MatchLedgerBill)
        .where(MatchLedgerBill.match_ledger_id.in_([m.id for m in match_rows]),
               MatchLedgerBill.role == "picked")).scalars()) if match_rows else []
    txn_ids = {m.gold_bank_txn_id for m in match_rows}
    bill_ids = {l.gold_bill_id for l in links}
    txns = {t.id: t for t in session.execute(
        select(GoldBankTxn).where(GoldBankTxn.id.in_(txn_ids))).scalars()} if txn_ids else {}
    bills = {b.id: b for b in session.execute(
        select(GoldBill).where(GoldBill.id.in_(bill_ids))).scalars()} if bill_ids else {}
    by_match: Dict[str, list] = {}
    for l in links:
        by_match.setdefault(l.match_ledger_id, []).append(bills.get(l.gold_bill_id))
    return txns, by_match


def _match_record(m: MatchLedger, txn, picked) -> dict:
    picked = [b for b in picked if b is not None]
    net = sum((b.net_payable_amount or 0.0) for b in picked)
    return {
        "Match": _label(m),
        "Confidence": m.confidence,
        "Status": m.status,
        "Locked_By": m.locked_by,
        "Locked_At": m.locked_at,
        "Created_At": m.created_at,
        "Run": m.run_id,
        "Run_Match_Id": m.match_id,
        "Bank_Ref": txn.bank_ref if txn else None,
        "Value_Date": txn.value_date if txn else None,
        "Credit_Amount": txn.amount if txn else None,
        "Bill_Count": len(picked),
        "Bill_Numbers": "; ".join(str(b.bill_number) for b in picked),
        "Submission_Refs": "; ".join(str(b.submission_ref) for b in picked if b.submission_ref),
        "Net_Payable": net if picked else None,
        "Variance": (round((txn.amount or 0.0) - net, 2)
                     if txn is not None and picked else None),
        "Note": m.note,
    }


MATCH_COLUMNS = [
    "Match", "Confidence", "Status", "Locked_By", "Locked_At", "Created_At",
    "Run", "Run_Match_Id", "Bank_Ref", "Value_Date", "Credit_Amount",
    "Bill_Count", "Bill_Numbers", "Submission_Refs", "Net_Payable",
    "Variance", "Note"]
MANUAL_COLUMNS = ["Match", "Status", "Bank_Ref", "Value_Date", "Credit_Amount",
                  "Bill_Numbers", "Submission_Refs", "Net_Payable", "Variance",
                  "Locked_At", "Note"]
DECISION_COLUMNS = ["Match", "Run_Match_Id", "Confidence", "Status", "Locked_By",
                    "Locked_At", "Bank_Ref", "Credit_Amount", "Bill_Numbers"]


def _frame(records: List[dict], columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records, columns=columns)


def run_ledger_frames(run_id: str) -> Dict[str, pd.DataFrame]:
    """{Manual_Matches, Decisions} for one run, or {} when the run created
    no ledger rows (snapshot runs) and no manual match touches it."""
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            return {}
        created = list(session.execute(
            select(MatchLedger).where(MatchLedger.run_id == run_id)
            .order_by(MatchLedger.seq)).scalars())
        manual = list(session.execute(
            select(MatchLedger).where(
                MatchLedger.customer_id == run.customer_id,
                MatchLedger.confidence == MANUAL_CONFIDENCE,
                MatchLedger.status != "REJECTED")
            .order_by(MatchLedger.seq)).scalars())
        txns, by_match = _load(session, run.customer_id, created + manual)

        # which manual matches touch THIS run: its credit is in the run's
        # bank frame, or a picked bill is in its bills frame
        frames = {}
        if manual:
            refs = set()
            bill_keys = set()
            for f in session.execute(
                    select(RunFrame).where(RunFrame.run_id == run_id,
                                           RunFrame.name.in_(("bank", "bills")))).scalars():
                for r in f.rows or []:
                    if f.name == "bank":
                        refs.add(str(r.get("bank_ref")))
                    else:
                        bill_keys.add((str(r.get("bill_number")),
                                       str(r.get("submission_ref"))))
            touching = []
            for m in manual:
                txn = txns.get(m.gold_bank_txn_id)
                picked = [b for b in by_match.get(m.id, []) if b is not None]
                if (txn is not None and str(txn.bank_ref) in refs) or any(
                        (str(b.bill_number), str(b.submission_ref)) in bill_keys
                        for b in picked):
                    touching.append(_match_record(m, txn, picked))
            if touching:
                frames["Manual_Matches"] = _frame(touching, MANUAL_COLUMNS)
        if created:
            frames["Decisions"] = _frame(
                [_match_record(m, txns.get(m.gold_bank_txn_id), by_match.get(m.id, []))
                 for m in created], DECISION_COLUMNS)
        return frames


def customer_ledger_frames(customer_pk: int) -> Dict[str, pd.DataFrame]:
    """Matches (all non-rejected incl. manual), Manual_Matches, Exceptions."""
    with SessionLocal() as session:
        matches = list(session.execute(
            select(MatchLedger).where(MatchLedger.customer_id == customer_pk)
            .order_by(MatchLedger.seq)).scalars())
        txns, by_match = _load(session, customer_pk, matches)
        recs = [_match_record(m, txns.get(m.gold_bank_txn_id), by_match.get(m.id, []))
                for m in matches if m.status != "REJECTED"]
        manual = [r for r in recs if r["Confidence"] == MANUAL_CONFIDENCE]
        seq_by_id = {m.id: _label(m) for m in matches}

        exc_rows = list(session.execute(
            select(ExceptionLedger).where(ExceptionLedger.customer_id == customer_pk)
            .order_by(ExceptionLedger.status, ExceptionLedger.exception_type)).scalars())
        txn_ids = {e.gold_bank_txn_id for e in exc_rows if e.gold_bank_txn_id}
        bill_ids = {e.gold_bill_id for e in exc_rows if e.gold_bill_id}
        etxns = {t.id: t for t in session.execute(
            select(GoldBankTxn).where(GoldBankTxn.id.in_(txn_ids))).scalars()} if txn_ids else {}
        ebills = {b.id: b for b in session.execute(
            select(GoldBill).where(GoldBill.id.in_(bill_ids))).scalars()} if bill_ids else {}
        exceptions = []
        for e in exc_rows:
            t = etxns.get(e.gold_bank_txn_id) if e.gold_bank_txn_id else None
            b = ebills.get(e.gold_bill_id) if e.gold_bill_id else None
            exceptions.append({
                "Type": e.exception_type,
                "Status": e.status,
                "Bank_Ref": t.bank_ref if t else None,
                "Value_Date": t.value_date if t else None,
                "Credit_Amount": t.amount if t else None,
                "Bill_Number": b.bill_number if b else None,
                "Submission_Ref": b.submission_ref if b else None,
                "Net_Payable": b.net_payable_amount if b else None,
                "Bill_Status": b.bill_status if b else None,
                "First_Seen_Run": e.first_seen_run_id,
                "Resolved_By": e.resolved_by,
                "Resolved_By_Run": e.resolved_by_run_id,
                "Resolved_By_Match": seq_by_id.get(e.resolved_by_match_id),
                "Resolved_At": e.resolved_at,
            })
        return {
            "Matches": _frame(recs, MATCH_COLUMNS),
            "Manual_Matches": _frame(manual, MANUAL_COLUMNS),
            "Exceptions": _frame(exceptions, [
                "Type", "Status", "Bank_Ref", "Value_Date", "Credit_Amount",
                "Bill_Number", "Submission_Ref", "Net_Payable", "Bill_Status",
                "First_Seen_Run", "Resolved_By", "Resolved_By_Run",
                "Resolved_By_Match", "Resolved_At"]),
        }
