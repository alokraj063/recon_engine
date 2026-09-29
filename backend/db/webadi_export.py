"""
Confirmed matches -> Oracle "AR Receipt Upload" WebADI records.

Finance uploads the file into Oracle at day end; it is composed at
DOWNLOAD time from the match ledger + gold, never stored. Scope: every
LOCKED match (auto-HIGH, analyst-accepted, manual) whose CREDIT's value
date falls in [date_from, date_to]. Review matches, rejected matches,
unmatched and non-IREPS credits are left out — Oracle gets only money
already tied to a bill.

Row shape: one record per (credit, bill, recovery line). Every field is
repeated on each of a bill's recovery rows — only Adjustment Amount and
Adjustment Type change — and a bill with no recovery lines is one record
with both blank. A batched credit repeats its receipt fields per bill.

Anything this module cannot derive with certainty stays BLANK and is
named in the record's `issues`, never guessed: Friction's receipt
method and adjustment-type prefix, a zone the directory does not know,
a recovery head with no Oracle adjustment type.

Column sources (spec 2026-09-29):
  Operating Unit ID Selected  bill.operating_unit (PartyCode suffix) -> OU_NAMES
  Customer Name               bill zone -> zone directory: "<CODE>-<NAME>"
  Receipt Method              by unit (RECEIPT_METHODS)
  Receipt Number              credit bank_ref (the UTR)
  Currency                    INR
  Receipt Amount / Date       credit amount / value_date
  GL Date                     the caller's date, else the receipt date
                              (the ADI marks it mandatory; the template's
                              sample rows post on the receipt date)
  Invoice Number              bill_number
  Receipt Amount Applied      bill net_payable_amount
  Adjustment Amount / Type    recovery line amount / head -> Oracle type
  Factoring Amount            not in use
"""

from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select

from recon.report import WEBADI_META_KEYS

from . import zones as db_zones
from .models import (GoldBankTxn, GoldBill, GoldRecovery, MatchLedger,
                     MatchLedgerBill, MatchRuleSetRow)

UNITS = ("Hosur", "Rohtak", "Friction")
UNASSIGNED = "Unassigned"

OU_NAMES = {
    "Hosur": "Faiveley Transport Rail Technologies India Private Limited - Hosur B&S",
    "Rohtak": "Faiveley Transport Rail Technologies India Private Limited - Rohtak B&S",
    "Friction": "Faiveley Transport Rail Technologies India Private Limited (Friction)",
}
# Oracle org prefix of each unit's receipt methods, responsibilities and
# adjustment types. Friction's is not known yet (with Tendering).
UNIT_PREFIX = {"Hosur": "WABHSR", "Rohtak": "WABRTK", "Friction": None}
RECEIPT_METHOD = "{prefix}_Receipt_HSBC_INR-7001"
RESPONSIBILITY = "Receivables Super User GUI - {prefix}"
CURRENCY = "INR"

# Bill Status recovery head -> Oracle adjustment type suffix, first
# keyword hit wins (order matters: a liquidated-damages "DEPOSIT STORES"
# head is LD, not a deposit; GST TDS is GST, not income-tax TDS). The
# spellings follow the Oracle list in the ADI template.
SECURITY_DEPOSIT = "SECURITY_DEPOSIT"
ADJUSTMENT_RULES = (
    (("LIQUIDATED", "LIQUIDITY DAMAGE"), "- LD"),
    (("GST",), "GST - TDS - IGST"),
    (("INCOME TAX", "TDS"), "IT TDS"),
    (("DEPOSIT SECURITY", "SECURITY DEPOSIT", "EQUIVALENT TO SD"), SECURITY_DEPOSIT),
    (("PENALTY", "GENERAL DAMAGES"), "General Damages"),
    (("OTHER CHARGES", "LEGAL"), "IR other Deductions"),
)
# security deposit: CS for the TSG segment, OE for OE
SECURITY_BY_SEGMENT = {"TSG": "Security Deposit - CS", "OE": "Security Deposit - OE"}

_BLANK = {"", "-", "----", "NAN", "NONE"}

ISSUE_TEXT = {
    "NO_UNIT": "No operating unit (PartyCode)",
    "NO_CUSTOMER": "Zone not in the zone directory",
    "NO_RECEIPT_METHOD": "No receipt method for the unit",
    "NO_INVOICE": "No bill number",
    "NO_ADJUSTMENT_TYPE": "Recovery head has no Oracle adjustment type",
    "DEDUCTION_WITHOUT_DETAIL": "Bill deduction not covered by recovery lines",
}


def _text(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.upper() in _BLANK else s


def adi_date(d) -> Optional[str]:
    """The ADI's DD-Mon-YYYY text (10-Oct-2025)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%d-%b-%Y")


def adjustment_suffix(head) -> Optional[str]:
    h = (_text(head) or "").upper()
    for keywords, suffix in ADJUSTMENT_RULES:
        if any(k in h for k in keywords):
            return suffix
    return None


def adjustment_type(head, unit: Optional[str], segment: Optional[str]) -> Optional[str]:
    prefix = UNIT_PREFIX.get(unit or "")
    suffix = adjustment_suffix(head)
    if suffix == SECURITY_DEPOSIT:
        suffix = SECURITY_BY_SEGMENT.get((segment or "").upper())
    if not prefix or not suffix:
        return None
    return f"{prefix} {suffix}"


def customer_name(zone_table: dict, zone) -> Optional[str]:
    info = db_zones.resolve(zone_table, _text(zone))
    if not info or not info.get("name"):
        return None
    return f"{info['code']}-{str(info['name']).upper()}"


def _confirmed_matches(session, customer_pk: int, date_from: date, date_to: date):
    return list(session.execute(
        select(MatchLedger, GoldBankTxn)
        .join(GoldBankTxn, GoldBankTxn.id == MatchLedger.gold_bank_txn_id)
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status == "LOCKED",
               GoldBankTxn.value_date >= date_from,
               GoldBankTxn.value_date <= date_to)
        .order_by(GoldBankTxn.value_date, MatchLedger.seq)).all())


def build_records(session, customer_pk: int, date_from: date, date_to: date,
                  units: Optional[Iterable[str]] = None,
                  gl_date: Optional[date] = None) -> List[dict]:
    """One dict per WebADI record: the template columns plus display-only
    keys the UI uses (unit, match, bill_status, recovery_head, issues)."""
    rule_row = session.execute(
        select(MatchRuleSetRow).where(MatchRuleSetRow.customer_id == customer_pk,
                                      MatchRuleSetRow.is_default.is_(True))
    ).scalar_one_or_none()
    zone_table = db_zones.lookup(db_zones.effective_directory(rule_row))
    wanted = set(units) if units else None

    pairs = _confirmed_matches(session, customer_pk, date_from, date_to)
    match_ids = [m.id for m, _t in pairs]
    links = list(session.execute(
        select(MatchLedgerBill.match_ledger_id, GoldBill)
        .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
        .where(MatchLedgerBill.match_ledger_id.in_(match_ids),
               MatchLedgerBill.role == "picked")).all()) if match_ids else []
    bills_by_match: Dict[str, list] = {}
    for mid, bill in links:
        bills_by_match.setdefault(mid, []).append(bill)
    bill_ids = [b.id for _m, b in links]
    lines_by_bill: Dict[str, list] = {}
    if bill_ids:
        for rec in session.execute(
                select(GoldRecovery).where(GoldRecovery.gold_bill_id.in_(bill_ids))
                .order_by(GoldRecovery.bronze_file_id, GoldRecovery.row_seq)).scalars():
            lines_by_bill.setdefault(rec.gold_bill_id, []).append(rec)

    out: List[dict] = []
    for m, txn in pairs:
        label = f"M-{m.seq}" if m.seq is not None else m.id[:8]
        bills = sorted(bills_by_match.get(m.id, []),
                       key=lambda b: (str(b.bill_number or ""), str(b.submission_ref or "")))
        for bill in bills:
            unit = bill.operating_unit if bill.operating_unit in UNITS else None
            if wanted is not None and (unit or UNASSIGNED) not in wanted:
                continue
            prefix = UNIT_PREFIX.get(unit or "")
            zone = _text(bill.zone)
            info = db_zones.resolve(zone_table, zone)
            segment = info.get("segment") if info else None
            invoice = _text(bill.bill_number)
            base = {
                "Upl": "O",
                "Operating Unit ID Selected": OU_NAMES.get(unit or ""),
                "Customer Name": customer_name(zone_table, zone),
                "Receipt Method": RECEIPT_METHOD.format(prefix=prefix) if prefix else None,
                "Receipt Number": _text(txn.bank_ref),
                "Currency": CURRENCY,
                "Receipt Amount": txn.amount,
                "Receipt Date": adi_date(txn.value_date),
                "GL Date": adi_date(gl_date or txn.value_date),
                "Invoice Number": invoice,
                "Receipt Amount Applied": bill.net_payable_amount,
                "Adjustment Amount": None,
                "Factoring Amount": None,
                "Adjustment Type": None,
                "Messages": None,
                # display-only
                "unit": unit or UNASSIGNED,
                "match": label,
                "match_ledger_id": m.id,
                "confidence": m.confidence,
                "zone": zone,
                "submission_ref": bill.submission_ref,
                "recovery_head": None,
            }
            issues = []
            if unit is None:
                issues.append("NO_UNIT")
            if base["Customer Name"] is None:
                issues.append("NO_CUSTOMER")
            if base["Receipt Method"] is None and unit is not None:
                issues.append("NO_RECEIPT_METHOD")
            if invoice is None:
                issues.append("NO_INVOICE")

            lines = lines_by_bill.get(bill.id) or []
            # a deduction the lines do not add up to would reach Oracle as
            # a bill with (part of) its adjustment missing — never silently
            covered = sum(l.recovery_amt or 0.0 for l in lines)
            if abs((bill.deduction_amount or 0.0) - covered) >= 1.0:
                issues.append("DEDUCTION_WITHOUT_DETAIL")
            if not lines:
                out.append({**base, "issues": list(issues)})
                continue
            for line in lines:
                adj_type = adjustment_type(line.recovery_head, unit, segment)
                row_issues = list(issues)
                if adj_type is None:
                    row_issues.append("NO_ADJUSTMENT_TYPE")
                out.append({**base,
                            "Adjustment Amount": line.recovery_amt,
                            "Adjustment Type": adj_type,
                            "recovery_head": _text(line.recovery_head),
                            "issues": row_issues})
    return out


def summarize(records: List[dict]) -> dict:
    receipts = {r["match_ledger_id"] for r in records}
    bills = {(r["match_ledger_id"], r["Invoice Number"], r["submission_ref"])
             for r in records}
    amount = {}
    for r in records:
        amount[r["match_ledger_id"]] = r["Receipt Amount"] or 0.0
    issues: Dict[str, int] = {}
    for r in records:
        for code in r["issues"]:
            issues[code] = issues.get(code, 0) + 1
    by_unit: Dict[str, int] = {}
    for r in records:
        by_unit[r["unit"]] = by_unit.get(r["unit"], 0) + 1
    return {"records": len(records), "receipts": len(receipts), "bills": len(bills),
            "receipt_amount": round(sum(amount.values()), 2),
            "issues": issues, "by_unit": by_unit}


def latest_date(session, customer_pk: int) -> Optional[date]:
    """The newest credit value date with a confirmed match — the page's
    default day."""
    return session.execute(
        select(GoldBankTxn.value_date)
        .join(MatchLedger, MatchLedger.gold_bank_txn_id == GoldBankTxn.id)
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status == "LOCKED")
        .order_by(GoldBankTxn.value_date.desc()).limit(1)).scalar_one_or_none()


def sheets(records: List[dict], created: date) -> List[dict]:
    """Records -> one WebADI sheet per operating unit (the ADI's
    responsibility and OU are per unit), in UNITS order, Unassigned last."""
    out = []
    for unit in (*UNITS, UNASSIGNED):
        recs = [r for r in records if r["unit"] == unit]
        if not recs:
            continue
        prefix = UNIT_PREFIX.get(unit)
        meta = {k: None for k in WEBADI_META_KEYS}
        meta.update({
            "RESPONSIBILITY": RESPONSIBILITY.format(prefix=prefix) if prefix else None,
            "OU_NAME_RESP": OU_NAMES.get(unit),
            "CREATION_DATE": adi_date(created),
        })
        out.append({"name": unit, "meta": meta, "records": recs})
    return out
