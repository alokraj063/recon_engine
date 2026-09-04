"""
The configurability plan's definition-of-done: onboarding a hypothetical
customer on a DIFFERENT bank + ERP + upstream document kind must touch
only adapters, source_configs rows and a rule set — zero edits under
recon/matching, recon/engine.py, db/ingest.py, db/incremental.py,
db/reconcile_gold.py or app/routes.py.

This test registers a toy synthetic adapter trio in the registry, wires
a throwaway customer to it (including a NOVEL lineage slot + doc type,
"lineage_grn"/"GRN"), ingests, and runs both snapshot and incremental
end-to-end through the unchanged machinery.
"""

import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import recon.sources as sources  # noqa: E402
from db import SessionLocal, incremental, init_db, reconcile_gold  # noqa: E402
from db.bronze import register_file  # noqa: E402
from db.ingest import ingest_gold_frames  # noqa: E402
from db.models import (AuditLog, BronzeFile, Customer, ExceptionLedger,  # noqa: E402
                       GoldBankTxn, GoldBill, GoldFileRow, GoldLineageDoc,
                       GoldRecovery, MatchLedger, MatchLedgerBill, Run,
                       RunMatchBill, SilverRecord, SourceConfig)
from db.storage import storage  # noqa: E402
from recon.gold import ensure_schema  # noqa: E402
from recon.rules import ExactSignal, FieldMapping, MatchRuleSet  # noqa: E402
from recon.sources.base import SilverResult, SourceAdapter  # noqa: E402


# --- the synthetic source family ---------------------------------------

class SynthBankAdapter(SourceAdapter):
    source_type = "bank_statement"
    adapter_key = "synthbank"
    label = "Synthetic bank CSV"
    system = "SYNTH"
    file_kinds = (".csv",)

    def parse(self, path, params):
        df = pd.read_csv(path)
        return SilverResult({"txns": df})

    def to_gold(self, silver, params):
        s = silver.frames["txns"]
        df = pd.DataFrame({
            "bank_ref": s["ref"],
            "customer_ref": s["payer_code"],       # the exact-signal source
            "narrative": s["memo"],
            "value_date": pd.to_datetime(s["date"]),
            "amount": s["amount"].astype(float),
            "txn_type": "CREDIT",
        })
        df["used_in_recon"] = True
        df = ensure_schema(df, "bank_txns")
        df["row_seq"] = range(len(df))
        return {"bank_txns": df}


class SynthErpAdapter(SourceAdapter):
    source_type = "bill_status"
    adapter_key = "syntherp"
    label = "Synthetic ERP export"
    system = "SYNTH"
    file_kinds = (".csv",)

    def parse(self, path, params):
        return SilverResult({"bills": pd.read_csv(path)})

    def to_gold(self, silver, params):
        s = silver.frames["bills"]
        bills = pd.DataFrame({
            "bill_number": s["invoice"],
            "vendor_code": s["vendor"],
            "bill_status": s["state"],
            "net_payable_amount": s["net"].astype(float),
            "payment_advice_date": pd.to_datetime(s["paid_on"]),
            "submission_ref": s["submission"],
            "sheet": "synth",
            "data_row": range(len(s)),
        })
        bills = ensure_schema(bills, "bills")
        bills["row_seq"] = range(len(bills))
        recoveries = ensure_schema(pd.DataFrame(), "recoveries")
        recoveries["row_seq"] = range(len(recoveries))
        recoveries["bill_row_seq"] = recoveries.get("bill_index")
        return {"bills": bills, "recoveries": recoveries}


class SynthGrnAdapter(SourceAdapter):
    """A document kind neither RNOTE nor CRN — proves lineage slots and
    doc types are open-ended."""
    source_type = "lineage_grn"
    adapter_key = "synthgrn"
    label = "Synthetic GRN report"
    system = "SYNTH"
    file_kinds = (".csv",)

    def parse(self, path, params):
        return SilverResult({"grn": pd.read_csv(path)})

    def to_gold(self, silver, params):
        s = silver.frames["grn"]
        df = pd.DataFrame({
            "doc_no": s["grn_no"],
            "doc_date": pd.to_datetime(s["grn_date"]),
            "invoice_no": s["invoice"],
            "po_no": s["po"],
        })
        df["doc_type"] = "GRN"
        df = ensure_schema(df, "lineage_docs")
        df["row_seq"] = range(len(df))
        return {"lineage_grn": df}


SYNTH_ADAPTERS = (SynthBankAdapter(), SynthErpAdapter(), SynthGrnAdapter())

# the customer's rule set: different signal, statuses and copy — pure config
SYNTH_RULES = MatchRuleSet(
    paid_statuses=frozenset({"APPROVED"}),
    field_map=FieldMapping(
        exact_signals=(ExactSignal("customer_ref", "vendor_code", 3),),
        bill_date_fallback=None,
        fallback_due_statuses=(),
    ),
    copy_overrides={"expected_basis": {
        "ADVICE_DATE": "Chase the customer for this invoice."}},
)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    init_db()
    tmp = tmp_path_factory.mktemp("synth")

    # input files a real tenant would upload
    (tmp / "stmt.csv").write_text(
        "ref,date,amount,payer_code,memo\n"
        "T1,2026-03-18,1000.0,V1,payment for INV-1\n"
        "T2,2026-03-18,500.0,V9,unknown remitter\n")
    (tmp / "bills.csv").write_text(
        "invoice,vendor,state,net,paid_on,submission\n"
        "INV-1,V1,APPROVED,1000.0,2026-03-18,S1\n"
        "INV-2,V2,APPROVED,750.0,2026-03-18,S2\n")
    (tmp / "grn.csv").write_text(
        "grn_no,grn_date,invoice,po\n"
        "G-77,2026-03-01,INV-1,PO-9\n")

    # register the family — the ONLY code a new source needs
    for a in SYNTH_ADAPTERS:
        sources.REGISTRY[(a.source_type, a.adapter_key)] = a
        sources.BY_KEY[a.adapter_key] = a

    key = f"synth-{uuid.uuid4().hex[:8]}"
    slots = [("bank_statement", "bank_statement", "synthbank", "stmt.csv"),
             ("bill_status", "bill_status", "syntherp", "bills.csv"),
             ("lineage_grn", "lineage", "synthgrn", "grn.csv")]
    bronze = {}
    with SessionLocal() as s:
        customer = Customer(key=key, name="Synthetic source family test")
        s.add(customer)
        s.flush()
        for slot, role, adapter_key, fname in slots:
            s.add(SourceConfig(customer_id=customer.id, source_type=slot,
                               role=role, adapter_key=adapter_key, params={}))
            bronze[slot] = register_file(s, customer, slot, tmp / fname,
                                         fname, adapter_key=adapter_key).id
        s.commit()
        customer_pk = customer.id

    # parse -> gold via the slot's (role-resolved) adapter, then ingest
    gold_frames, bronze_ids = {}, {}
    for slot, _role, adapter_key, fname in slots:
        adapter = sources.resolve_adapter(slot, adapter_key)
        gold = adapter.to_gold(adapter.parse(tmp / fname, {}), {})
        for name, df in gold.items():
            frame_key = slot if name.startswith("lineage") else name
            gold_frames[frame_key] = df
            bronze_ids[frame_key] = bronze[slot]
    with SessionLocal() as s:
        ingest_gold_frames(s, customer_pk, gold_frames, bronze_ids)
        s.commit()

    yield {"customer_pk": customer_pk, "bronze": bronze}

    with SessionLocal() as s:
        run_ids = [r for (r,) in s.execute(
            Run.__table__.select().with_only_columns(Run.id)
            .where(Run.customer_id == customer_pk))]
        if run_ids:
            s.execute(delete(RunMatchBill).where(RunMatchBill.run_id.in_(run_ids)))
        ledger_ids = [m for (m,) in s.execute(
            MatchLedger.__table__.select().with_only_columns(MatchLedger.id)
            .where(MatchLedger.customer_id == customer_pk))]
        if ledger_ids:
            s.execute(delete(MatchLedgerBill)
                      .where(MatchLedgerBill.match_ledger_id.in_(ledger_ids)))
        for model in (AuditLog, ExceptionLedger, MatchLedger, GoldFileRow,
                      GoldRecovery, GoldBill, GoldBankTxn, GoldLineageDoc,
                      SilverRecord,
                      Run, SourceConfig, BronzeFile):
            s.execute(delete(model).where(model.customer_id == customer_pk))
        s.execute(delete(Customer).where(Customer.id == customer_pk))
        s.commit()
    for a in SYNTH_ADAPTERS:
        sources.REGISTRY.pop((a.source_type, a.adapter_key), None)
        sources.BY_KEY.pop(a.adapter_key, None)
    shutil.rmtree(storage.root / "bronze" / key, ignore_errors=True)


def test_snapshot_over_synthetic_family(world):
    with SessionLocal() as s:
        out, _bank_ids, _bill_ids = reconcile_gold.run_snapshot(
            s, world["customer_pk"], world["bronze"]["bank_statement"],
            SYNTH_RULES)

    # T1 <-> INV-1: amount + custom exact signal + primary date => HIGH
    assert len(out["matched"]) == 1
    m = out["matched"].iloc[0]
    assert m["bank_ref"] == "T1" and m["bill_number"] == "INV-1"
    assert m["confidence"] == "HIGH"

    # T2 has no bill; INV-2 was advised with no credit
    assert list(out["bank_only"]["bank_ref"]) == ["T2"]
    assert list(out["bill_only"]["bill_number"]) == ["INV-2"]
    # per-customer copy override reached the exception row
    assert list(out["bill_only"]["action"]) == \
        ["Chase the customer for this invoice."]

    # the NOVEL doc type flowed through lineage: trail + status + via
    enriched = out["bills_enriched"]
    inv1 = enriched[enriched["bill_number"] == "INV-1"].iloc[0]
    assert inv1["LineageStatus"] == "GRN"
    assert inv1["GRN_MatchedVia"] == "InvoiceNo"
    assert inv1["Receipt_Doc"] == "G-77" and inv1["PO"] == "PO-9"
    inv2 = enriched[enriched["bill_number"] == "INV-2"].iloc[0]
    assert inv2["LineageStatus"] == "NO_UPSTREAM_DOC"


def test_incremental_over_synthetic_family(world):
    cust = world["customer_pk"]
    run_id = incremental.start_run(cust, {})
    with SessionLocal() as s:
        out, bank_ids, bill_ids = incremental.run_matching(
            s, cust, world["bronze"]["bank_statement"], SYNTH_RULES)
        stats, _links, _ledger_ids = incremental.finalize_ledger(
            s, cust, run_id, out, bank_ids, bill_ids)
        s.get(Run, run_id).status = "succeeded"
        s.commit()

    # the HIGH match auto-locked in the durable ledger
    assert stats["matches_created"] == 1
    assert stats["auto_locked"] == 1
    assert stats["exceptions_opened"] == 2   # T2 BANK_ONLY + INV-2 BILL_ONLY
