"""
One reconciliation over SEVERAL bank statements: the credit pool is the
union of the selected statements' credits, each gold row once, in both
pool builders (db/reconcile_gold.run_snapshot, db/incremental.build_pool)
and through POST /api/reconcile (statement_bronze_ids; the scalar
statement_bronze_id stays accepted).
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

from db import SessionLocal, incremental, init_db, reconcile_gold  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.gold import reported_by_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldRecovery,
                       MatchLedger, MatchLedgerBill, MatchRuleSetRow, Run,
                       RunFrame, RunMatchBill, SilverRecord, SourceConfig)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from recon.rules import MatchRuleSet  # noqa: E402


@pytest.fixture()
def world(tmp_path):
    """Throwaway customer (created through the API so it has source
    configs) with two statements + one bills file."""
    init_db()
    from fastapi import FastAPI
    from app.routes import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    key = f"multistmt-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/customers", json={"key": key, "name": "multi statement test"})
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        c = s.execute(select(Customer).where(Customer.key == key)).scalar_one()

        def bronze(name, source_type):
            p = tmp_path / name
            p.write_text(name + uuid.uuid4().hex)
            return register_file(s, c, source_type, p, name).id

        bz = {"a": bronze("stmt-a.txt", "bank_statement"),
              "b": bronze("stmt-b.txt", "bank_statement"),
              "bills": bronze("bills.txt", "bill_status")}
        s.commit()
        pk = c.id
    yield {"pk": pk, "key": key, "bz": bz, "client": client}
    with SessionLocal() as s:
        run_ids = select(Run.id).where(Run.customer_id == pk)
        match_ids = select(MatchLedger.id).where(MatchLedger.customer_id == pk)
        s.execute(delete(RunMatchBill).where(RunMatchBill.run_id.in_(run_ids)))
        s.execute(delete(RunFrame).where(RunFrame.run_id.in_(run_ids)))
        s.execute(delete(MatchLedgerBill).where(MatchLedgerBill.match_ledger_id.in_(match_ids)))
        for model in (MatchLedger, ExceptionLedger, Run, AuditLog, GoldFileRow,
                      GoldRecovery, GoldBill, GoldBankTxn, SilverRecord, BronzeFile,
                      SourceConfig, MatchRuleSetRow):
            s.execute(delete(model).where(model.customer_id == pk))
        s.execute(delete(Customer).where(Customer.id == pk))
        s.commit()
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def credits(refs, day="2026-03-18"):
    df = ensure_schema(pd.DataFrame([
        # amount follows the ref so the same credit re-reported by another
        # statement is the SAME entity (the upsert key includes amount)
        {"bank_ref": r, "value_date": pd.Timestamp(day),
         "amount": 1000.0 + sum(map(ord, r)), "used_in_recon": True}
        for r in refs]), "bank_txns")
    df["row_seq"] = range(len(df))
    return df


def bills(numbers):
    df = ensure_schema(pd.DataFrame([
        {"bill_number": n, "submission_ref": f"CO6-{n}",
         "submission_date": pd.Timestamp("2026-03-10"),
         "net_payable_amount": 5000.0 + i, "bill_status": "PAYMENT MADE",
         "payment_advice_date": pd.Timestamp("2026-03-18"),
         "payment_order_ref": f"CO7-{n}",
         "payment_order_date": pd.Timestamp("2026-03-17"), "zone": "NR",
         "data_row": i + 2}
        for i, n in enumerate(numbers)]), "bills")
    df["row_seq"] = range(len(df))
    return df


def _ingest(pk, frames, bronze_ids):
    with SessionLocal() as s:
        ingest_gold_frames(s, pk, frames, bronze_ids)
        s.commit()


def _refs(df):
    return list(df["bank_ref"])


def test_union_of_two_statements_each_row_once(world):
    pk, bz = world["pk"], world["bz"]
    _ingest(pk, {"bank_txns": credits(["A1", "A2"])}, {"bank_txns": bz["a"]})
    _ingest(pk, {"bank_txns": credits(["B1", "B2", "B3"])}, {"bank_txns": bz["b"]})
    _ingest(pk, {"bills": bills(["X1"])}, {"bills": bz["bills"]})
    with SessionLocal() as s:
        one = reconcile_gold.build_snapshot_frames(s, pk, bz["a"])
        assert _refs(one["bank_df"]) == ["A1", "A2"]
        both = reconcile_gold.build_snapshot_frames(s, pk, [bz["a"], bz["b"]])
        assert _refs(both["bank_df"]) == ["A1", "A2", "B1", "B2", "B3"]
        # ids aligned with rows
        assert len(both["bank_ids"]) == 5
        rows = {r.id: r.bank_ref for r in s.execute(
            select(GoldBankTxn).where(GoldBankTxn.customer_id == pk)).scalars()}
        assert [rows[i] for i in both["bank_ids"]] == _refs(both["bank_df"])
        # same statement twice -> once
        assert _refs(reconcile_gold.build_snapshot_frames(
            s, pk, [bz["a"], bz["a"]])["bank_df"]) == ["A1", "A2"]
        # incremental pool sees the same union
        pool = incremental.build_pool(s, pk, [bz["a"], bz["b"]])
        assert _refs(pool["bank_df"]) == ["A1", "A2", "B1", "B2", "B3"]
        # the list clause is the union of the per-id clauses
        union = set(s.execute(select(GoldBankTxn.bank_ref).where(
            reported_by_file(s, GoldBankTxn, [bz["a"], bz["b"]], pk))).scalars())
        per_id = set()
        for i in (bz["a"], bz["b"]):
            per_id |= set(s.execute(select(GoldBankTxn.bank_ref).where(
                reported_by_file(s, GoldBankTxn, i, pk))).scalars())
        assert union == per_id == {"A1", "A2", "B1", "B2", "B3"}
        # a run over both still reconciles (engine untouched)
        out, bank_ids, _ = reconcile_gold.run_snapshot(
            s, pk, [bz["a"], bz["b"]], MatchRuleSet())
        assert len(out["bank"]) == 5 and len(bank_ids) == 5


def test_overlapping_re_export_shares_the_credit_once(world):
    pk, bz = world["pk"], world["bz"]
    _ingest(pk, {"bank_txns": credits(["A1", "A2"])}, {"bank_txns": bz["a"]})
    # b re-reports A2 (same key -> entity upsert keeps ONE gold row) + B1
    _ingest(pk, {"bank_txns": credits(["A2", "B1"])}, {"bank_txns": bz["b"]})
    with SessionLocal() as s:
        both = reconcile_gold.build_snapshot_frames(s, pk, [bz["a"], bz["b"]])
        refs = _refs(both["bank_df"])
        assert sorted(refs) == ["A1", "A2", "B1"] and len(refs) == 3


def test_reconcile_route_accepts_list_and_scalar(world):
    pk, bz, client, key = world["pk"], world["bz"], world["client"], world["key"]
    _ingest(pk, {"bank_txns": credits(["A1"])}, {"bank_txns": bz["a"]})
    _ingest(pk, {"bank_txns": credits(["B1"])}, {"bank_txns": bz["b"]})
    _ingest(pk, {"bills": bills(["X1"])}, {"bills": bz["bills"]})
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [bz["a"], bz["b"]],
        "mode": "snapshot"})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["statement_bronze_ids"] == [bz["a"], bz["b"]]
    assert meta["statement_bronze_id"] == bz["a"]
    assert meta["filenames"]["statements"] == ["stmt-a.txt", "stmt-b.txt"]
    assert meta["filenames"]["statement"] == "stmt-a.txt + 1 more"
    assert meta["counts"]["bank_credits"] == 2
    assert meta["selfcheck"] is None         # several statements -> list only
    assert isinstance(meta["selfchecks"], list)
    # scalar form still works
    r = client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_id": bz["a"], "mode": "snapshot"})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["statement_bronze_ids"] == [bz["a"]]
    assert r.json()["meta"]["filenames"]["statement"] == "stmt-a.txt"
    # unknown id -> 404, none -> 400
    assert client.post("/api/reconcile", json={
        "customer_id": key, "statement_bronze_ids": [bz["a"], 999999],
        "mode": "snapshot"}).status_code == 404
    assert client.post("/api/reconcile", json={
        "customer_id": key, "mode": "snapshot"}).status_code == 400


def test_selfcheck_sees_the_whole_statement_not_just_credits(world):
    """A statement holding only debits has zero credits; the run-time
    selfcheck must hand the adapter the FULL statement (debits included,
    with used_in_recon) — a credits-only frame is empty and trips the
    adapter's "no rows recognised" guard, a false parse-mismatch."""
    from app.routes import _gold_selfcheck
    from recon.sources.base import SelfCheckError

    pk, bz = world["pk"], world["bz"]
    debits = credits(["D1", "D2"])
    debits["used_in_recon"] = False
    debits["txn_type"] = "TFR-"
    _ingest(pk, {"bank_txns": debits}, {"bank_txns": bz["a"]})
    with SessionLocal() as s:
        frame = reconcile_gold.statement_txns_frame(s, bz["a"], pk)
    assert len(frame) == 2 and "used_in_recon" in frame.columns
    assert not frame["used_in_recon"].any()

    class Adapter:
        seen = None
        def selfcheck(self, gold, path, params):
            Adapter.seen = gold["bank_txns"]
            if gold["bank_txns"].empty:
                raise SelfCheckError("no transaction rows recognised", None)
            return None   # no printed totals -> nothing to tie

    path = Path(__file__)   # any existing file: the stub never opens it
    assert _gold_selfcheck(Adapter(), {}, frame, path, pk, bz["a"]) is None
    assert len(Adapter.seen) == 2

    # and a refused frame still reports WHY (no totals -> detail text)
    empty = frame.iloc[0:0]
    out = _gold_selfcheck(Adapter(), {}, empty, path, pk, bz["a"])
    assert out["passed"] is False and "no transaction rows" in out["detail"]
