"""
Daily Collection settings and reference lookups — per-customer reference
data, like db/zones.py, read only by the Daily Collection export.

Stored on match_rule_sets.collection_config as
  {"fiscal_calendar": {"pattern": [...12 week counts], "year_starts": {"2027": "2027-01-04"}},
   "category_master": [{region, sales_rep, order_type, category, subcategory}, ...],
   "branch_codes": {subcategory: BRANCH},
   "recipients": ["someone@example.com", ...],
   "segments": ["TSG"]}
Any key missing or NULL falls back to the default below, so a customer
that never saved sees the list supplied on 2026-09-30.

Category master: an AR invoice's Sales Rep (+ Sales Order Type when a
rep appears more than once) -> Category / Subcategory; BRANCH is the
code the team's sheet shows for a Subcategory. The AR statement's own
Category / Sub category columns (typed in by Finance) are the fallback
for a rep the master does not list.
"""

import re
from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select

from recon.fiscal import FiscalCalendar, FiscalCalendarError, to_config

from .models import GoldArInvoice

# REGION / Sales Rep / Type (order type) / Category / Subcategory, as
# supplied 2026-09-30. "WABRKT" in the Rohtak spares row is the source's
# own spelling; the rep alone already identifies that row.
DEFAULT_CATEGORY_MASTER = [
    {"region": "HOSUR", "sales_rep": "WABHSR CS -Spares Sales", "order_type": "WABHSR CS -Spares Sales", "category": "S&T", "subcategory": "CS-Others"},
    {"region": "HOSUR", "sales_rep": "WABHSR OE", "order_type": "Standard Domestic-YHR", "category": "OE", "subcategory": "OE"},
    {"region": "HOSUR", "sales_rep": "WABHSR CS -Service Agreement", "order_type": "WABHSR CS -Retrofit", "category": "CS", "subcategory": "Retro"},
    {"region": "HOSUR", "sales_rep": "WABHSR CS -Invoice Only Dom", "order_type": "WABHSR CS -Retrofit", "category": "CS", "subcategory": "Retro"},
    {"region": "ROHTAK", "sales_rep": "WABRTK CS -Spares Sales", "order_type": "WABRKT CS -Spares Sales", "category": "S&T", "subcategory": "CS-Others"},
    {"region": "ROHTAK", "sales_rep": "WABRTK CS -Service Agreement", "order_type": "WABRTK CS -Retrofit", "category": "CS", "subcategory": "Retro"},
    {"region": "ROHTAK", "sales_rep": "WABRTK OE", "order_type": "Standard Domestic-YRK", "category": "OE", "subcategory": "OE"},
    {"region": "ROHTAK", "sales_rep": "WABRTK CS -Invoice Only Dom", "order_type": "WABRTK CS -Spares Sales", "category": "S&T", "subcategory": "CS"},
    {"region": "ROHTAK(Brakes)", "sales_rep": "WABINF Break Pad", "order_type": "Standard Domestic WABINF - YRF", "category": "Rohtak Friction", "subcategory": "Friction"},
    {"region": "ROHTAK(Brakes)", "sales_rep": "WABINF Break Block KC", "order_type": "Standard Domestic WABINF - YRF", "category": "Rohtak Friction", "subcategory": "Friction"},
    {"region": "ROHTAK(Brakes)", "sales_rep": "WABINF Break Block (L-FREIGHT)", "order_type": "Standard Domestic WABINF - YRF", "category": "Rohtak Friction", "subcategory": "Friction"},
    {"region": "ROHTAK(Brakes)", "sales_rep": "WABINF Break Block KF", "order_type": "Standard Domestic WABINF - YRF", "category": "Rohtak Friction", "subcategory": "Friction"},
    {"region": "ROHTAK(Brakes)", "sales_rep": "WABINF Break Block (L-Coach)", "order_type": "Standard Domestic WABINF - YRF", "category": "Rohtak Friction", "subcategory": "Friction"},
]

# Subcategory -> the sheet's BRANCH code, from the Mapping sheet of the
# team's Daily Collection file. AMC / METRO / EMD / OE have no rule yet
# (left for later on 2026-09-30) and stay blank.
DEFAULT_BRANCH_CODES = {"CS": "ST", "CS-Others": "ST", "Retro": "RETRO",
                        "Friction": "FR"}

# Zone-directory segments the sheet covers. The team's own sheet holds
# TSG railways only: on 31 Aug-22 Sep 2026 every credit it lists is TSG
# and every OE (production unit: ICF, CLW, RCF, MCF) credit is absent.
DEFAULT_SEGMENTS = ["TSG"]

MASTER_FIELDS = ("region", "sales_rep", "order_type", "category", "subcategory")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CollectionConfigError(ValueError):
    pass


def _k(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()


def effective(rule_row) -> dict:
    stored = (getattr(rule_row, "collection_config", None) or {}) if rule_row is not None else {}
    cal = stored.get("fiscal_calendar") or {}
    return {
        "fiscal_calendar": to_config(FiscalCalendar.from_config(cal)),
        "category_master": stored.get("category_master") or DEFAULT_CATEGORY_MASTER,
        "branch_codes": stored.get("branch_codes") or DEFAULT_BRANCH_CODES,
        "recipients": list(stored.get("recipients") or []),
        "segments": list(stored.get("segments") or DEFAULT_SEGMENTS),
    }


def normalize(body: dict) -> dict:
    """API body -> stored config (validated). A section equal to its
    default is stored as NULL, so the default can still change under it."""
    out: dict = {}
    try:
        cal = FiscalCalendar.from_config(body.get("fiscal_calendar"))
    except (FiscalCalendarError, ValueError, TypeError) as e:
        raise CollectionConfigError(f"fiscal calendar: {e}") from e
    cal_cfg = to_config(cal)
    out["fiscal_calendar"] = None if cal_cfg == to_config(FiscalCalendar()) else cal_cfg

    master = []
    seen = set()
    for i, row in enumerate(body.get("category_master") or []):
        clean = {f: (str(row.get(f) or "").strip() or None) for f in MASTER_FIELDS}
        if not clean["sales_rep"]:
            raise CollectionConfigError(f"category row {i + 1}: sales rep is required")
        if not clean["subcategory"]:
            raise CollectionConfigError(f"{clean['sales_rep']}: subcategory is required")
        key = (_k(clean["sales_rep"]), _k(clean["order_type"]))
        if key in seen:
            raise CollectionConfigError(
                f"{clean['sales_rep']} / {clean['order_type'] or '-'} appears twice")
        seen.add(key)
        master.append(clean)
    out["category_master"] = None if master == DEFAULT_CATEGORY_MASTER else master

    branches = {}
    for sub, code in (body.get("branch_codes") or {}).items():
        sub, code = str(sub).strip(), str(code or "").strip().upper()
        if sub and code:
            branches[sub] = code
    out["branch_codes"] = None if branches == DEFAULT_BRANCH_CODES else branches

    recipients = []
    for r in body.get("recipients") or []:
        r = str(r).strip()
        if not r:
            continue
        if not _EMAIL_RE.match(r):
            raise CollectionConfigError(f"'{r}' is not an email address")
        if r.lower() not in {x.lower() for x in recipients}:
            recipients.append(r)
    out["recipients"] = recipients or None

    segments = []
    for seg in body.get("segments") or []:
        seg = str(seg).strip().upper()
        if seg and seg not in segments:
            segments.append(seg)
    if body.get("segments") is not None and not segments:
        raise CollectionConfigError("at least one segment is required")
    out["segments"] = None if not segments or segments == DEFAULT_SEGMENTS else segments
    return {k: v for k, v in out.items() if v is not None} or None


def calendar(cfg: dict) -> FiscalCalendar:
    return FiscalCalendar.from_config(cfg.get("fiscal_calendar"))


class CategoryLookup:
    def __init__(self, master: List[dict], branch_codes: Dict[str, str]):
        self.by_rep: Dict[str, List[dict]] = defaultdict(list)
        for row in master:
            self.by_rep[_k(row.get("sales_rep"))].append(row)
        self.branch = {_k(s): c for s, c in branch_codes.items()}

    def category(self, sales_rep, order_type) -> Optional[dict]:
        rows = self.by_rep.get(_k(sales_rep)) or []
        if len(rows) > 1:
            typed = [r for r in rows if _k(r.get("order_type")) == _k(order_type)]
            rows = typed or rows
        subs = {(r.get("category"), r.get("subcategory")) for r in rows}
        if len(subs) != 1:
            return None
        category, subcategory = subs.pop()
        return {"category": category, "subcategory": subcategory}

    def branch_code(self, subcategory) -> Optional[str]:
        return self.branch.get(_k(subcategory)) if subcategory else None


def ar_lookup(session, customer_pk: int, invoice_numbers: Iterable[str]) -> Dict[str, list]:
    """invoice_number -> [one aggregated dict per AR statement that lists
    it], oldest statement first. An invoice spread over several lines of
    one statement is summed (open + original amount), earliest due date."""
    wanted = sorted({str(i).strip() for i in invoice_numbers if i})
    per: Dict[tuple, dict] = {}
    for chunk in range(0, len(wanted), 500):
        for r in session.execute(
                select(GoldArInvoice)
                .where(GoldArInvoice.customer_id == customer_pk,
                       GoldArInvoice.invoice_number.in_(wanted[chunk:chunk + 500]))
                .order_by(GoldArInvoice.statement_date, GoldArInvoice.bronze_file_id,
                          GoldArInvoice.row_seq)).scalars():
            key = (r.invoice_number, r.statement_date, r.bronze_file_id)
            agg = per.get(key)
            if agg is None:
                per[key] = {"statement_date": r.statement_date,
                            "bronze_file_id": r.bronze_file_id,
                            "due_date": r.due_date, "sales_rep": r.sales_rep,
                            "sales_order_type": r.sales_order_type,
                            "category": r.category, "subcategory": r.subcategory,
                            "functional_amount": r.functional_amount,
                            "functional_amount_open": r.functional_amount_open}
                continue
            for f in ("functional_amount", "functional_amount_open"):
                if getattr(r, f) is not None:
                    agg[f] = (agg[f] or 0.0) + getattr(r, f)
            if r.due_date and (agg["due_date"] is None or r.due_date < agg["due_date"]):
                agg["due_date"] = r.due_date
    out: Dict[str, list] = defaultdict(list)
    for (inv, _d, _f), agg in per.items():
        out[inv].append(agg)
    for inv in out:
        out[inv].sort(key=lambda a: (a["statement_date"] or date.min, a["bronze_file_id"]))
    return out


def pick_statement(snapshots: list, on: date) -> Optional[dict]:
    """The AR snapshot to read for a receipt on `on`: the newest statement
    dated on or before it (the position Finance knew that day), else the
    oldest one after it."""
    if not snapshots:
        return None
    before = [s for s in snapshots if s["statement_date"] and s["statement_date"] <= on]
    if before:
        return before[-1]
    return snapshots[0]
