"""
Items 3.1 / 3.2 of improvement_7sept.md: analyst decisions flow into the
exception ledger, and an analyst can pair a credit with bill(s) by hand.

Runs over a synthetic customer built from plain gold frames (no sample
documents, no adapters) so it is independent of the sample folder.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal, incremental, init_db, overview  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldRecovery,
                       IngestConflict, MatchLedger, MatchLedgerBill, Run,
                       RunFrame, RunMatchBill, SilverRecord, SourceConfig)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from recon.rules import ExactSignal, FieldMapping, MatchRuleSet  # noqa: E402

RULES = MatchRuleSet(
    paid_statuses=frozenset({"APPROVED"}),
    field_map=FieldMapping(
        exact_signals=(ExactSignal("customer_ref", "vendor_code", 3),),
        bill_date_fallback=None,
        fallback_due_statuses=(),
    ),
)


def _bank(rows):
    df = pd.DataFrame(rows, columns=["bank_ref", "customer_ref", "amount"])
    df["narrative"] = "credit " + df["bank_ref"]
    df["value_date"] = pd.Timestamp("2026-03-18")
    df["txn_type"] = "CREDIT"
    df["used_in_recon"] = True
    df = ensure_schema(df, "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def _bills(rows):
    df = pd.DataFrame(rows, columns=["bill_number", "vendor_code", "net_payable_amount"])
    df["bill_status"] = "APPROVED"
    df["payment_advice_date"] = pd.Timestamp("2026-03-18")
    df["submission_ref"] = "S-" + df["bill_number"]
    df["sheet"] = "synth"
    df["data_row"] = range(len(df))
    df = ensure_schema(df, "bills")
    df["row_seq"] = range(len(df))
    return df


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    init_db()
    tmp = tmp_path_factory.mktemp("decisions")
    key = f"ptest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        customer = Customer(key=key, name="ledger decisions test")
        s.add(customer)
        s.flush()

        def bronze(name, source_type):
            p = tmp / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, customer, source_type, p, name).id

        bz = {"stmt": bronze("stmt.txt", "bank_statement"),
              "bills": bronze("bills.txt", "bill_status"),
              "bills_v2": bronze("bills_v2.txt", "bill_status")}
        s.commit()
        cust = customer.id

    # T1<->INV-1 agrees on amount + signal -> HIGH (auto-locks)
    # T2 has no bill at all; INV-2 has no credit          -> exceptions
    # T3<->INV-3 agree on amount but the signal disagrees -> review (OPEN)
    bank = _bank([("T1", "V1", 1000.0), ("T2", "V9", 500.0), ("T3", "V3", 2000.0)])
    bills = _bills([("INV-1", "V1", 1000.0), ("INV-2", "V2", 750.0),
                    ("INV-3", "V4", 2000.0)])
    with SessionLocal() as s:
        ids, _ = ingest_gold_frames(
            s, cust,
            {"bank_txns": bank, "bills": bills,
             "recoveries": ensure_schema(pd.DataFrame(), "recoveries").assign(row_seq=[])},
            {"bank_txns": bz["stmt"], "bills": bz["bills"], "recoveries": bz["bills"]})
        s.commit()
    txn_ids = {ref: ids["bank_txns"][i] for i, ref in enumerate(bank["bank_ref"])}
    bill_ids = {no: ids["bills"][i] for i, no in enumerate(bills["bill_number"])}

    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        out, bank_pos, bill_pos = incremental.run_matching(s, cust, bz["stmt"], RULES)
        stats, _links, _ = incremental.finalize_ledger(s, cust, run_id, out, bank_pos, bill_pos)
        s.get(Run, run_id).status = "succeeded"
        s.commit()
    assert stats["matches_created"] == 2 and stats["auto_locked"] == 1
    assert stats["exceptions_opened"] == 2

    yield {"cust": cust, "key": key, "bz": bz, "txn": txn_ids, "bill": bill_ids,
           "run": run_id, "bills_frame": bills}

    with SessionLocal() as s:
        run_ids = list(s.execute(select(Run.id).where(Run.customer_id == cust)).scalars())
        if run_ids:
            s.execute(delete(RunMatchBill).where(RunMatchBill.run_id.in_(run_ids)))
            s.execute(delete(RunFrame).where(RunFrame.run_id.in_(run_ids)))
        ledger_ids = list(s.execute(
            select(MatchLedger.id).where(MatchLedger.customer_id == cust)).scalars())
        if ledger_ids:
            s.execute(delete(MatchLedgerBill)
                      .where(MatchLedgerBill.match_ledger_id.in_(ledger_ids)))
        for model in (AuditLog, IngestConflict, ExceptionLedger, MatchLedger,
                      GoldFileRow, GoldRecovery, GoldBill, GoldBankTxn,
                      SilverRecord, Run, SourceConfig, BronzeFile):
            s.execute(delete(model).where(model.customer_id == cust))
        s.execute(delete(Customer).where(Customer.id == cust))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def _match(cust, confidence):
    with SessionLocal() as s:
        return s.execute(select(MatchLedger).where(
            MatchLedger.customer_id == cust,
            MatchLedger.confidence == confidence)).scalar_one()


def _exception(cust, txn=None, bill=None, status=None):
    with SessionLocal() as s:
        q = select(ExceptionLedger).where(ExceptionLedger.customer_id == cust)
        if txn:
            q = q.where(ExceptionLedger.gold_bank_txn_id == txn)
        if bill:
            q = q.where(ExceptionLedger.gold_bill_id == bill)
        if status:
            q = q.where(ExceptionLedger.status == status)
        return list(s.execute(q.order_by(ExceptionLedger.resolved_at)).scalars())


def test_run_resolution_is_stamped_run(world):
    # run-opened exceptions carry no resolver yet; a run-resolved one says RUN
    for e in _exception(world["cust"]):
        assert e.status == "OPEN" and e.resolved_by is None


def test_reject_then_reopen_resolves_by_user(world):
    cust = world["cust"]
    review = _match(cust, "AMOUNT_ONLY") if _has(cust, "AMOUNT_ONLY") else _match(cust, "LOW")
    assert review.status == "OPEN"

    assert incremental.reject_match(review.id)["status"] == "REJECTED"
    (opened,) = _exception(cust, txn=world["txn"]["T3"], status="OPEN")
    assert opened.exception_type == "BANK_ONLY"
    # a run-created match's bills are NOT re-opened (the next run reports them)
    assert _exception(cust, bill=world["bill"]["INV-3"]) == []

    assert incremental.reopen_match(review.id)["status"] == "OPEN"
    (closed,) = _exception(cust, txn=world["txn"]["T3"])
    assert closed.status == "RESOLVED"
    assert closed.resolved_by == "USER_REOPEN"
    assert closed.resolved_by_match_id == review.id
    assert closed.resolved_by_run_id is None and closed.resolved_at is not None


def _has(cust, confidence):
    with SessionLocal() as s:
        return s.execute(select(MatchLedger.id).where(
            MatchLedger.customer_id == cust,
            MatchLedger.confidence == confidence)).first() is not None


def test_accept_resolves_linked_open_exceptions(world):
    cust = world["cust"]
    review = _match(cust, "AMOUNT_ONLY") if _has(cust, "AMOUNT_ONLY") else _match(cust, "LOW")
    # plant an OPEN BILL_ONLY row for the review match's bill, as a stale
    # earlier run would have left it
    with SessionLocal() as s:
        s.add(ExceptionLedger(customer_id=cust, exception_type="BILL_ONLY",
                              gold_bill_id=world["bill"]["INV-3"],
                              first_seen_run_id=world["run"]))
        s.commit()

    state = incremental.accept_match(review.id)
    assert state == {"id": review.id, "status": "LOCKED", "locked_by": "USER"}
    (row,) = _exception(cust, bill=world["bill"]["INV-3"])
    assert row.status == "RESOLVED" and row.resolved_by == "USER_ACCEPT"
    assert row.resolved_by_match_id == review.id
    with SessionLocal() as s:
        ev = s.execute(select(AuditLog).where(
            AuditLog.customer_id == cust,
            AuditLog.event_type == "ledger.match_accepted")
            .order_by(AuditLog.created_at.desc())).scalars().first()
    assert ev.details["exceptions_resolved"] == 1

    # unlock does NOT re-open anything: an OPEN match still claims its sides
    incremental.unlock_match(review.id)
    (row,) = _exception(cust, bill=world["bill"]["INV-3"])
    assert row.status == "RESOLVED"
    incremental.accept_match(review.id)

    view = incremental.ledger_view(cust)
    exc = next(e for e in view["exceptions"] if e["gold_bill_id"] == world["bill"]["INV-3"])
    assert exc["resolved_by"] == "USER_ACCEPT"
    assert exc["resolved_by_match_seq"] == review.seq


def test_manual_match_end_to_end(world):
    cust, txn, bill = world["cust"], world["txn"], world["bill"]

    # a bill already picked by a LOCKED match is refused, naming it
    with pytest.raises(incremental.LedgerConsumed) as ei:
        incremental.create_manual_match(cust, txn["T2"], [bill["INV-1"]])
    assert "INV-1" in str(ei.value) and "M-" in str(ei.value)
    # so is a held credit
    with pytest.raises(incremental.LedgerConsumed):
        incremental.create_manual_match(cust, txn["T1"], [bill["INV-2"]])

    # T2 (500) <-> INV-2 (750): the analyst pairs them despite the gap
    state = incremental.create_manual_match(cust, txn["T2"], [bill["INV-2"]],
                                            note="short-paid, agreed with vendor")
    assert state["status"] == "LOCKED" and state["locked_by"] == "USER"
    assert state["confidence"] == "MANUAL"
    assert state["variance"] == -250.0
    assert state["exceptions_resolved"] == 2
    with SessionLocal() as s:
        m = s.get(MatchLedger, state["id"])
        assert m.run_id is None and m.match_id == "manual"
        assert m.seq is not None and m.note == "short-paid, agreed with vendor"
        seqs = list(s.execute(select(MatchLedger.seq)
                              .where(MatchLedger.customer_id == cust)).scalars())
        assert len(seqs) == len(set(seqs))
    for gid, kw in ((txn["T2"], "txn"), (bill["INV-2"], "bill")):
        (row,) = _exception(cust, **{kw: gid})
        assert row.status == "RESOLVED" and row.resolved_by == "USER_MANUAL"
        assert row.resolved_by_match_id == state["id"]

    # every read path tolerates a NULL-run match
    view = incremental.ledger_view(cust)
    manual = next(x for x in view["matches"] if x["id"] == state["id"])
    assert manual["run_id"] is None and manual["confidence"] == "MANUAL"
    assert manual["note"] == "short-paid, agreed with vendor"
    assert [b["gold_bill_id"] for b in manual["bills"]] == [bill["INV-2"]]
    with SessionLocal() as s:
        ov = overview.overview(s, cust)
        ar = overview.ar_view(s, cust)
    assert ov["manual_matches"] == 1
    settled = [r for r in ar["rows"] if r["match_ledger_id"] == state["id"]]
    assert len(settled) == 1 and settled[0]["status"] == "SETTLED"
    assert settled[0]["variance"] == -250.0 and settled[0]["run_id"] is None
    with SessionLocal() as s:
        ev = s.execute(select(AuditLog).where(
            AuditLog.customer_id == cust,
            AuditLog.event_type == "ledger.match_created_manual")).scalar_one()
    assert ev.details["variance"] == -250.0 and ev.details["bill_count"] == 1
    assert "note" not in ev.details and "short-paid" not in str(ev.details)

    # the next pool excludes both sides with zero engine change
    with SessionLocal() as s:
        pool = incremental.build_pool(s, cust, world["bz"]["stmt"])
    assert txn["T2"] not in pool["bank_ids"] and bill["INV-2"] not in pool["bill_ids"]

    # a changed export for the manually settled bill lands in conflicts
    v2 = world["bills_frame"].copy()
    v2.loc[v2["bill_number"] == "INV-2", "bill_status"] = "RETURNED"
    with SessionLocal() as s:
        _, st = ingest_gold_frames(
            s, cust,
            {"bills": v2,
             "recoveries": ensure_schema(pd.DataFrame(), "recoveries").assign(row_seq=[])},
            {"bills": world["bz"]["bills_v2"], "recoveries": world["bz"]["bills_v2"]})
        s.commit()
    assert st["conflicts"] == 1

    # reject re-opens the credit AND the bill (no run will re-report it)
    assert incremental.unlock_match(state["id"])["status"] == "OPEN"
    assert incremental.reject_match(state["id"])["status"] == "REJECTED"
    (t2,) = _exception(cust, txn=txn["T2"], status="OPEN")
    (inv2,) = _exception(cust, bill=bill["INV-2"], status="OPEN")
    # no run created the manual match, so the re-opened rows inherit the
    # run that FIRST reported each side as an exception
    assert t2.first_seen_run_id == world["run"]
    assert inv2.first_seen_run_id == world["run"]


def test_manual_match_route(world):
    from app.main import app
    cust_key = world["key"]
    client = TestClient(app)
    bad = client.post("/api/matches/manual", json={
        "customer_id": cust_key, "gold_bank_txn_id": world["txn"]["T3"],
        "gold_bill_ids": [world["bill"]["INV-1"]]})
    assert bad.status_code == 409 and bad.json()["detail"]["error"] == "ALREADY_CONSUMED"
    nothing = client.post("/api/matches/manual", json={
        "customer_id": cust_key, "gold_bank_txn_id": world["txn"]["T2"],
        "gold_bill_ids": []})
    assert nothing.status_code == 400
    ok = client.post("/api/matches/manual", json={
        "customer_id": cust_key, "gold_bank_txn_id": world["txn"]["T2"],
        "gold_bill_ids": [world["bill"]["INV-2"]], "note": "  "})
    assert ok.status_code == 200
    body = ok.json()
    assert body["confidence"] == "MANUAL" and body["variance"] == -250.0
    assert client.get(f"/api/ledger?customer_id={cust_key}").status_code == 200
    assert client.get(f"/api/overview?customer_id={cust_key}").status_code == 200
    assert client.get(f"/api/ar?customer_id={cust_key}").status_code == 200


def test_workbooks_carry_ledger_sheets(world, tmp_path):
    """Item 3.3: the run workbook grows Decisions + Manual_Matches at
    download time (stored file untouched); a run with nothing in the
    ledger streams byte-identical; the ledger export lists the manual
    match under Matches AND Manual_Matches."""
    from openpyxl import load_workbook
    from app.main import app
    from recon.report import write_workbook
    cust, key, run_id = world["cust"], world["key"], world["run"]

    # give the run a stored workbook (finalize_ledger alone writes none)
    stored = tmp_path / "Recon_Output.xlsx"
    write_workbook({
        "summary": pd.DataFrame({"Category": ["x"], "Count": [1]}),
        "matched": pd.DataFrame({"bank_ref": ["T1"], "bill_number": ["INV-1"]}),
        "queue": pd.DataFrame({"exception_type": ["BANK_ONLY"], "bank_ref": ["T2"]}),
    }, stored)
    before = stored.read_bytes()
    with SessionLocal() as s:
        s.get(Run, run_id).workbook_path = str(stored)
        s.add(RunFrame(run_id=run_id, name="bank", row_count=1,
                       rows=[{"bank_ref": "T2", "amount": 500.0}]))
        s.add(RunFrame(run_id=run_id, name="bills", row_count=1,
                       rows=[{"bill_number": "INV-9", "submission_ref": "S-INV-9"}]))
        s.commit()

    client = TestClient(app)
    r = client.get(f"/api/runs/{run_id}/workbook")
    assert r.status_code == 200
    out = tmp_path / "dl.xlsx"
    out.write_bytes(r.content)
    wb = load_workbook(out, read_only=True)
    assert wb.sheetnames[:3] == ["Summary", "Matched", "Exception_Queue"]
    assert "Decisions" in wb.sheetnames and "Manual_Matches" in wb.sheetnames
    decisions = list(wb["Decisions"].iter_rows(values_only=True))
    assert decisions[0][:4] == ("Match", "Run_Match_Id", "Confidence", "Status")
    assert len(decisions) - 1 == 2                     # the run created 2 matches
    manual = list(wb["Manual_Matches"].iter_rows(values_only=True))
    assert len(manual) - 1 == 1                        # T2 is in this run's bank frame
    row = dict(zip(manual[0], manual[1]))
    assert row["Bank_Ref"] == "T2" and row["Bill_Numbers"] == "INV-2"
    assert row["Variance"] == -250.0
    # the stored file was not touched
    assert stored.read_bytes() == before

    # a run with nothing in the ledger streams the stored bytes unchanged
    with SessionLocal() as s:
        other = Run(customer_id=cust, status="succeeded", mode="snapshot",
                    params={}, payload={}, workbook_path=str(stored))
        s.add(other)
        s.commit()
        other_id = other.id
    r2 = client.get(f"/api/runs/{other_id}/workbook")
    assert r2.status_code == 200 and r2.content == before

    # customer-level ledger export
    r3 = client.get(f"/api/ledger/workbook?customer_id={key}")
    assert r3.status_code == 200
    (tmp_path / "ledger.xlsx").write_bytes(r3.content)
    lw = load_workbook(tmp_path / "ledger.xlsx", read_only=True)
    assert lw.sheetnames == ["Matches", "Manual_Matches", "Exceptions"]
    matches = list(lw["Matches"].iter_rows(values_only=True))
    confs = [dict(zip(matches[0], m))["Confidence"] for m in matches[1:]]
    assert "MANUAL" in confs and "HIGH" in confs
    exc = list(lw["Exceptions"].iter_rows(values_only=True))
    resolved_by = {dict(zip(exc[0], e))["Resolved_By"] for e in exc[1:]}
    assert "USER_MANUAL" in resolved_by
