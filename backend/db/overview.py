"""
Command Center overview + AR reconciliation + audit feed: cheap read-only
aggregates for one customer, all via SQL — no gold frames are loaded.
Read-only (no audit events).
"""

from datetime import date

from datetime import datetime, timedelta
from sqlalchemy import and_, exists, false, not_, or_, true
from sqlalchemy import func, select

from . import incremental
from .models import (AuditLog, BronzeFile, CreditSource, ExceptionLedger, GoldBankTxn,
                     GoldBill, GoldLineageDoc, GoldRecovery, MatchLedger,
                     MatchLedgerBill, MatchRuleSetRow, Run)


def _audit_entity_context(session, rows) -> dict:
    """Read-time display enrichment for audit entities, batched per type
    (never N+1). Storage stays minimal per the logging taxonomy; joining
    the human-facing identifiers (M-{seq}, filenames — ids, no amounts)
    for DISPLAY is the same contract /api/ledger already serves."""
    by_type: dict = {}
    for r in rows:
        if r.entity_type and r.entity_id is not None:
            by_type.setdefault(r.entity_type, set()).add(str(r.entity_id))
    out: dict = {}

    match_ids = by_type.get("match_ledger", set())
    if match_ids:
        matches = session.execute(
            select(MatchLedger).where(MatchLedger.id.in_(match_ids))).scalars()
        picked = dict(session.execute(
            select(MatchLedgerBill.match_ledger_id, GoldBill.bill_number)
            .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
            .where(MatchLedgerBill.match_ledger_id.in_(match_ids),
                   MatchLedgerBill.role == "picked")).all())
        for m in matches:
            label = f"M-{m.seq}" if m.seq is not None else m.id[:8]
            out[("match_ledger", m.id)] = {
                "label": label,
                "context": {"match": label, "status": m.status,
                            "confidence": m.confidence,
                            "bill_number": picked.get(m.id)},
            }

    file_ids = by_type.get("bronze_file", set())
    if file_ids:
        ids = [int(i) for i in file_ids if str(i).isdigit()]
        for b in session.execute(
                select(BronzeFile).where(BronzeFile.id.in_(ids))).scalars():
            out[("bronze_file", str(b.id))] = {
                "label": b.original_name,
                "context": {"file": b.original_name,
                            "source_type": b.source_type},
            }

    for rid in by_type.get("match_rule_set", set()):
        out[("match_rule_set", rid)] = {"label": "Matching config", "context": None}

    txn_ids = set(by_type.get("gold_bank_txn", set()))
    exc_ids = by_type.get("exception_ledger", set())
    excs = {}
    if exc_ids:
        excs = {e.id: e for e in session.execute(
            select(ExceptionLedger).where(ExceptionLedger.id.in_(exc_ids))).scalars()}
        txn_ids |= {e.gold_bank_txn_id for e in excs.values() if e.gold_bank_txn_id}
    txns = {t.id: t for t in session.execute(
        select(GoldBankTxn).where(GoldBankTxn.id.in_(txn_ids))).scalars()} \
        if txn_ids else {}
    bill_ids = ({e.gold_bill_id for e in excs.values() if e.gold_bill_id}
                | set(by_type.get("gold_bill", set())))
    bills = {b.id: b for b in session.execute(
        select(GoldBill).where(GoldBill.id.in_(bill_ids))).scalars()} \
        if bill_ids else {}

    def bill_label(b):
        # a works-contract bill has no number ('-'): name it by its CO6
        if b is None:
            return "Bill"
        num = (b.bill_number or "").strip()
        if num and num != "-":
            return f"Bill · {num}"
        return f"Bill · CO6 {b.submission_ref}" if b.submission_ref else "Bill"

    def credit_label(t):
        return f"Credit · {t.bank_ref}" if t is not None and t.bank_ref else "Credit"

    for tid in by_type.get("gold_bank_txn", set()):
        t = txns.get(tid)
        out[("gold_bank_txn", tid)] = {
            "label": credit_label(t),
            "context": {"bank_ref": t.bank_ref,
                        "value_date": t.value_date.isoformat() if t.value_date else None}
            if t is not None else None,
        }
    for eid, e in excs.items():
        if e.gold_bank_txn_id:
            t = txns.get(e.gold_bank_txn_id)
            label = credit_label(t)
            ctx = {"bank_ref": t.bank_ref if t else None, "exception": "Credit with no bill"}
        else:
            b = bills.get(e.gold_bill_id)
            num = b.bill_number if b is not None else None
            label = bill_label(b)
            ctx = {"bill_number": num, "exception": "Bill with no credit"}
        out[("exception_ledger", eid)] = {"label": label, "context": ctx}

    for bid in by_type.get("gold_bill", set()):
        b = bills.get(bid)
        num = b.bill_number if b is not None else None
        out[("gold_bill", bid)] = {"label": bill_label(b),
                                   "context": {"bill_number": num} if num else None}
    for rid in by_type.get("run", set()):
        out[("run", rid)] = {"label": f"Run · {rid[:8]}", "context": None}
    for cid in by_type.get("customer", set()):
        out[("customer", cid)] = {"label": "Customer", "context": None}
    return out


def audit_events(session, customer_pk: int, limit: int = 500) -> list:
    """Newest-first audit_log rows for one customer — the Audit trail
    view's feed. details are safe to serve verbatim: the logging taxonomy
    keeps them to counts/ids/field NAMES, never row content. Each event
    additionally carries a display `entity_label`/`context` resolved at
    read time (see _audit_entity_context)."""
    rows = list(session.execute(
        select(AuditLog)
        .where(AuditLog.customer_id == customer_pk)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)).scalars())
    enrich = _audit_entity_context(session, rows)
    names = incremental.user_names(session, {r.actor_user_id for r in rows})
    out = []
    for r in rows:
        e = enrich.get((r.entity_type, str(r.entity_id))) if r.entity_type else None
        # a non-entity config event still names what it touched
        if e is None and r.entity_type == "source_config":
            e = {"label": "Source setup", "context": None}
        out.append({
            "id": r.id,
            "event_type": r.event_type,
            "severity": r.severity,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "entity_label": e["label"] if e else None,
            "context": e["context"] if e else None,
            "run_id": r.run_id,
            "details": r.details,
            "created_at": r.created_at.isoformat(),
            # who: the signed-in user behind the event, None = the system
            # (or an event older than 2026-09-23, before users were recorded)
            "actor_user_id": r.actor_user_id,
            "actor": names.get(r.actor_user_id),
        })
    return out


def _count(session, model, *where) -> int:
    return session.execute(
        select(func.count()).select_from(model).where(*where)).scalar() or 0


# the operating-unit token for what has NO unit: unmatched bank credits
# (a credit only inherits a unit through the bill it settles) and bills
# whose PartyCode named no known unit. Sent by the UI as a unit value so
# hiding those rows is a visible choice, never a silent drop.
UNASSIGNED_UNIT = "UNASSIGNED"


def operating_units(session, customer_pk: int) -> list:
    """Distinct non-null gold.bills.operating_unit values with bill counts —
    per-customer data (derived from the IREPS PartyCode), not schema."""
    return [{"unit": u, "bills": n} for u, n in session.execute(
        select(GoldBill.operating_unit, func.count())
        .where(GoldBill.customer_id == customer_pk,
               GoldBill.operating_unit.isnot(None))
        .group_by(GoldBill.operating_unit)
        .order_by(GoldBill.operating_unit))]


def _date_bounds(col, date_from, date_to, is_datetime=False):
    """[from, to] inclusive on a Date column; a DateTime column gets the
    half-open [from 00:00, to+1day 00:00) so the whole last day counts."""
    cl = []
    if date_from:
        cl.append(col >= (datetime.combine(date_from, datetime.min.time())
                          if is_datetime else date_from))
    if date_to:
        cl.append(col < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
                  if is_datetime else col <= date_to)
    return cl


UNRECOGNISED = "UNRECOGNISED_RECEIPT"
# credit_sources.source values (an analyst's decision, db/models.CreditSource)
IREPS = incremental.IREPS
NON_IREPS = incremental.NON_IREPS
# exception_ledger.resolved_by for an approved non-IREPS receipt — a
# classification, not a reconciliation: never counted as "resolved"
RESOLVED_NON_IREPS = incremental.RESOLVED_NON_IREPS


def decided_clause(source: str):
    """WHERE clause (needs GoldBankTxn joined): an analyst decided this
    credit's source is `source` (IREPS | NON_IREPS)."""
    return exists(
        select(CreditSource.id)
        .where(CreditSource.gold_bank_txn_id == GoldBankTxn.id,
               CreditSource.source == source)
        .correlate(GoldBankTxn))


def _engine_unrecognised():
    """The engine's own reading, before any analyst decision: the stored
    gap code, or — for rows written before the code was stored — a blank
    zone_guess, which is exactly what makes the default mapping assign it."""
    return or_(ExceptionLedger.gap_type == UNRECOGNISED,
               and_(ExceptionLedger.gap_type.is_(None),
                    or_(GoldBankTxn.zone_guess.is_(None),
                        GoldBankTxn.zone_guess == "")))


def unrecognised_clause():
    """WHERE clause (needs GoldBankTxn joined) picking the BANK_ONLY
    exceptions that are unrecognised (non-IREPS) receipts. An analyst's
    decision wins either way (credit_sources); without one, the engine's
    reading (_engine_unrecognised). An unrecognised receipt carries no
    match signal, is never offered to the matcher (db/incremental
    build_pool), and is therefore kept OUT of every match-rate
    denominator."""
    return and_(not_(decided_clause(IREPS)),
                or_(decided_clause(NON_IREPS), _engine_unrecognised()))


def non_ireps_clause():
    """The exception rows that ARE the customer's non-IREPS receipts:
    open ones, plus the ones an analyst approved (RESOLVED as
    USER_NON_IREPS). Needs GoldBankTxn joined. "Other receipts" counts
    these, so approving a receipt never moves it into the rate."""
    return and_(unrecognised_clause(),
                or_(ExceptionLedger.status == "OPEN",
                    ExceptionLedger.resolved_by == RESOLVED_NON_IREPS))


def not_non_ireps_resolution():
    """Resolved exceptions minus the non-IREPS approvals — a
    classification is not a reconciliation."""
    return or_(ExceptionLedger.resolved_by.is_(None),
               ExceptionLedger.resolved_by != RESOLVED_NON_IREPS)


# Source-system statuses meaning "the bill is in flight but payment has
# not been advised yet". The matcher only ever considers PAID-status
# bills, so a credit whose only same-amount bill sits here could not have
# matched: the status lags the money, which is not a reconciliation
# failure. Such credits are reported on their own line and kept OUT of
# the match-rate denominator — like an unrecognised receipt, for a
# different reason. RETURNED is deliberately absent: money against a
# RETURNED bill IS surprising and must stay visible in the rate.
# CO7 DONE joined 2026-09-22 when it stopped counting as paid: a credit
# whose only same-amount bill has a payment order but no PAYMENT MADE yet
# is the same status lag, not a matching failure.
IN_FLIGHT_STATUSES = ("PASSED", "REGISTERED", "CO7 DONE")
# RETURNED is deliberately NOT in flight: money against a bill IREPS
# rejected is surprising and stays in the match rate. It gets its own
# read-time reading instead (BILL_RETURNED below), because "no bill in
# the export" would send an analyst looking for the wrong thing.
RETURNED_STATUSES = ("RETURNED",)
# IREPS rounds Net Amt to whole rupees, so the amount join carries ±1.
IN_FLIGHT_AMOUNT_SLACK = 1.0
# How long "the status lags the money" stays a fair excuse, counted from
# the CREDIT's value date against the data's own last day. Per-customer
# (MatchRuleSet.awaiting_status_days); this is the dataclass default.
AWAITING_STATUS_DAYS = 7


def same_amount_bill_clause(customer_pk: int, statuses,
                            slack: float = IN_FLIGHT_AMOUNT_SLACK):
    """WHERE clause (needs GoldBankTxn joined) picking open BANK_ONLY
    credits whose amount matches a bill in one of `statuses`.

    The date guard keeps coincidences out: the bill must have been
    submitted on or before the credit's value date, because money cannot
    arrive for a bill that does not exist yet. A bill carrying no date at
    all is allowed through — missing data is not evidence against it.
    """
    bill_date = func.coalesce(GoldBill.submission_date, GoldBill.bill_date)
    return exists(
        select(GoldBill.id)
        .where(GoldBill.customer_id == customer_pk,
               func.upper(func.trim(GoldBill.bill_status)).in_(
                   [s.upper() for s in statuses]),
               GoldBill.net_payable_amount.isnot(None),
               func.abs(GoldBill.net_payable_amount - GoldBankTxn.amount) <= slack,
               or_(bill_date.is_(None),
                   bill_date <= GoldBankTxn.value_date))
        .correlate(GoldBankTxn))


def awaiting_status_clause(customer_pk: int,
                           statuses=IN_FLIGHT_STATUSES,
                           slack: float = IN_FLIGHT_AMOUNT_SLACK):
    """The same clause for the IN-FLIGHT statuses — the credit could not
    have matched yet, so it is excused from the match rate."""
    return same_amount_bill_clause(customer_pk, statuses, slack)


# How long a credit may be excused as "the bill export has not arrived
# yet" before it counts as unmatched again. Exports land daily, so the
# normal wait is one day and the longest observed gap is a weekend; a
# feed further behind than this is a process problem, not a timing
# quirk, and must not hide in a quiet bucket.
AWAITING_BILL_DATA_CAP_DAYS = 5


def _as_date(v):
    """SQLite can hand back an ISO string for max() on a Date column."""
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return getattr(v, "date", lambda: v)() if isinstance(v, datetime) else v


def coverage(session, customer_pk: int):
    """How far the SOURCE data reaches, ignoring any date filter:

      bills_covered_through  latest payment advice in gold. An export
                             dated D carries advices up to D-1, so a
                             credit valued after this date has no bill
                             data to match against YET.
      data_as_of             latest credit value date — the system's own
                             "today", so a historical replay ages the
                             same way a live feed does.
    """
    bills_through = session.execute(
        select(func.max(GoldBill.payment_advice_date))
        .where(GoldBill.customer_id == customer_pk)).scalar()
    as_of = session.execute(
        select(func.max(GoldBankTxn.value_date))
        .where(GoldBankTxn.customer_id == customer_pk,
               GoldBankTxn.used_in_recon.is_(True))).scalar()
    return _as_date(bills_through), _as_date(as_of)


def awaiting_bill_data_clause(bills_through, as_of,
                              cap_days: int = AWAITING_BILL_DATA_CAP_DAYS):
    """WHERE clause (needs GoldBankTxn joined) picking open BANK_ONLY
    credits that could not have matched because the bill export covering
    their advice has not been ingested yet — value date past the bill
    coverage, and not yet stale beyond the cap.

    With no bills or no credits at all there is no coverage to reason
    from, and the rule stays OFF: a customer with an empty bill layer is
    genuinely unreconciled, not waiting."""
    if bills_through is None or as_of is None:
        return false()
    return and_(GoldBankTxn.value_date > bills_through,
                GoldBankTxn.value_date >= as_of - timedelta(days=cap_days))


# The four-way reading of an OPEN BANK_ONLY exception, in the SAME
# priority order the match-rate denominator subtracts the buckets, so a
# credit lands in exactly one of them. Only UNRECOGNISED_RECEIPT and
# SIGNAL_BILL_NOT_FOUND are STORED (exception_ledger.gap_type); the two
# awaiting readings are derived at read time from live gold, which is
# why a caller has to be HANDED them per row — there is no column to
# filter on. Frozen codes: they travel to the UI and back as filters.
AWAITING_STATUS = "AWAITING_STATUS"
AWAITING_BILL_DATA = "AWAITING_BILL_DATA"
# an analyst rejected the non-IREPS reading of a credit with no match
# signal: it is IREPS money whose bill is missing. Read-time too — the
# stored gap code (from the engine) still says UNRECOGNISED_RECEIPT.
MARKED_IREPS = "MARKED_IREPS"
# a bill of this amount exists but was RETURNED — still real work, still
# in the rate, but the analyst should look at the returned bill
BILL_RETURNED = "BILL_RETURNED"
# read-time codes that are NOT a credit-funnel bucket: the credit stays
# RECOGNISED (it counts in the match rate), only its WORDING changes
NOT_A_SCOPE = (MARKED_IREPS, BILL_RETURNED)


def awaiting_window_clause(as_of, days: int):
    """WHERE clause (needs GoldBankTxn joined): the credit is still young
    enough to be excused — its value date is within `days` of the data's
    own last day. With no data at all nothing is excused."""
    if as_of is None:
        return false()
    return GoldBankTxn.value_date >= as_of - timedelta(days=days)


def awaiting_status_days(session, customer_pk: int) -> int:
    """The customer's excuse window, NULL meaning the dataclass default —
    read here rather than threaded through every caller, because these are
    read-time views and the customer's row is the only source that applies."""
    v = session.execute(
        select(MatchRuleSetRow.awaiting_status_days)
        .where(MatchRuleSetRow.customer_id == customer_pk,
               MatchRuleSetRow.is_default.is_(True))).scalar()
    return AWAITING_STATUS_DAYS if v is None else v


def gap_details(session, customer_pk: int) -> dict:
    """``{exception_ledger.id: gap code}`` for the customer's OPEN
    BANK_ONLY exceptions, built from the very clauses the overview
    counts with — so a Command Center row and the queue filter it links
    to select the same credits. Ids absent from the result carry no
    read-time reading and keep their stored ``gap_type``.

    Deliberately unwindowed: the ledger view is whole-customer, and a
    date window here would classify by one scope and count by another.
    """
    bills_through, data_as_of = coverage(session, customer_pk)

    def ids(*extra):
        return session.execute(
            select(ExceptionLedger.id)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   ExceptionLedger.status == "OPEN", *extra)).scalars().all()

    # same order, same not_() guards as the denominator in overview()
    out = {i: UNRECOGNISED for i in ids(unrecognised_clause())}
    # disjoint from the line above (unrecognised_clause excludes it); an
    # awaiting reading below still wins — it says WHY there is no bill
    out.update({i: MARKED_IREPS for i in ids(decided_clause(IREPS),
                                             _engine_unrecognised())})
    # ...and only while the credit is young enough to excuse (the status
    # lag is a timing quirk for a few days, a process problem after that)
    excuse = awaiting_window_clause(data_as_of, awaiting_status_days(session, customer_pk))
    out.update({i: AWAITING_STATUS for i in ids(
        not_(unrecognised_clause()),
        awaiting_status_clause(customer_pk), excuse)})
    out.update({i: AWAITING_BILL_DATA for i in ids(
        not_(unrecognised_clause()),
        not_(and_(awaiting_status_clause(customer_pk), excuse)),
        awaiting_bill_data_clause(bills_through, data_as_of))})
    # last: a same-amount bill exists but IREPS returned it. An in-flight
    # bill (above) is the softer reading and wins when both exist.
    out.update({i: BILL_RETURNED for i in ids(
        not_(unrecognised_clause()),
        not_(and_(awaiting_status_clause(customer_pk), excuse)),
        not_(awaiting_bill_data_clause(bills_through, data_as_of)),
        same_amount_bill_clause(customer_pk, RETURNED_STATUSES))})
    return out


GAP_BILLS_MAX = 5


def gap_bills(session, customer_pk: int, gaps: dict) -> dict:
    """``{exception_ledger.id: [bill, ...]}`` for the OPEN BANK_ONLY rows
    whose read-time reading rests on a same-amount bill — AWAITING_STATUS
    (an in-flight bill) and BILL_RETURNED (a returned one) — so the queue
    can show WHICH bill and its current status, not just that one exists.
    Same predicate as same_amount_bill_clause, joined instead of EXISTS.
    Newest bill first, at most GAP_BILLS_MAX per credit (+ "more")."""
    wanted = {AWAITING_STATUS: IN_FLIGHT_STATUSES, BILL_RETURNED: RETURNED_STATUSES}
    out: dict = {}
    for code, statuses in wanted.items():
        ids = [i for i, c in gaps.items() if c == code]
        if not ids:
            continue
        bill_date = func.coalesce(GoldBill.submission_date, GoldBill.bill_date)
        rows = session.execute(
            select(ExceptionLedger.id, GoldBill.id, GoldBill.bill_number,
                   GoldBill.bill_status, GoldBill.submission_ref, bill_date)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .join(GoldBill, and_(
                GoldBill.customer_id == customer_pk,
                func.upper(func.trim(GoldBill.bill_status)).in_(
                    [st.upper() for st in statuses]),
                GoldBill.net_payable_amount.isnot(None),
                func.abs(GoldBill.net_payable_amount - GoldBankTxn.amount)
                <= IN_FLIGHT_AMOUNT_SLACK,
                or_(bill_date.is_(None), bill_date <= GoldBankTxn.value_date)))
            .where(ExceptionLedger.id.in_(ids))
            .order_by(ExceptionLedger.id, bill_date.desc())).all()
        for eid, bid, num, status, ref, bdate in rows:
            lst = out.setdefault(eid, [])
            if len(lst) < GAP_BILLS_MAX:
                lst.append({"gold_bill_id": bid, "bill_number": num,
                            "bill_status": status, "submission_ref": ref,
                            "bill_date": _as_date(bdate).isoformat() if bdate else None})
    return out


# The credit funnel's per-row reading for the gold bank browse: every
# credit (used_in_recon) lands in exactly one bucket, so a Command Center
# figure (IREPS credits, Recognised) can open the Bank Transactions page
# already filtered to the very credits it counted. Frozen codes — they
# travel to the UI and back as column-filter values.
RECOGNISED = "RECOGNISED"


def credit_scopes(session, customer_pk: int) -> dict:
    """``{gold_bank_txn_id: code}`` for every credit of the customer:
    UNRECOGNISED_RECEIPT (out of scope — "Other receipts"), AWAITING_STATUS
    / AWAITING_BILL_DATA (in scope, not rated), else RECOGNISED (the rate's
    denominator). Debits are absent. Built from gap_details, i.e. the same
    clauses and priority order overview() subtracts with, and unwindowed
    for the same reason."""
    by_exc = gap_details(session, customer_pk)
    out = {tid: RECOGNISED for tid in session.execute(
        select(GoldBankTxn.id)
        .where(GoldBankTxn.customer_id == customer_pk,
               GoldBankTxn.used_in_recon.is_(True))).scalars()}
    if by_exc:
        for eid, tid in session.execute(
                select(ExceptionLedger.id, ExceptionLedger.gold_bank_txn_id)
                .where(ExceptionLedger.id.in_(list(by_exc)))):
            # MARKED_IREPS / BILL_RETURNED are wordings, not buckets:
            # those credits stay RECOGNISED and count in the match rate
            if tid in out and by_exc[eid] not in NOT_A_SCOPE:
                out[tid] = by_exc[eid]
    # approved non-IREPS receipts are RESOLVED, so gap_details (OPEN only)
    # does not see them — they stay "Other receipts", the same rows
    # overview() counts through non_ireps_clause
    for tid in session.execute(
            select(ExceptionLedger.gold_bank_txn_id)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   ExceptionLedger.status == "RESOLVED",
                   non_ireps_clause())).scalars():
        if tid in out:
            out[tid] = UNRECOGNISED
    return out


def credit_sources(session, customer_pk: int, scopes: dict | None = None) -> dict:
    """``{gold_bank_txn_id: IREPS | NON_IREPS}`` for every credit — the
    Bank Transactions page's Source column. A credit the ledger has seen
    reads off credit_scopes (UNRECOGNISED_RECEIPT = NON_IREPS, every other
    bucket is IREPS money), so the column always agrees with the Command
    Center's "Other receipts". A credit no incremental run has seen yet
    (no exception, no live match) has no ledger reading: the analyst's
    decision, else the zone rule the engine applies, answers there."""
    scopes = scopes if scopes is not None else credit_scopes(session, customer_pk)
    seen = set(session.execute(
        select(ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.gold_bank_txn_id.isnot(None))).scalars())
    seen |= set(session.execute(
        select(MatchLedger.gold_bank_txn_id)
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status != "REJECTED")).scalars())
    decided = dict(session.execute(
        select(CreditSource.gold_bank_txn_id, CreditSource.source)
        .where(CreditSource.customer_id == customer_pk)).all())
    unseen = [t for t in scopes if t not in seen and t not in decided]
    zone = dict(session.execute(
        select(GoldBankTxn.id, GoldBankTxn.zone_guess)
        .where(GoldBankTxn.id.in_(unseen))).all()) if unseen else {}
    out = {}
    for tid, scope in scopes.items():
        if tid in seen:
            out[tid] = NON_IREPS if scope == UNRECOGNISED else IREPS
        elif tid in decided:
            out[tid] = decided[tid]
        else:
            out[tid] = IREPS if (zone.get(tid) or "").strip() else NON_IREPS
    return out


def overview(session, customer_pk: int, date_from=None, date_to=None,
             units=None) -> dict:
    """Command Center aggregates. With no filter every query is the one it
    always was (the payload stays identical); with a date window and/or
    an operating-unit set, each figure narrows on the column the table
    in improvement_7sept.md §2.1 names:

      credits / bank_txns    value_date; unit = the settling bill's unit,
                             UNASSIGNED = not matched to any bill
      bills / recoveries     submission_date (fallback bill_date); unit
      lineage_docs           doc_date (no unit)
      match status counts    the credit's value_date (NOT the match's
                             created_at — same credits as the rate);
                             unit via a picked bill
      matched_credits/rate   the credit's value_date + unit (so the rate's
                             numerator and denominator agree)
      exceptions             BILL_ONLY: advice -> order -> submission date
                             + unit; BANK_ONLY: value_date, always
                             UNASSIGNED (a bank credit has no unit)
    `filters_applied` echoes the window and reports how many open
    BANK_ONLY rows fell in the UNASSIGNED bucket."""
    units = list(dict.fromkeys(units)) if units else None
    named = [u for u in units if u != UNASSIGNED_UNIT] if units else []
    include_unassigned = units is None or UNASSIGNED_UNIT in units
    filtered = bool(date_from or date_to or units)

    # --- filter clauses (empty lists when unfiltered -> identical SQL) ---
    txn_cl = _date_bounds(GoldBankTxn.value_date, date_from, date_to)
    bill_cl = _date_bounds(func.coalesce(GoldBill.submission_date, GoldBill.bill_date),
                           date_from, date_to)
    bill_due_cl = _date_bounds(func.coalesce(GoldBill.payment_advice_date,
                                             GoldBill.payment_order_date,
                                             GoldBill.submission_date),
                               date_from, date_to)
    # a match is windowed by its CREDIT's value date, never by when the
    # match row was created: the Settled tile, the rate and the funnel all
    # count credits by value_date, and a run (or an analyst) acting today
    # on last month's money must not move a figure into a different window
    # than the credit it settles
    match_cl = ([MatchLedger.gold_bank_txn_id.in_(
                    select(GoldBankTxn.id).where(GoldBankTxn.customer_id == customer_pk,
                                                 *txn_cl))]
                if txn_cl else [])
    doc_cl = _date_bounds(GoldLineageDoc.doc_date, date_from, date_to)
    unit_bill_cl = []
    unit_txn_cl = []
    unit_match_cl = []
    bank_only_cl = []          # a BANK_ONLY row is always UNASSIGNED
    if units is not None:
        bill_in_units = or_(GoldBill.operating_unit.in_(named) if named else false(),
                            GoldBill.operating_unit.is_(None) if include_unassigned else false())
        unit_bill_cl = [bill_in_units]
        # credits that settle a bill in the units (any non-rejected match)
        credit_in_units = (select(MatchLedger.gold_bank_txn_id)
                           .join(MatchLedgerBill, MatchLedgerBill.match_ledger_id == MatchLedger.id)
                           .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
                           .where(MatchLedger.customer_id == customer_pk,
                                  MatchLedger.status != "REJECTED",
                                  MatchLedgerBill.role == "picked",
                                  bill_in_units))
        matched_any = (select(MatchLedger.gold_bank_txn_id)
                       .where(MatchLedger.customer_id == customer_pk,
                              MatchLedger.status != "REJECTED"))
        unit_txn_cl = [or_(GoldBankTxn.id.in_(credit_in_units),
                           GoldBankTxn.id.not_in(matched_any) if include_unassigned else false())]
        unit_match_cl = [MatchLedger.id.in_(
            select(MatchLedgerBill.match_ledger_id)
            .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
            .where(MatchLedgerBill.role == "picked", bill_in_units))]
        bank_only_cl = [true() if include_unassigned else false()]
    bills_in_scope = (select(GoldBill.id)
                      .where(GoldBill.customer_id == customer_pk, *bill_cl, *unit_bill_cl))

    gold = {
        "bank_txns": _count(session, GoldBankTxn,
                            GoldBankTxn.customer_id == customer_pk,
                            *txn_cl, *unit_txn_cl),
        "credits": _count(session, GoldBankTxn,
                          GoldBankTxn.customer_id == customer_pk,
                          GoldBankTxn.used_in_recon.is_(True),
                          *txn_cl, *unit_txn_cl),
        "bills": _count(session, GoldBill, GoldBill.customer_id == customer_pk,
                        *bill_cl, *unit_bill_cl),
        "recoveries": _count(session, GoldRecovery,
                             GoldRecovery.customer_id == customer_pk,
                             *([GoldRecovery.gold_bill_id.in_(bills_in_scope)]
                               if filtered else [])),
        "lineage_docs": _count(session, GoldLineageDoc,
                               GoldLineageDoc.customer_id == customer_pk,
                               *doc_cl),
    }

    matches = {status: 0 for status in ("OPEN", "LOCKED", "REJECTED")}
    for status, n in session.execute(
            select(MatchLedger.status, func.count())
            .where(MatchLedger.customer_id == customer_pk, *match_cl, *unit_match_cl)
            .group_by(MatchLedger.status)):
        matches[status] = n
    locked_by = {"AUTO_HIGH": 0, "USER": 0}
    for by, n in session.execute(
            select(MatchLedger.locked_by, func.count())
            .where(MatchLedger.customer_id == customer_pk,
                   MatchLedger.status == "LOCKED", *match_cl, *unit_match_cl)
            .group_by(MatchLedger.locked_by)):
        if by in locked_by:
            locked_by[by] = n
    # matches an analyst created by hand (item 3.2): LOCKED by USER like an
    # accepted review match, but told apart by the frozen MANUAL confidence
    manual_matches = _count(session, MatchLedger,
                            MatchLedger.customer_id == customer_pk,
                            MatchLedger.confidence == incremental.MANUAL_CONFIDENCE,
                            MatchLedger.status != "REJECTED",
                            *match_cl, *unit_match_cl)

    # exception rows in scope: BANK_ONLY through its credit, BILL_ONLY
    # through its bill. Unfiltered keeps the plain (join-free) counts.
    def exc_bank(*extra):
        return (select(ExceptionLedger)
                .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
                .where(ExceptionLedger.customer_id == customer_pk,
                       ExceptionLedger.exception_type == "BANK_ONLY",
                       *txn_cl, *bank_only_cl, *extra))

    def exc_bill(*extra):
        return (select(ExceptionLedger)
                .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
                .where(ExceptionLedger.customer_id == customer_pk,
                       ExceptionLedger.exception_type == "BILL_ONLY",
                       *bill_due_cl, *unit_bill_cl, *extra))

    def exc_count(q):
        return session.execute(
            select(func.count()).select_from(q.subquery())).scalar() or 0

    open_exc = {"BANK_ONLY": 0, "BILL_ONLY": 0, "UNRECOGNISED": 0}
    if filtered:
        open_exc["BANK_ONLY"] = exc_count(exc_bank(ExceptionLedger.status == "OPEN"))
        open_exc["BILL_ONLY"] = exc_count(exc_bill(ExceptionLedger.status == "OPEN"))
        resolved = (exc_count(exc_bank(ExceptionLedger.status == "RESOLVED",
                                       not_non_ireps_resolution()))
                    + exc_count(exc_bill(ExceptionLedger.status == "RESOLVED")))
    else:
        for etype, n in session.execute(
                select(ExceptionLedger.exception_type, func.count())
                .where(ExceptionLedger.customer_id == customer_pk,
                       ExceptionLedger.status == "OPEN")
                .group_by(ExceptionLedger.exception_type)):
            open_exc[etype] = n
        resolved = _count(session, ExceptionLedger,
                          ExceptionLedger.customer_id == customer_pk,
                          ExceptionLedger.status == "RESOLVED",
                          not_non_ireps_resolution())
    # the unrecognised subset of open BANK_ONLY (same scope either way —
    # the join-based count equals the plain one when nothing is filtered)
    open_exc["UNRECOGNISED"] = exc_count(
        exc_bank(ExceptionLedger.status == "OPEN", unrecognised_clause()))
    # credits waiting on the SOURCE SYSTEM's status, not on a match: the
    # not_(unrecognised_clause()) keeps them from being subtracted twice
    bills_through, data_as_of = coverage(session, customer_pk)
    excuse = awaiting_window_clause(data_as_of, awaiting_status_days(session, customer_pk))
    awaiting_status_cl = (ExceptionLedger.status == "OPEN",
                          not_(unrecognised_clause()),
                          awaiting_status_clause(customer_pk), excuse)
    awaiting_status_credits = exc_count(exc_bank(*awaiting_status_cl))
    # credits whose bill export has not arrived yet (see coverage()) —
    # the three buckets are mutually exclusive, so the denominator never
    # subtracts the same credit twice
    awaiting_bill_data_cl = (ExceptionLedger.status == "OPEN",
                             not_(unrecognised_clause()),
                             not_(and_(awaiting_status_clause(customer_pk), excuse)),
                             awaiting_bill_data_clause(bills_through, data_as_of))
    awaiting_bill_data_credits = exc_count(exc_bank(*awaiting_bill_data_cl))

    def exc_bank_value(*extra):
        # the money behind exc_bank(*extra): same joins, same filters
        return session.execute(
            exc_bank(*extra).with_only_columns(
                func.coalesce(func.sum(GoldBankTxn.amount), 0.0))).scalar() or 0.0

    # --- in scope (IREPS) vs other receipts ----------------------------
    # The split rides the STORED gap code, which the engine stamps through
    # the CUSTOMER'S field map — never a hardcoded signal column, so a
    # customer matching on something other than zone splits correctly too.
    # Everything that is not an unrecognised receipt is in scope: matched,
    # awaiting, or a genuine gap.
    # open non-IREPS receipts AND the ones an analyst approved as such:
    # approving one classifies it, it never moves it into the rate
    out_of_scope_credits = exc_count(exc_bank(non_ireps_clause()))
    out_of_scope_value = exc_bank_value(non_ireps_clause())
    # the open-only part, which is what open BANK_ONLY has to shed
    open_out_of_scope_value = exc_bank_value(ExceptionLedger.status == "OPEN",
                                             unrecognised_clause())
    credits_value = session.execute(
        select(func.coalesce(func.sum(GoldBankTxn.amount), 0.0))
        .where(GoldBankTxn.customer_id == customer_pk,
               GoldBankTxn.used_in_recon.is_(True),
               *txn_cl, *unit_txn_cl)).scalar() or 0.0
    in_scope_credits = max(0, gold["credits"] - out_of_scope_credits)
    in_scope_value = credits_value - out_of_scope_value

    bank_open_value = session.execute(
        select(func.coalesce(func.sum(GoldBankTxn.amount), 0.0))
        .select_from(ExceptionLedger)
        .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.status == "OPEN",
               ExceptionLedger.exception_type == "BANK_ONLY",
               *txn_cl, *bank_only_cl)).scalar() or 0.0
    bill_open_value = session.execute(
        select(func.coalesce(func.sum(GoldBill.net_payable_amount), 0.0))
        .select_from(ExceptionLedger)
        .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.status == "OPEN",
               ExceptionLedger.exception_type == "BILL_ONLY",
               *bill_due_cl, *unit_bill_cl)).scalar() or 0.0
    # the open work: what needs an analyst. Left out, each counted once
    # elsewhere: other receipts (never matchable -> out_of_scope_credits)
    # and credits awaiting data (IREPS money that could not have matched
    # YET -> awaiting_*_credits, the same credits the rate excuses, so
    # Settled's "of N" and this tile read one definition). Same filters
    # as the figures above, so this is their difference.
    awaiting_count = awaiting_status_credits + awaiting_bill_data_credits
    awaiting_value = (exc_bank_value(*awaiting_status_cl)
                      + exc_bank_value(*awaiting_bill_data_cl))
    in_scope_bank_only = open_exc["BANK_ONLY"] - open_exc["UNRECOGNISED"] - awaiting_count
    open_in_scope = {
        "bank_only": in_scope_bank_only,
        "bill_only": open_exc["BILL_ONLY"],
        "count": in_scope_bank_only + open_exc["BILL_ONLY"],
        "value": bank_open_value - open_out_of_scope_value - awaiting_value + bill_open_value,
        # what the tile names as not counted
        "awaiting": awaiting_count,
        "awaiting_value": awaiting_value,
    }

    def credit_count(*status_cl):
        # numerator on the same column set as the credits denominator
        q = select(func.count(func.distinct(MatchLedger.gold_bank_txn_id)))
        if filtered:
            q = q.join(GoldBankTxn, GoldBankTxn.id == MatchLedger.gold_bank_txn_id)
        return session.execute(
            q.where(MatchLedger.customer_id == customer_pk, *status_cl,
                    *(txn_cl + unit_txn_cl if filtered else []))).scalar() or 0
    # matched = any non-rejected match (incl. those awaiting review);
    # settled = LOCKED only (auto-locked HIGH, accepted by a user, or
    # matched by hand) — the match RATE is the settled share
    matched_credits = credit_count(MatchLedger.status != "REJECTED")
    settled_credits = credit_count(MatchLedger.status == "LOCKED")
    # unrecognised receipts are not matchable, and a credit whose bill is
    # still in flight in the source system could not have matched either:
    # the rate is over the credits that COULD have settled, and both
    # groups are reported as their own counts
    # ONE definition, two readings: the rate's denominator is the IN SCOPE
    # credits minus the two "could not have matched yet" buckets
    unrecognised_credits = out_of_scope_credits
    recognised_credits = max(0, in_scope_credits
                             - awaiting_status_credits
                             - awaiting_bill_data_credits)
    match_rate = ((settled_credits / recognised_credits)
                  if recognised_credits else None)

    # top open exceptions by absolute value (both sides in one list),
    # the same rows as open_in_scope — a large interest credit, sweep or
    # credit awaiting data would otherwise head a list whose tile does
    # not count it
    top: list = []
    for e, t in session.execute(
            select(ExceptionLedger, GoldBankTxn)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BANK_ONLY",
                   not_(unrecognised_clause()),
                   not_(and_(awaiting_status_clause(customer_pk), excuse)),
                   not_(awaiting_bill_data_clause(bills_through, data_as_of)),
                   *txn_cl, *bank_only_cl)):
        top.append({"id": e.id, "exception_type": "BANK_ONLY",
                    "ref": t.bank_ref, "zone": t.zone_guess,
                    "amount": t.amount, "date": str(t.value_date or "")})
    for e, b in session.execute(
            select(ExceptionLedger, GoldBill)
            .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BILL_ONLY",
                   *bill_due_cl, *unit_bill_cl)):
        top.append({"id": e.id, "exception_type": "BILL_ONLY",
                    "ref": b.bill_number, "zone": b.zone,
                    "amount": b.net_payable_amount,
                    "date": str(b.payment_advice_date or b.payment_order_date or "")})
    top.sort(key=lambda x: abs(x["amount"] or 0), reverse=True)

    filters_applied = None
    if filtered:
        # open BANK_ONLY credits in the window have no unit by definition:
        # say so, so a unit filter that hides them is a visible choice
        unassigned_bank_only = exc_count(
            select(ExceptionLedger)
            .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BANK_ONLY", *txn_cl))
        filters_applied = {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
            "operating_units": units,
            "unassigned_included": include_unassigned,
            "bank_only_unassigned": unassigned_bank_only,
        }

    last_run_row = session.execute(
        select(Run).where(Run.customer_id == customer_pk,
                          Run.status == "succeeded")
        .order_by(Run.created_at.desc()).limit(1)).scalar_one_or_none()
    last_run = None
    if last_run_row is not None:
        counts = ((last_run_row.payload or {}).get("meta") or {}).get("counts")
        last_run = {"run_id": last_run_row.id, "mode": last_run_row.mode,
                    "created_at": last_run_row.created_at.isoformat(),
                    "counts": counts}

    last_file = session.execute(
        select(BronzeFile).where(BronzeFile.customer_id == customer_pk)
        .order_by(BronzeFile.uploaded_at.desc()).limit(1)).scalar_one_or_none()
    last_ingestion = ({"at": last_file.uploaded_at.isoformat(),
                       "original_name": last_file.original_name,
                       "source_type": last_file.source_type}
                      if last_file is not None else None)

    return {
        "gold": gold,
        "matches": matches,
        "locked_by": locked_by,
        "manual_matches": manual_matches,
        "open_exceptions": open_exc,
        "resolved_exceptions": resolved,
        "open_value": {"bank_only": bank_open_value,
                       "bill_only": bill_open_value,
                       "total": bank_open_value + bill_open_value},
        "open_in_scope": open_in_scope,
        "matched_credits": matched_credits,
        "settled_credits": settled_credits,
        "unrecognised_credits": unrecognised_credits,
        # the same split as counts + money: in scope = this source's
        # payments (IREPS), out of scope = receipts from anywhere else
        "in_scope_credits": in_scope_credits,
        "in_scope_value": in_scope_value,
        "out_of_scope_credits": out_of_scope_credits,
        "out_of_scope_value": out_of_scope_value,
        "awaiting_status_credits": awaiting_status_credits,
        "awaiting_bill_data_credits": awaiting_bill_data_credits,
        # how far the source data reaches — explains the bucket above
        "bills_covered_through": (bills_through.isoformat()
                                  if bills_through else None),
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "recognised_credits": recognised_credits,
        "match_rate": match_rate,
        "top_exceptions": top[:8],
        "last_run": last_run,
        "last_ingestion": last_ingestion,
        **({"filters_applied": filters_applied} if filtered else {}),
    }


# --- AR reconciliation ---------------------------------------------------

OVERDUE_DAYS = 30

_AR_STATUS_ORDER = {"OVERDUE": 0, "AWAITING": 1, "IN_REVIEW": 2, "SETTLED": 3}


def _due_date(bill: GoldBill):
    """Economic due date: the day the money was advised to the bank,
    falling back to the payment order, then the CO6 submission."""
    return (bill.payment_advice_date or bill.payment_order_date
            or bill.submission_date)


def ar_view(session, customer_pk: int,
            overdue_days: int = OVERDUE_DAYS) -> dict:
    """Bill-centric receivables working set: every bill that is settled
    by the ledger, under review, or still owed (open BILL_ONLY) — plus
    KPIs and an aging analysis. Historic bills outside the recon working
    set are deliberately excluded; every row here is actionable.
    overdue_days is per-customer config (MatchRuleSet.ar_overdue_days).

    Ages run from the DATA's own last day (the latest credit value date),
    not from the wall clock — the Command Center ages the same way. A
    historical load would otherwise show every open bill as overdue purely
    because the calendar has moved on since the statements were cut."""
    _bills_through, data_as_of = coverage(session, customer_pk)
    today = data_as_of or date.today()
    rows: list = []

    # --- settled / in-review: picked bills of non-REJECTED matches ------
    ledger_rows = list(session.execute(
        select(MatchLedger, MatchLedgerBill, GoldBill, GoldBankTxn)
        .join(MatchLedgerBill, MatchLedgerBill.match_ledger_id == MatchLedger.id)
        .join(GoldBill, GoldBill.id == MatchLedgerBill.gold_bill_id)
        .join(GoldBankTxn, GoldBankTxn.id == MatchLedger.gold_bank_txn_id)
        .where(MatchLedger.customer_id == customer_pk,
               MatchLedger.status != "REJECTED",
               MatchLedgerBill.role == "picked")))
    picked_per_match: dict = {}
    for m, _l, _b, _t in ledger_rows:
        picked_per_match[m.id] = picked_per_match.get(m.id, 0) + 1
    received_txns: dict = {}
    names = incremental.user_names(session, {m.decided_by_user_id for m, *_ in ledger_rows})
    for m, _link, bill, txn in ledger_rows:
        # "received" = SETTLED (LOCKED) credits only; a match still under
        # review is listed as IN_REVIEW below but is not money received
        if m.status == "LOCKED":
            received_txns[txn.id] = txn
        net = bill.net_payable_amount
        # a batched credit covers several bills — per-bill variance would
        # mislead, so it is only computed for 1:1 matches
        variance = (round((txn.amount or 0) - (net or 0), 2)
                    if picked_per_match[m.id] == 1 and txn.amount is not None
                    and net is not None else None)
        due = _due_date(bill)
        rows.append({
            "bill_number": bill.bill_number,
            "zone": bill.zone,
            "org_unit": bill.org_unit,
            # gold operating_unit — the AR view's unit filter (item 2.2)
            "org_unit_operating": bill.operating_unit,
            "bill_status": bill.bill_status,
            "gross_amount": bill.gross_amount,
            "net_payable_amount": net,
            "due_date": due.isoformat() if due else None,
            "age_days": None,
            "status": "SETTLED" if m.status == "LOCKED" else "IN_REVIEW",
            "pay": {"bank_ref": txn.bank_ref, "amount": txn.amount,
                    "value_date": (txn.value_date.isoformat()
                                   if txn.value_date else None)},
            "variance": variance,
            "match_ledger_id": m.id,
            "match_seq": m.seq,
            # who settled it: the deciding user, "System" for an automatic
            # HIGH lock, None for a decision older than user tracking
            "decided_by": names.get(m.decided_by_user_id)
            or ("System" if m.locked_by == "AUTO_HIGH" and m.decided_at is None else None),
            "exception_id": None,
            # the run that produced this row — the AR view's run filter
            "run_id": m.run_id,
        })

    # --- outstanding: open BILL_ONLY exceptions -------------------------
    for exc, bill in session.execute(
            select(ExceptionLedger, GoldBill)
            .join(GoldBill, GoldBill.id == ExceptionLedger.gold_bill_id)
            .where(ExceptionLedger.customer_id == customer_pk,
                   ExceptionLedger.status == "OPEN",
                   ExceptionLedger.exception_type == "BILL_ONLY")):
        due = _due_date(bill)
        age = (today - due).days if due else None
        rows.append({
            "bill_number": bill.bill_number,
            "zone": bill.zone,
            "org_unit": bill.org_unit,
            # gold operating_unit — the AR view's unit filter (item 2.2)
            "org_unit_operating": bill.operating_unit,
            "bill_status": bill.bill_status,
            "gross_amount": bill.gross_amount,
            "net_payable_amount": bill.net_payable_amount,
            "due_date": due.isoformat() if due else None,
            "age_days": age,
            "status": ("OVERDUE" if age is not None and age > overdue_days
                       else "AWAITING"),
            "pay": None,
            "variance": (-bill.net_payable_amount
                         if bill.net_payable_amount is not None else None),
            "match_ledger_id": None,
            "match_seq": None,
            "decided_by": None,
            "exception_id": exc.id,
            "run_id": exc.first_seen_run_id,
        })

    rows.sort(key=lambda r: (_AR_STATUS_ORDER[r["status"]],
                             -(abs(r["net_payable_amount"] or 0))))

    # --- KPIs + aging ---------------------------------------------------
    open_rows = [r for r in rows if r["status"] in ("AWAITING", "OVERDUE")]
    overdue_rows = [r for r in rows if r["status"] == "OVERDUE"]
    received_value = sum(t.amount or 0 for t in received_txns.values())
    mtd_value = sum(
        t.amount or 0 for t in received_txns.values()
        if t.value_date and t.value_date.year == today.year
        and t.value_date.month == today.month)
    credits = _count(session, GoldBankTxn,
                     GoldBankTxn.customer_id == customer_pk,
                     GoldBankTxn.used_in_recon.is_(True))
    unrecognised = session.execute(
        select(func.count()).select_from(ExceptionLedger)
        .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.exception_type == "BANK_ONLY",
               ExceptionLedger.status == "OPEN",
               unrecognised_clause())).scalar() or 0
    awaiting_status = session.execute(
        select(func.count()).select_from(ExceptionLedger)
        .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.exception_type == "BANK_ONLY",
               ExceptionLedger.status == "OPEN",
               not_(unrecognised_clause()),
               awaiting_status_clause(customer_pk))).scalar() or 0
    bills_through, data_as_of = coverage(session, customer_pk)
    awaiting_bill_data = session.execute(
        select(func.count()).select_from(ExceptionLedger)
        .join(GoldBankTxn, GoldBankTxn.id == ExceptionLedger.gold_bank_txn_id)
        .where(ExceptionLedger.customer_id == customer_pk,
               ExceptionLedger.exception_type == "BANK_ONLY",
               ExceptionLedger.status == "OPEN",
               not_(unrecognised_clause()),
               not_(awaiting_status_clause(customer_pk)),
               awaiting_bill_data_clause(bills_through, data_as_of))).scalar() or 0
    recognised = max(0, credits - unrecognised - awaiting_status
                     - awaiting_bill_data)
    match_rate = (len(received_txns) / recognised) if recognised else None

    buckets = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90),
               ("90+", 91, None)]
    aging = []
    for label, lo, hi in buckets:
        hit = [r for r in open_rows
               if r["age_days"] is not None and r["age_days"] >= lo
               and (hi is None or r["age_days"] <= hi)]
        aging.append({"bucket": label, "count": len(hit),
                      "value": sum(r["net_payable_amount"] or 0 for r in hit)})
    undated = [r for r in open_rows if r["age_days"] is None]
    aging.append({"bucket": "undated", "count": len(undated),
                  "value": sum(r["net_payable_amount"] or 0 for r in undated)})

    # per-run credit counts so the UI can compute a match rate for a run
    # subset (item 2.2): the denominator of a filtered rate is the credits
    # of the runs in scope, which no row carries
    run_credits = []
    for run in session.execute(
            select(Run).where(Run.customer_id == customer_pk,
                              Run.status == "succeeded")
            .order_by(Run.created_at.desc())).scalars():
        counts = ((run.payload or {}).get("meta") or {}).get("counts") or {}
        run_credits.append({"run_id": run.id,
                            "credits": counts.get("bank_credits"),
                            "unrecognised": counts.get("unrecognised_receipts")})

    return {
        "as_of": today.isoformat(),
        "runs": run_credits,
        "kpis": {
            "outstanding": {"count": len(open_rows),
                            "value": sum(r["net_payable_amount"] or 0
                                         for r in open_rows)},
            "received": {"count": len(received_txns), "value": received_value,
                         "mtd_value": mtd_value},
            "match_rate": match_rate,
            "unrecognised": unrecognised,
            "awaiting_status": awaiting_status,
            "awaiting_bill_data": awaiting_bill_data,
            "overdue": {"count": len(overdue_rows),
                        "value": sum(r["net_payable_amount"] or 0
                                     for r in overdue_rows)},
        },
        "aging": aging,
        "rows": rows,
    }
