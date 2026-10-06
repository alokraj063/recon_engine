"""
Confirmed matches -> the team's "DAILY COLLECTION" workbook.

Composed at DOWNLOAD time from the match ledger + gold + the AR
statement, never stored. Scope: every LOCKED match (auto-HIGH,
analyst-accepted, manual) whose CREDIT's value date falls in the window
— the same money the WebADI export posts — on bills of the segments the
sheet covers (collection config `segments`, default TSG: the team's
sheet has no OE production-unit money). The window always starts on
the first day of a 4-4-5 fiscal month (recon/fiscal.py), because the
sheet is month-to-date: TOTAL COLLECTION runs from the month's first day.

Row shape (the team's layout, one sheet per fiscal month, header row 4):
one row per (credit, bill). SL.NO and EFT AMOUNT sit on a credit's first
row only; DAILY COLLECTION (the day's INVOICE AMOUNT total) and TOTAL
COLLECTION (month to date) on the day's last row.

Column sources (spec 2026-09-30, Mapping sheet of the team's file):
  EFT DATE / Bank Ref / EFT AMOUNT  credit value_date / bank_ref / amount
  Bank                              "HSBC"
  Region / RLY                      zone directory region / code — the
                                    bill's zone, else the credit's
  BILL NO. / Bill Date              bill_number / bill_date
  INVOICE AMOUNT                    the bill's net payable: what the credit
                                    paid against it (sums to EFT AMOUNT)
  Receipt No. / Receipts Date       not required, blank
  BRANCH                            category master -> Subcategory -> code
  Invoice Value                     AR statement: functional amount, open
  OD/NOD                            the AR due date's bucket against the
                                    sheet's fiscal month (see od_nod)
  (unnamed column R)                category master -> Category
  Week                              4-4-5 week within the fiscal month

Anything not derivable stays BLANK and is named in the row's `issues`,
never guessed — like the WebADI export.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select

from . import collection as db_collection
from . import zones as db_zones
from .models import GoldBill, MatchLedgerBill, MatchRuleSetRow
from .webadi_export import _confirmed_matches, _text

BANK = "HSBC"

ISSUE_TEXT = {
    "NO_INVOICE": "No bill number",
    "NO_ZONE": "Zone not in the zone directory",
    "NO_LEGACY_ZONE": "South Coast zone: legacy railway not readable from the AR customer or division",
    "NO_AR_INVOICE": "Invoice not in any AR statement",
    "NO_CATEGORY": "Sales rep not in the category master",
    "NO_BRANCH": "No branch code for the subcategory",
}


def _day(d) -> Optional[date]:
    if d is None:
        return None
    return d.date() if isinstance(d, datetime) else d


def od_nod(cal, value_date: date, due: Optional[date]) -> Optional[str]:
    """The team's OD/NOD label: where the invoice's AR due date falls
    against the fiscal month the credit's sheet belongs to.
      due before the month starts   "OD"
      due inside the month          "OD Sep 26"
      due inside the next month     "OD Oct 26"
      due later                     "NOD"
    Read off the team's Sep 26 sheet (2026-10-05): 240 of the 247 rows we
    can label agree. The 7 misses are Friction invoices due 1 Oct and
    invoices due on the next month's last day (25 Oct), which the team
    reads NOD (or, once, OD Sep 26) — unexplained, so not special-cased. It is a
    due-date bucket, NOT "the credit arrived after the due date" — a credit
    paid early still reads "OD Sep 26" when the invoice falls due in the
    month. No due date -> blank."""
    if due is None:
        return None
    this = cal.period(value_date)
    if due < this.month_start:
        return "OD"
    if due <= this.month_end:
        return f"OD {this.month_label}"
    nxt = cal.period(this.month_end + timedelta(days=1))
    if due <= nxt.month_end:
        return f"OD {nxt.month_label}"
    return "NOD"


def _rule_row(session, customer_pk: int):
    return session.execute(
        select(MatchRuleSetRow).where(MatchRuleSetRow.customer_id == customer_pk,
                                      MatchRuleSetRow.is_default.is_(True))
    ).scalar_one_or_none()


def window(session, customer_pk: int, date_from: Optional[date],
           date_to: Optional[date]):
    """(from, to) with `from` snapped back to its fiscal month's first day.
    Defaults: `to` = the newest credit date with a confirmed match (else
    today), `from` = the start of `to`'s fiscal month."""
    from .webadi_export import latest_date
    cal = db_collection.calendar(db_collection.effective(_rule_row(session, customer_pk)))
    if date_to is None:
        date_to = date_from or latest_date(session, customer_pk) or date.today()
    anchor = date_from or date_to
    return cal.period(anchor).month_start, date_to


def build_rows(session, customer_pk: int, date_from: date, date_to: date) -> List[dict]:
    """One dict per sheet row: the sheet's columns plus display-only keys
    (month, match, match_ledger_id, zone, issues, first/last flags)."""
    rule_row = _rule_row(session, customer_pk)
    cfg = db_collection.effective(rule_row)
    cal = db_collection.calendar(cfg)
    cats = db_collection.CategoryLookup(cfg["category_master"], cfg["branch_codes"])
    zone_table = db_zones.lookup(db_zones.effective_directory(rule_row))
    segments = {seg.upper() for seg in cfg["segments"]}

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
    ar = db_collection.ar_lookup(
        session, customer_pk, [_text(b.bill_number) for _m, b in links])

    out: List[dict] = []
    for m, txn in pairs:
        value_date = _day(txn.value_date)
        period = cal.period(value_date)
        label = f"M-{m.seq}" if m.seq is not None else m.id[:8]
        bills = []
        for bill in sorted(bills_by_match.get(m.id, []),
                           key=lambda b: (str(b.bill_number or ""), str(b.submission_ref or ""))):
            zone = _text(bill.zone) or _text(txn.zone_guess)
            invoice = _text(bill.bill_number)
            snap = db_collection.pick_statement(ar.get(invoice) or [], value_date) if invoice else None
            # the railway Oracle books the bill under (SCoR -> SCR / ECoR)
            info = db_zones.oracle_zone(zone_table, zone,
                                        snap["customer_name"] if snap else None,
                                        bill.org_unit)
            # a bill of a segment the sheet does not cover is left out; an
            # UNKNOWN zone stays in, flagged, rather than vanish unseen
            if info is not None and (info.get("segment") or "").upper() not in segments:
                continue
            bills.append((bill, zone, info, snap))
        for i, (bill, zone, info, snap) in enumerate(bills):
            invoice = _text(bill.bill_number)
            cat = None
            if snap:
                cat = cats.category(snap["sales_rep"], snap["sales_order_type"])
                if cat is None and (snap["subcategory"] or snap["category"]):
                    # Finance's own labels on the statement, for a rep the
                    # master does not list
                    cat = {"category": snap["category"], "subcategory": snap["subcategory"]}
            branch = cats.branch_code(cat["subcategory"]) if cat else None
            due = _day(snap["due_date"]) if snap else None
            issues = []
            if invoice is None:
                issues.append("NO_INVOICE")
            if info is None:
                issues.append("NO_LEGACY_ZONE" if db_zones.needs_legacy_zone(zone_table, zone)
                              else "NO_ZONE")
            if invoice is not None and snap is None:
                issues.append("NO_AR_INVOICE")
            if snap is not None and cat is None:
                issues.append("NO_CATEGORY")
            if cat is not None and branch is None:
                issues.append("NO_BRANCH")
            out.append({
                "SL.NO": None,                   # numbered per sheet below
                "EFT DATE": value_date,
                "Bank": BANK,
                "Bank Ref": _text(txn.bank_ref),
                "Region": info.get("region") if info else None,
                # as the source spells it (IREPS writes NFR, the team's
                # sheet too) — except SCoR, which Oracle books under its
                # legacy railway; the directory supplies the region
                "RLY": (info["code"] if info and info.get("legacy")
                        else zone.upper() if zone else None),
                "EFT AMOUNT": txn.amount if i == 0 else None,
                "BILL NO.": invoice,
                "Bill Date": _day(bill.bill_date),
                "INVOICE AMOUNT": bill.net_payable_amount,
                "Receipt No.": None,
                "Receipts Date": None,
                "DAILY COLLECTION": None,        # filled per day below
                "TOTAL COLLECTION": None,
                "BRANCH": branch,
                "Invoice Value": snap["functional_amount_open"] if snap else None,
                "OD/NOD": od_nod(cal, value_date, due),
                "Category": cat["category"] if cat else None,
                "Week": period.week_label,
                # display-only
                "month": period.month_label,
                "match": label,
                "match_ledger_id": m.id,
                "confidence": m.confidence,
                "zone": zone,
                "subcategory": cat["subcategory"] if cat else None,
                "due_date": due,
                "ar_statement_date": _day(snap["statement_date"]) if snap else None,
                "first_of_credit": i == 0,
                "issues": issues,
            })
    _number(out)
    return out


def _number(rows: List[dict]) -> None:
    """SL.NO per credit and the day / month-to-date totals, per sheet
    (fiscal month), in row order."""
    serial: Dict[str, int] = {}
    total: Dict[str, float] = {}
    for idx, r in enumerate(rows):
        month = r["month"]
        if r["first_of_credit"]:
            serial[month] = serial.get(month, 0) + 1
            r["SL.NO"] = serial[month]
        nxt = rows[idx + 1] if idx + 1 < len(rows) else None
        day_ends = nxt is None or nxt["EFT DATE"] != r["EFT DATE"] or nxt["month"] != month
        if day_ends:
            day_total = 0.0
            j = idx
            while j >= 0 and rows[j]["EFT DATE"] == r["EFT DATE"] and rows[j]["month"] == month:
                day_total += rows[j]["INVOICE AMOUNT"] or 0.0
                j -= 1
            total[month] = total.get(month, 0.0) + day_total
            r["DAILY COLLECTION"] = round(day_total, 2)
            r["TOTAL COLLECTION"] = round(total[month], 2)


def summarize(rows: List[dict]) -> dict:
    credits = {r["match_ledger_id"]: r for r in rows if r["first_of_credit"]}
    issues: Dict[str, int] = {}
    for r in rows:
        for code in r["issues"]:
            issues[code] = issues.get(code, 0) + 1
    by_month: Dict[str, int] = {}
    for r in rows:
        by_month[r["month"]] = by_month.get(r["month"], 0) + 1
    return {"rows": len(rows), "credits": len(credits),
            "eft_amount": round(sum(r["EFT AMOUNT"] or 0.0 for r in credits.values()), 2),
            "collection": round(sum(r["INVOICE AMOUNT"] or 0.0 for r in rows), 2),
            "days": len({r["EFT DATE"] for r in rows}),
            "issues": issues, "by_month": by_month}


def sheets(rows: List[dict]) -> List[dict]:
    """Rows -> one sheet per fiscal month, oldest first."""
    order: List[str] = []
    for r in rows:
        if r["month"] not in order:
            order.append(r["month"])
    return [{"name": m, "rows": [r for r in rows if r["month"] == m]} for m in order]


def file_date(date_to: date) -> str:
    """The team's file name day: "DAILY COLLECTION 21 Aug 26"."""
    return f"{date_to.day} {date_to.strftime('%b %y')}"
