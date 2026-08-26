"""
The API surface. One POST runs the whole reconciliation — snapshot mode
(today's behaviour) or incremental mode (accumulate + carry forward with
a locked ledger). The GETs serve what runs already produced. Parsers take
file paths, so uploads are written to a per-run temp directory first,
then registered in the bronze file store; results persist to the
database, so runs survive restarts.
"""

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from db import SessionLocal, incremental, reconcile_gold
from db import overview as db_overview
from db.audit import record_event
from db.bronze import register_file
from db.ingest import ingest_gold_frames
from db.models import BronzeFile, Customer, MatchRuleSetRow, SourceConfig
from db.silver import persist_silver
from db.storage import file_sha256
from logging_setup import customer_id_var, get_logger
from recon import (REGISTRY, MatchRuleSet, PipelineSinks, SelfCheckError,
                   get_adapter, run_pipeline, write_workbook)
from recon.gold import GOLD_COLUMNS
from recon.rules import FieldMapping

from . import runs
from .serialize import clean, df_to_records, summary_records

router = APIRouter(prefix="/api")
logger = get_logger(__name__)

# upload field -> source_type used by adapters / bronze registry
SOURCE_TYPES = {
    "statement": "bank_statement",
    "bills": "bill_status",
    "rnote": "lineage_rnote",
    "crn": "lineage_crn",
}

# gold frame name -> upload field that produced it
FRAME_TO_FIELD = {"bank_txns": "statement", "bills": "bills",
                  "recoveries": "bills", "lineage_rnote": "rnote",
                  "lineage_crn": "crn"}

# Sample documents shipped in the repo, used as defaults when a field has
# no upload. The UI shows them pre-filled; any of them can be replaced.
SAMPLE_DIR = (Path(__file__).resolve().parents[2]
              / "Receipt_reconciliation_and_IDR _ Requested_sample_documents")

DEFAULT_PATTERNS = {
    "statement": ("*.pdf", "*.PDF"),
    "bills": ("BILL STATUS*.xlsx", "BILL STATUS*.xlsm"),
    "rnote": ("RNOTE*.xlsx", "RNOTE*.xlsm"),
    "crn": ("CRN*.xlsx", "CRN*.xlsm"),
}


def _default_file(field):
    if not SAMPLE_DIR.is_dir():
        return None
    for pattern in DEFAULT_PATTERNS[field]:
        for p in sorted(SAMPLE_DIR.glob(pattern)):
            if not p.name.startswith("~$"):   # Excel lock files
                return p
    return None


def _fail(status, code, detail):
    raise HTTPException(status_code=status, detail={"error": code, "detail": detail})


def _allowed_exts(field: str) -> tuple:
    """Extensions any REGISTERED adapter for this slot's source_type
    declares (adapter.file_kinds is the single authority — the API keeps
    no literal list). Union across adapters: the check is a coarse UX
    guard, per-customer precision lives in the UI's accept attribute and
    a wrong file past this gate fails loudly in the adapter's own parse.
    Empty union means no restriction."""
    source_type = SOURCE_TYPES[field]
    exts: set = set()
    for (st, _key), adapter in REGISTRY.items():
        if st == source_type:
            exts.update(adapter.file_kinds)
    return tuple(sorted(exts))


def _save_upload(upload: UploadFile, field: str, tmpdir: Path) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    allowed = _allowed_exts(field)
    if allowed and suffix not in allowed:
        _fail(400, "INVALID_INPUT",
              f"{field}: expected {' or '.join(allowed)}, got "
              f"'{upload.filename}'")
    # keep a recognisable name, but strip anything path-like
    stem = re.sub(r"[^\w. -]", "_", Path(upload.filename).stem)[:80] or field
    dest = tmpdir / f"{field}__{stem}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    if dest.stat().st_size == 0:
        _fail(400, "INVALID_INPUT", f"{field}: uploaded file is empty")
    return dest


@router.get("/defaults")
def default_files(customer_id: str = "default"):
    """Which repo sample document backs each field when nothing is
    uploaded. null means no default exists for that field. Samples back
    the seeded default customer ONLY — for any other customer every
    field is null, so real tenants never see (or ingest) demo data."""
    if customer_id != "default":
        return {field: None for field in SOURCE_TYPES}
    return {
        field: ({"name": p.name, "size": p.stat().st_size} if p else None)
        for field in SOURCE_TYPES
        for p in [_default_file(field)]
    }


def _resolve_input(field, upload, tmpdir, required, allow_samples=True):
    """An uploaded file wins; otherwise fall back to the repo default —
    but only when allow_samples (the default customer). Returns
    (path, display_name)."""
    if upload is not None and upload.filename:
        path = _save_upload(upload, field, tmpdir)
        return path, upload.filename
    p = _default_file(field) if allow_samples else None
    if p is None:
        if required:
            _fail(400, "INVALID_INPUT",
                  f"{field}: nothing uploaded"
                  + (" and no repo sample found" if allow_samples
                     else " (repo samples back the default customer only)"))
        return None, None
    return p, p.name


def _get_customer(session, customer_key: str) -> Customer:
    customer = session.execute(
        select(Customer).where(Customer.key == customer_key)
    ).scalar_one_or_none()
    if customer is None:
        _fail(400, "INVALID_INPUT", f"unknown customer '{customer_key}'")
    return customer


class CaptureSinks(PipelineSinks):
    """Collects silver results and gold frames during the run; everything
    is persisted after the run succeeds."""

    def __init__(self):
        self.silver = {}
        self.gold = {}
        self.checks = {}

    def on_silver(self, source_type, adapter, silver):
        self.silver[source_type] = silver

    def on_gold(self, source_type, adapter, gold):
        self.gold[source_type] = gold

    def on_selfcheck(self, source_type, adapter, check):
        if check is not None:
            self.checks[source_type] = check


def _load_customer_context(session, customer_key: str):
    """Customer + its active source configs + default rule set row."""
    customer = _get_customer(session, customer_key)
    source_configs = {
        sc.source_type: sc for sc in session.execute(
            select(SourceConfig)
            .where(SourceConfig.customer_id == customer.id,
                   SourceConfig.is_active.is_(True))
        ).scalars()
    }
    rule_row = session.execute(
        select(MatchRuleSetRow)
        .where(MatchRuleSetRow.customer_id == customer.id,
               MatchRuleSetRow.is_default.is_(True))
    ).scalar_one_or_none()
    return customer, source_configs, rule_row


def _register_inputs(session, customer, paths, names, source_configs):
    """Bronze-register every provided input; identical bytes reuse rows.
    Returns {field: bronze_file_id}."""
    bronze_ids = {}
    for field, path in paths.items():
        if path is not None:
            sc = source_configs.get(SOURCE_TYPES[field])
            bronze_ids[field] = register_file(
                session, customer, SOURCE_TYPES[field], Path(path),
                names[field],
                adapter_key=sc.adapter_key if sc else None).id
    return bronze_ids


def _build_adapters(source_configs, paths, customer_key):
    """Adapter instances + per-source params for every provided input."""
    inputs, adapters, adapter_params = {}, {}, {}
    for field, path in paths.items():
        if path is None:
            continue
        source_type = SOURCE_TYPES[field]
        sc = source_configs.get(source_type)
        if sc is None:
            _fail(400, "INVALID_INPUT",
                  f"customer '{customer_key}' has no source configured "
                  f"for {source_type}")
        try:
            adapters[source_type] = get_adapter(source_type, sc.adapter_key)
        except KeyError as e:
            _fail(400, "INVALID_INPUT", str(e))
        inputs[source_type] = path
        adapter_params[source_type] = sc.params or {}
    return inputs, adapters, adapter_params


def _effective_rules(rule_row, form_config):
    """dataclass defaults <- customer DB rule set <- form tunables.
    field_map is customer-level only (never in the form)."""
    db_overrides = {}
    if rule_row is not None:
        db_overrides = {
            "date_tolerance_days": rule_row.date_tolerance_days,
            "amount_tolerance": rule_row.amount_tolerance,
            "window_days": rule_row.window_days,
            "co7_lookback_days": rule_row.co7_lookback_days,
            "allow_batched": rule_row.allow_batched,
            "max_batch_size": rule_row.max_batch_size,
            "paid_statuses": rule_row.paid_statuses or None,
            "weights": rule_row.weights or None,
            "field_map": rule_row.field_map or None,
        }
    return MatchRuleSet().merged(db_overrides).merged(form_config)


def _build_payload(out, form_config, selfcheck, names, customer_key,
                   mode, extra_meta=None, rules=None):
    payload = {
        "summary": summary_records(out["summary"]),
        "matched": df_to_records(out["matched"]),
        "exceptions": df_to_records(out["queue"]),
        "meta": {
            "counts": {
                "matched": len(out["matched"]),
                "bank_only": len(out["bank_only"]),
                "bill_only": len(out["bill_only"]),
                "match_review": len(out["match_review"]),
                "bank_credits": len(out["bank"]),
                "bank_txns": len(out["bank_all"]),
                "bills": len(out["bills"]),
                "bills_grouped": len(out["bills_grouped"]),
                "recoveries": len(out["recoveries"]),
            },
            "selfcheck": selfcheck,
            "config": form_config,
            "filenames": names,
            "customer": customer_key,
            "mode": mode,
        },
    }
    if extra_meta:
        payload["meta"].update(extra_meta)
    if rules is not None:
        # echo the effective config so every run proves which field
        # mapping it actually used (the golden gate can't cover the
        # gold-sourced reconcile paths)
        payload["meta"]["rules_effective"] = {
            "field_map": rules.field_map.to_dict(),
            "paid_statuses": sorted(rules.paid_statuses),
            "weights": rules.weights,
        }
    return payload


def _frame_records(out):
    return {
        "bank": df_to_records(out["bank_all"]),
        "bills": df_to_records(out["bills"]),
        # the Bills + lineage tab shows attempts combined per bill;
        # the per-attempt data is embedded in each row's Attempts list
        "bills_enriched": df_to_records(out["bills_grouped"]),
        "recoveries": df_to_records(out["recoveries"]),
    }


def _persist_side_effects(customer_pk, bronze_ids, capture, run_id=None,
                          ingestion_event=None):
    """Silver rows + ingestion-owned gold, one transaction. Returns
    ({frame: {row_seq: gold id}}, ingest stats). When ingestion_event is
    given (the standalone /api/ingest path), an ingestion.completed audit
    event rides the same commit — transactionally coupled to the gold
    writes it describes."""
    gold_frames, gold_bronze_ids = {}, {}
    for source_type, gold in capture.gold.items():
        for name, df in gold.items():
            gold_frames[name] = df
            gold_bronze_ids[name] = bronze_ids[FRAME_TO_FIELD[name]]
    with SessionLocal() as session:
        for field, source_type in SOURCE_TYPES.items():
            silver = capture.silver.get(source_type)
            if silver is not None and field in bronze_ids:
                persist_silver(session, customer_pk, bronze_ids[field], {
                    name: df_to_records(df)
                    for name, df in silver.frames.items()
                })
        ids, stats = ingest_gold_frames(session, customer_pk, gold_frames,
                                        gold_bronze_ids, run_id=run_id)
        if ingestion_event is not None:
            record_event(session, logger, event_type="ingestion.completed",
                         customer_id=customer_pk,
                         details={**ingestion_event, "stats": stats})
        session.commit()
    return ids, stats


def _match_links(out, bill_ids_by_seq):
    """run_match_bills rows from the matched frame; indices == gold bill
    row_seq in snapshot mode."""
    links = []
    if out["matched"].empty:
        return links
    for r in out["matched"].itertuples():
        picked = {int(i) for i in r.bill_indices}
        for i in picked:
            if i in bill_ids_by_seq:
                links.append({"match_id": r.match_id,
                              "gold_bill_id": bill_ids_by_seq[i],
                              "role": "picked"})
        for i in {int(i) for i in r.candidate_indices} - picked:
            if i in bill_ids_by_seq:
                links.append({"match_id": r.match_id,
                              "gold_bill_id": bill_ids_by_seq[i],
                              "role": "candidate"})
    return links


@router.post("/runs")
async def create_run(
    request: Request,
    statement: UploadFile | None = File(None),
    bills: UploadFile | None = File(None),
    rnote: UploadFile | None = File(None),
    crn: UploadFile | None = File(None),
    window_days: int = Form(0),
    co7_lookback_days: int = Form(5),
    date_tolerance_days: int = Form(2),
    amount_tolerance: float = Form(0.0),
    allow_batched: bool = Form(True),
    max_batch_size: int = Form(3),
    customer_id: str = Form("default"),
    mode: str = Form("snapshot"),
):
    if mode not in ("snapshot", "incremental"):
        _fail(400, "INVALID_INPUT", f"unknown mode '{mode}'")
    tmpdir = Path(tempfile.mkdtemp(prefix="recon_run_"))
    try:
        # repo samples never stand in for a real tenant's documents
        samples_ok = customer_id == "default"
        stmt_path, stmt_name = _resolve_input("statement", statement, tmpdir,
                                              True, samples_ok)
        bills_path, bills_name = _resolve_input("bills", bills, tmpdir,
                                                True, samples_ok)
        rnote_path, rnote_name = _resolve_input("rnote", rnote, tmpdir,
                                                False, samples_ok)
        crn_path, crn_name = _resolve_input("crn", crn, tmpdir,
                                            False, samples_ok)
        paths = {"statement": stmt_path, "bills": bills_path,
                 "rnote": rnote_path, "crn": crn_path}
        names = {"statement": stmt_name, "bills": bills_name,
                 "rnote": rnote_name, "crn": crn_name}

        # customer context + bronze registry (identical bytes reuse rows)
        with SessionLocal() as session:
            customer, source_configs, rule_row = _load_customer_context(
                session, customer_id)
            # bind correlation as early as possible so the bronze
            # registration lines below already carry cust=
            customer_id_var.set(customer_id)
            request.state.customer_id = customer_id
            bronze_ids = _register_inputs(session, customer, paths, names,
                                          source_configs)
            session.commit()
            customer_pk = customer.id
            rule_set_id = rule_row.id if rule_row else None

        inputs, adapters, adapter_params = _build_adapters(
            source_configs, paths, customer_id)

        form_config = {
            "window_days": window_days,
            "co7_lookback_days": co7_lookback_days,
            "date_tolerance_days": date_tolerance_days,
            "amount_tolerance": amount_tolerance,
            "allow_batched": allow_batched,
            "max_batch_size": max_batch_size,
        }
        rules = _effective_rules(rule_row, form_config)

        common = dict(customer_pk=customer_pk, customer_key=customer_id,
                      rule_set_id=rule_set_id, form_config=form_config,
                      rules=rules, inputs=inputs, adapters=adapters,
                      adapter_params=adapter_params, bronze_ids=bronze_ids,
                      names=names, tmpdir=tmpdir, stmt_path=stmt_path)
        if mode == "incremental":
            return await _run_incremental(**common)
        return await _run_snapshot(**common)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_snapshot(customer_pk, customer_key, rule_set_id, form_config,
                        rules, inputs, adapters, adapter_params, bronze_ids,
                        names, tmpdir, stmt_path):
    capture = CaptureSinks()
    try:
        out = await run_in_threadpool(
            run_pipeline, inputs, adapters, adapter_params, rules,
            sinks=capture, verbose=False)
    except SelfCheckError as e:
        # the statement parse did not tie to the bank's printed totals
        logger.warning("run.selfcheck_failed", extra={
            "event_type": "run.selfcheck_failed",
            "details": {"customer_id": customer_pk, "detail": str(e)}})
        runs.persist_failure(customer_pk, "snapshot", form_config,
                             {"error": "BANK_SELFCHECK_FAILED",
                              "detail": str(e)})
        _fail(422, "BANK_SELFCHECK_FAILED", str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("run.parse_failed", extra={
            "event_type": "run.parse_failed",
            "details": {"customer_id": customer_pk}})
        runs.persist_failure(customer_pk, "snapshot", form_config,
                             {"error": "PARSE_FAILED",
                              "detail": f"{type(e).__name__}: {e}"})
        _fail(422, "PARSE_FAILED", f"{type(e).__name__}: {e}")

    workbook_path = tmpdir / "Recon_Output.xlsx"
    await run_in_threadpool(write_workbook, out, workbook_path)

    ids, ingest_stats = await run_in_threadpool(
        _persist_side_effects, customer_pk, bronze_ids, capture)

    # the adapter's own parse-time check (captured via sinks) — routes
    # never re-check with a source-specific function
    selfcheck = clean(capture.checks.get("bank_statement"))
    payload = _build_payload(out, form_config, selfcheck, names,
                             customer_key, "snapshot", rules=rules)
    await run_in_threadpool(
        runs.persist_success, customer_pk, rule_set_id, "snapshot",
        {**form_config, "ingest": ingest_stats}, payload, selfcheck,
        workbook_path, _frame_records(out),
        _match_links(out, ids.get("bills", {})))
    return payload


async def _run_incremental(customer_pk, customer_key, rule_set_id,
                           form_config, rules, inputs, adapters,
                           adapter_params, bronze_ids, names, tmpdir,
                           stmt_path):
    try:
        # start_run binds run_id_var itself, as soon as the id exists —
        # it's called directly (not via run_in_threadpool), so the set
        # propagates to the rest of this request
        run_id = incremental.start_run(customer_pk, form_config)
    except incremental.RunInProgress as e:
        _fail(409, "RUN_IN_PROGRESS", str(e))
    try:
        # parse -> silver -> gold -> selfcheck, per provided source
        capture = CaptureSinks()

        def parse_all():
            for source_type, path in inputs.items():
                adapter = adapters[source_type]
                p = adapter_params.get(source_type, {})
                silver = adapter.parse(path, p)
                capture.on_silver(source_type, adapter, silver)
                gold = adapter.to_gold(silver, p)
                capture.on_gold(source_type, adapter, gold)
                capture.on_selfcheck(source_type, adapter,
                                     adapter.selfcheck(gold, path, p))

        await run_in_threadpool(parse_all)
        _ids, ingest_stats = await run_in_threadpool(
            _persist_side_effects, customer_pk, bronze_ids, capture, run_id)

        def match_and_ledger():
            with SessionLocal() as session:
                out, bank_ids, bill_ids = incremental.run_matching(
                    session, customer_pk, bronze_ids["statement"], rules)
                ledger_stats, links, ledger_ids = incremental.finalize_ledger(
                    session, customer_pk, run_id, out, bank_ids, bill_ids)
                session.commit()
            return out, ledger_stats, links, ledger_ids

        out, ledger_stats, links, ledger_ids = await run_in_threadpool(
            match_and_ledger)

        workbook_path = tmpdir / "Recon_Output.xlsx"
        await run_in_threadpool(write_workbook, out, workbook_path)

        # stamp the durable ledger row id onto matched / review rows so the
        # UI can offer accept/reject; snapshot runs never carry this key
        # (stamped after write_workbook so the workbook is unchanged)
        if not out["matched"].empty:
            out["matched"]["match_ledger_id"] = \
                out["matched"]["match_id"].map(ledger_ids)
        if not out["queue"].empty and "match_id" in out["queue"].columns:
            out["queue"]["match_ledger_id"] = \
                out["queue"]["match_id"].map(ledger_ids)
        # parse-time file check from the adapter (the pool frame carries
        # forward exceptions from earlier statements, so checking IT
        # against one statement's printed totals was never right)
        selfcheck = clean(capture.checks.get("bank_statement"))
        payload = _build_payload(
            out, form_config, selfcheck, names, customer_key, "incremental",
            extra_meta={"ingest": ingest_stats, "ledger": ledger_stats},
            rules=rules)
        await run_in_threadpool(
            runs.persist_success, customer_pk, rule_set_id, "incremental",
            {**form_config, "ingest": ingest_stats}, payload, selfcheck,
            workbook_path, _frame_records(out), links, run_id)
        return payload
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {"detail": e.detail}
        logger.warning("run.failed", extra={
            "event_type": "run.failed",
            "details": {"customer_id": customer_pk, "run_id": run_id,
                        "detail": detail}})
        runs.persist_failure(customer_pk, "incremental", form_config,
                             detail, run_id=run_id)
        raise
    except SelfCheckError as e:
        logger.warning("run.selfcheck_failed", extra={
            "event_type": "run.selfcheck_failed",
            "details": {"customer_id": customer_pk, "run_id": run_id,
                        "detail": str(e)}})
        runs.persist_failure(customer_pk, "incremental", form_config,
                             {"error": "BANK_SELFCHECK_FAILED",
                              "detail": str(e)}, run_id=run_id)
        _fail(422, "BANK_SELFCHECK_FAILED", str(e))
    except Exception as e:
        logger.exception("run.parse_failed", extra={
            "event_type": "run.parse_failed",
            "details": {"customer_id": customer_pk, "run_id": run_id}})
        runs.persist_failure(customer_pk, "incremental", form_config,
                             {"error": "PARSE_FAILED",
                              "detail": f"{type(e).__name__}: {e}"},
                             run_id=run_id)
        _fail(422, "PARSE_FAILED", f"{type(e).__name__}: {e}")


@router.get("/runs/{run_id}")
def read_run(run_id: str):
    rec = runs.get_run(run_id)
    if rec is None or rec.payload is None:
        _fail(404, "RUN_NOT_FOUND", f"no run {run_id}")
    return rec.payload


@router.get("/runs/{run_id}/frames/{name}")
def read_frame(run_id: str, name: str):
    """One source frame of a completed run, from the run_frames table."""
    rows = runs.get_frame(run_id, name)
    if rows is None:
        if runs.get_run(run_id) is None:
            _fail(404, "RUN_NOT_FOUND", f"no run {run_id}")
        _fail(404, "FRAME_NOT_FOUND",
              f"unknown frame '{name}'; have: {runs.frame_names(run_id)}")
    return {"name": name, "count": len(rows), "rows": rows}


@router.get("/runs/{run_id}/workbook")
def download_workbook(run_id: str):
    rec = runs.get_run(run_id)
    if (rec is None or rec.workbook_path is None
            or not Path(rec.workbook_path).exists()):
        _fail(404, "RUN_NOT_FOUND", f"no workbook for run {run_id}")
    stem = Path((rec.payload or {}).get("meta", {})
                .get("filenames", {}).get("statement") or "statement").stem
    safe = re.sub(r"[^\w. -]", "_", stem)[:60]
    return FileResponse(
        rec.workbook_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Recon_{safe}.xlsx",
    )


# --- ledger (incremental mode) -----------------------------------------

class AcceptBody(BaseModel):
    # override an ambiguous pick: must be one of the match's bills
    gold_bill_id: Optional[str] = None


@router.post("/matches/{match_ledger_id}/accept")
def accept_match(match_ledger_id: str, body: Optional[AcceptBody] = None):
    """Lock an OPEN (review-confidence) match: both sides leave every
    future incremental pool for good. An optional gold_bill_id picks
    WHICH of the match's candidate bills settles the credit."""
    try:
        state = incremental.accept_match(
            match_ledger_id, body.gold_bill_id if body else None)
    except ValueError as e:
        _fail(400, "INVALID_INPUT", str(e))
    if state is None:
        _fail(404, "MATCH_NOT_FOUND", f"no ledger match {match_ledger_id}")
    return state


@router.post("/matches/{match_ledger_id}/unlock")
def unlock_match(match_ledger_id: str):
    """Reopen a LOCKED match (USER or AUTO_HIGH): back to OPEN for
    review; nothing is released — re-accept or reject from there."""
    state = incremental.unlock_match(match_ledger_id)
    if state is None:
        _fail(404, "MATCH_NOT_FOUND", f"no ledger match {match_ledger_id}")
    return state


@router.post("/matches/{match_ledger_id}/reject")
def reject_match(match_ledger_id: str):
    """Reject an OPEN match: bills are released and the credit goes back
    into the pool as an OPEN BANK_ONLY exception."""
    state = incremental.reject_match(match_ledger_id)
    if state is None:
        _fail(404, "MATCH_NOT_FOUND", f"no ledger match {match_ledger_id}")
    return state


@router.post("/matches/{match_ledger_id}/reopen")
def reopen_match(match_ledger_id: str):
    """Undo a REJECTED match: back to OPEN, re-claiming the credit and its
    bills and closing the BANK_ONLY exception the rejection opened. Fails
    409 if a later run already claimed either side."""
    try:
        state = incremental.reopen_match(match_ledger_id)
    except incremental.LedgerConflict as e:
        _fail(409, "MATCH_CONFLICT", str(e))
    if state is None:
        _fail(404, "MATCH_NOT_FOUND", f"no ledger match {match_ledger_id}")
    return state


@router.get("/ledger")
def ledger(customer_id: str = "default"):
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
    return incremental.ledger_view(customer_pk)


@router.get("/overview")
def customer_overview(customer_id: str = "default"):
    """Command Center aggregates: gold pool, ledger state, open exposure,
    match rate, top open exceptions, last run/ingestion. Read-only."""
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        return db_overview.overview(session, customer_pk)


@router.get("/ar")
def ar_reconciliation(customer_id: str = "default"):
    """AR working set: settled / in-review / outstanding bills with the
    settling credit, variance, due-date age, KPIs and aging buckets —
    the AR Reconciliation view. Read-only."""
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        return db_overview.ar_view(session, customer_pk)


@router.get("/audit")
def audit_trail(customer_id: str = "default", limit: int = 500):
    """The customer's audit_log event stream, newest first — feeds the
    Audit trail view. Read-only (reading the log is not itself logged)."""
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        return db_overview.audit_events(session, customer_pk,
                                        max(1, min(limit, 2000)))


# --- additive read API -------------------------------------------------

@router.get("/customers")
def list_customers():
    with SessionLocal() as session:
        out = []
        for c in session.execute(select(Customer).order_by(Customer.key)).scalars():
            sources = session.execute(
                select(SourceConfig)
                .where(SourceConfig.customer_id == c.id,
                       SourceConfig.is_active.is_(True))
            ).scalars()
            out.append({
                "key": c.key,
                "name": c.name,
                "sources": {s.source_type: s.adapter_key for s in sources},
            })
        return out


@router.get("/runs")
def list_runs(customer_id: str | None = None, limit: int = 50):
    customer_pk = None
    if customer_id is not None:
        with SessionLocal() as session:
            customer_pk = _get_customer(session, customer_id).id
    return [
        {
            "run_id": r.id,
            "status": r.status,
            "mode": r.mode,
            "created_at": r.created_at.isoformat(),
            "counts": (r.payload or {}).get("meta", {}).get("counts"),
            "error": r.error,
        }
        for r in runs.list_runs(customer_pk, min(max(limit, 1), 200))
    ]


# --- two-step workflow: standalone ingestion ----------------------------

@router.post("/ingest")
async def ingest(
    request: Request,
    statement: UploadFile | None = File(None),
    bills: UploadFile | None = File(None),
    rnote: UploadFile | None = File(None),
    crn: UploadFile | None = File(None),
    customer_id: str = Form("default"),
    slots: str | None = Form(None),
):
    """Raw files -> bronze -> silver -> gold, standalone. `slots` is a
    comma-separated list of ENABLED slots; the repo-sample fallback only
    applies to enabled slots, so skipped documents are truly skipped (an
    uploaded file always implies its slot is enabled). With `slots`
    omitted, only uploaded files are ingested — an empty form no longer
    silently ingests all four samples. No Run row — a failed ingest
    leaves log lines only, since nothing ran."""
    tmpdir = Path(tempfile.mkdtemp(prefix="recon_ingest_"))
    try:
        uploads = {"statement": statement, "bills": bills,
                   "rnote": rnote, "crn": crn}
        enabled = {f for f, u in uploads.items() if u is not None and u.filename}
        if slots is not None:
            requested = {s.strip() for s in slots.split(",") if s.strip()}
            unknown = requested - set(uploads)
            if unknown:
                _fail(400, "INVALID_INPUT",
                      f"unknown slot(s): {sorted(unknown)}")
            enabled |= requested
        if not enabled:
            _fail(400, "INVALID_INPUT",
                  "no slots enabled: upload a file or enable at least one "
                  "of statement/bills/rnote/crn")

        paths, names = {}, {}
        samples_ok = customer_id == "default"
        for field, upload in uploads.items():
            if field in enabled:
                paths[field], names[field] = _resolve_input(
                    field, upload, tmpdir, False, samples_ok)
            else:
                paths[field], names[field] = None, None
        if not any(paths.values()):
            _fail(400, "INVALID_INPUT",
                  "no input files: enabled slots have neither uploads nor "
                  "repo samples")

        with SessionLocal() as session:
            customer, source_configs, _rule_row = _load_customer_context(
                session, customer_id)
            customer_id_var.set(customer_id)
            request.state.customer_id = customer_id
            # dedup outcome per file, checked BEFORE register_file (which
            # silently returns the existing row on identical bytes)
            outcomes = {}
            for field, path in paths.items():
                if path is None:
                    continue
                sha = file_sha256(path)
                exists = session.execute(
                    select(BronzeFile.id)
                    .where(BronzeFile.customer_id == customer.id,
                           BronzeFile.sha256 == sha).limit(1)).first()
                outcomes[field] = "deduped" if exists else "registered"
            bronze_ids = _register_inputs(session, customer, paths, names,
                                          source_configs)
            session.commit()
            customer_pk = customer.id

        files_resp = [{
            "field": field,
            "source_type": SOURCE_TYPES[field],
            "original_name": names[field],
            "bronze_file_id": bronze_ids[field],
            "outcome": outcomes[field],
            "size_bytes": Path(paths[field]).stat().st_size,
        } for field in bronze_ids]

        inputs, adapters, adapter_params = _build_adapters(
            source_configs, paths, customer_id)

        capture = CaptureSinks()

        def parse_all():
            checks = {}
            for source_type, path in inputs.items():
                adapter = adapters[source_type]
                p = adapter_params.get(source_type, {})
                silver = adapter.parse(path, p)
                capture.on_silver(source_type, adapter, silver)
                gold = adapter.to_gold(silver, p)
                capture.on_gold(source_type, adapter, gold)
                check = adapter.selfcheck(gold, path, p)
                if check is not None:
                    checks[source_type] = check
            return checks

        try:
            checks = await run_in_threadpool(parse_all)
        except SelfCheckError as e:
            logger.warning("ingest.selfcheck_failed", extra={
                "event_type": "ingest.selfcheck_failed",
                "details": {"customer_id": customer_pk, "detail": str(e)}})
            _fail(422, "BANK_SELFCHECK_FAILED", str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("ingest.parse_failed", extra={
                "event_type": "ingest.parse_failed",
                "details": {"customer_id": customer_pk}})
            _fail(422, "PARSE_FAILED", f"{type(e).__name__}: {e}")

        selfcheck = (clean(checks["bank_statement"])
                     if "bank_statement" in checks else None)
        # audit details carry ids/outcomes, never filenames (joined back
        # at read time by GET /api/ingestions)
        ingestion_event = {
            "files": [{k: v for k, v in f.items() if k != "original_name"}
                      for f in files_resp],
            "selfcheck_passed": (True if "bank_statement" in checks
                                 else None),
        }
        _ids, stats = await run_in_threadpool(
            _persist_side_effects, customer_pk, bronze_ids, capture,
            None, ingestion_event)
        return {"customer": customer_id, "files": files_resp,
                "stats": stats, "selfcheck": selfcheck}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/ingestions")
def ingestions(customer_id: str = "default", limit: int = 50):
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        return reconcile_gold.list_ingestions(
            session, customer_pk, min(max(limit, 1), 200))


# --- gold layer browsing ------------------------------------------------
# NOTE: static /gold/* routes must be declared before /gold/{frame}

@router.get("/gold/schema")
def gold_schema():
    """Gold field lists for the matching-config dropdowns."""
    from db.gold import FRAME_DATE_COLS
    _numeric = {"bank_txns": ["amount"],
                "bills": ["gross_amount", "approved_amount",
                          "deduction_amount", "net_payable_amount",
                          "recovery_sum"]}
    return {
        frame: {
            "fields": GOLD_COLUMNS[frame],
            "date_fields": FRAME_DATE_COLS.get(frame, []),
            "numeric_fields": _numeric.get(frame, []),
        }
        for frame in ("bank_txns", "bills")
    }


@router.get("/gold/files")
def gold_files(customer_id: str = "default"):
    """Every bronze file owning gold rows (feeds the Reconcile statement
    picker and the gold tabs' ingestion filters)."""
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        return reconcile_gold.gold_files(session, customer_pk)


@router.get("/gold/{frame}")
def gold_frame(frame: str, customer_id: str = "default",
               bronze_file_id: int | None = None, limit: int = 20000):
    """One gold table as engine-shaped rows, optionally filtered to one
    ingested file. Served whole with a hard cap; `total` exposes
    truncation."""
    if frame not in reconcile_gold.BROWSE_FRAMES:
        _fail(404, "FRAME_NOT_FOUND",
              f"unknown gold frame '{frame}'; "
              f"have: {sorted(reconcile_gold.BROWSE_FRAMES)}")
    with SessionLocal() as session:
        customer_pk = _get_customer(session, customer_id).id
        df, provenance, total = reconcile_gold.gold_frame(
            session, frame, customer_pk, bronze_file_id, limit)
    rows = df_to_records(df)
    for rec, (bfid, seq) in zip(rows, provenance):
        rec["bronze_file_id"] = bfid
        rec["row_seq"] = seq
    return {"name": frame, "count": len(rows), "total": total, "rows": rows}


# --- two-step workflow: reconcile from gold -----------------------------

class ReconcileParams(BaseModel):
    customer_id: str = "default"
    statement_bronze_id: int
    mode: str = "snapshot"
    # per-run tunables are OPTIONAL: None falls through MatchRuleSet.merged
    # (which ignores None) so the customer's saved matching config applies.
    # The UI no longer sends them — matching config is the single source;
    # API callers can still override per run by passing explicit values.
    window_days: Optional[int] = None
    co7_lookback_days: Optional[int] = None
    date_tolerance_days: Optional[int] = None
    amount_tolerance: Optional[float] = None
    allow_batched: Optional[bool] = None
    max_batch_size: Optional[int] = None


def _gold_filenames(session, customer_pk: int, stmt_name: str) -> dict:
    """meta.filenames for gold-sourced runs: statement is exact; the
    rest are the latest bronze file per source_type (best-effort,
    cosmetic — keeps the app header rendering)."""
    names = {"statement": stmt_name, "bills": None, "rnote": None, "crn": None}
    for source_type, field in (("bill_status", "bills"),
                               ("lineage_rnote", "rnote"),
                               ("lineage_crn", "crn")):
        b = session.execute(
            select(BronzeFile)
            .where(BronzeFile.customer_id == customer_pk,
                   BronzeFile.source_type == source_type)
            .order_by(BronzeFile.uploaded_at.desc()).limit(1)
        ).scalar_one_or_none()
        names[field] = b.original_name if b else None
    return names


def _gold_selfcheck(adapter, adapter_params, bank_df, stmt_path,
                    customer_pk, statement_bronze_id):
    """Selfcheck of the gold-rebuilt credits against the stored bronze
    statement, using the CUSTOMER'S configured bank adapter — its own
    selfcheck() is the single authority on what "ties" means. Missing
    file / no adapter -> None (never a 500). A mismatch is reported with
    passed=false + WARNING audit event, NOT a 422 — the parse-time gate
    already ran at ingest; a delta here signals cross-file accumulation,
    not corruption."""
    try:
        if adapter is None or not Path(stmt_path).exists():
            return None
        # bank_df is the credits-only engine frame; the adapter contract
        # takes its own gold frames, so hand back the used_in_recon flag
        gold = {"bank_txns": bank_df.assign(used_in_recon=True)}
        try:
            check = adapter.selfcheck(gold, stmt_path, adapter_params)
            if check is None:
                return None
            check = dict(check)
            check["passed"] = True
        except SelfCheckError as e:
            check = dict(e.check or {})
            check["passed"] = False
        if not check["passed"]:
            with SessionLocal() as session:
                record_event(
                    session, logger, event_type="run.selfcheck_mismatch",
                    level=logging.WARNING, customer_id=customer_pk,
                    entity_type="bronze_file", entity_id=statement_bronze_id,
                    # counts only — no rupee totals in log/audit details
                    details={"parsed_count": check.get("parsed_count"),
                             "stated_count": check.get("stated_count")})
                session.commit()
        return clean(check)
    except Exception:
        logger.exception("run.selfcheck_error", extra={
            "event_type": "run.selfcheck_error",
            "details": {"statement_bronze_id": statement_bronze_id}})
        return None


@router.post("/reconcile")
async def reconcile_from_gold(request: Request, params: ReconcileParams):
    """Reconcile purely from the gold layer — no uploads, no parsing.
    snapshot = the chosen statement vs ALL current gold bills with real
    window semantics and no ledger involvement; incremental = feeds the
    durable match ledger exactly as before."""
    if params.mode not in ("snapshot", "incremental"):
        _fail(400, "INVALID_INPUT", f"unknown mode '{params.mode}'")
    tmpdir = Path(tempfile.mkdtemp(prefix="recon_gold_"))
    try:
        with SessionLocal() as session:
            customer, source_configs, rule_row = _load_customer_context(
                session, params.customer_id)
            customer_id_var.set(params.customer_id)
            request.state.customer_id = params.customer_id
            stmt_bronze = reconcile_gold.get_statement_bronze(
                session, customer.id, params.statement_bronze_id)
            if stmt_bronze is None:
                _fail(404, "STATEMENT_NOT_FOUND",
                      f"no ingested bank statement with id "
                      f"{params.statement_bronze_id} for customer "
                      f"'{params.customer_id}'")
            customer_pk = customer.id
            rule_set_id = rule_row.id if rule_row else None
            stmt_path = Path(stmt_bronze.stored_path)
            names = _gold_filenames(session, customer_pk,
                                    stmt_bronze.original_name)
            # the customer's bank adapter re-runs its own selfcheck
            # against the stored bronze file (best-effort, see
            # _gold_selfcheck)
            bank_sc = source_configs.get("bank_statement")
            bank_adapter, bank_adapter_params = None, {}
            if bank_sc is not None:
                try:
                    bank_adapter = get_adapter("bank_statement",
                                               bank_sc.adapter_key)
                    bank_adapter_params = bank_sc.params or {}
                except KeyError:
                    bank_adapter = None

        form_config = {
            "window_days": params.window_days,
            "co7_lookback_days": params.co7_lookback_days,
            "date_tolerance_days": params.date_tolerance_days,
            "amount_tolerance": params.amount_tolerance,
            "allow_batched": params.allow_batched,
            "max_batch_size": params.max_batch_size,
        }
        rules = _effective_rules(rule_row, form_config)
        common = dict(customer_pk=customer_pk,
                      customer_key=params.customer_id,
                      rule_set_id=rule_set_id, form_config=form_config,
                      rules=rules,
                      statement_bronze_id=params.statement_bronze_id,
                      stmt_path=stmt_path, names=names, tmpdir=tmpdir,
                      bank_adapter=bank_adapter,
                      bank_adapter_params=bank_adapter_params)
        if params.mode == "incremental":
            return await _reconcile_incremental_from_gold(**common)
        return await _reconcile_snapshot_from_gold(**common)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _reconcile_snapshot_from_gold(customer_pk, customer_key,
                                        rule_set_id, form_config, rules,
                                        statement_bronze_id, stmt_path,
                                        names, tmpdir, bank_adapter,
                                        bank_adapter_params):
    def do_match():
        with SessionLocal() as session:
            return reconcile_gold.run_snapshot(
                session, customer_pk, statement_bronze_id, rules)

    try:
        out, _bank_ids, bill_ids = await run_in_threadpool(do_match)
    except Exception as e:
        logger.exception("run.reconcile_failed", extra={
            "event_type": "run.reconcile_failed",
            "details": {"customer_id": customer_pk,
                        "statement_bronze_id": statement_bronze_id}})
        runs.persist_failure(customer_pk, "snapshot", form_config,
                             {"error": "RECONCILE_FAILED",
                              "detail": f"{type(e).__name__}: {e}"})
        _fail(422, "RECONCILE_FAILED", f"{type(e).__name__}: {e}")

    workbook_path = tmpdir / "Recon_Output.xlsx"
    await run_in_threadpool(write_workbook, out, workbook_path)
    selfcheck = await run_in_threadpool(
        _gold_selfcheck, bank_adapter, bank_adapter_params, out["bank"],
        stmt_path, customer_pk, statement_bronze_id)
    payload = _build_payload(
        out, form_config, selfcheck, names, customer_key, "snapshot",
        extra_meta={"statement_bronze_id": statement_bronze_id}, rules=rules)
    await run_in_threadpool(
        runs.persist_success, customer_pk, rule_set_id, "snapshot",
        form_config, payload, selfcheck, workbook_path,
        _frame_records(out), _match_links(out, dict(enumerate(bill_ids))))
    return payload


async def _reconcile_incremental_from_gold(customer_pk, customer_key,
                                           rule_set_id, form_config, rules,
                                           statement_bronze_id, stmt_path,
                                           names, tmpdir, bank_adapter,
                                           bank_adapter_params):
    try:
        run_id = incremental.start_run(customer_pk, form_config)
    except incremental.RunInProgress as e:
        _fail(409, "RUN_IN_PROGRESS", str(e))
    try:
        def match_and_ledger():
            with SessionLocal() as session:
                out, bank_ids, bill_ids = incremental.run_matching(
                    session, customer_pk, statement_bronze_id, rules)
                ledger_stats, links, ledger_ids = incremental.finalize_ledger(
                    session, customer_pk, run_id, out, bank_ids, bill_ids)
                session.commit()
            return out, ledger_stats, links, ledger_ids

        out, ledger_stats, links, ledger_ids = await run_in_threadpool(
            match_and_ledger)

        workbook_path = tmpdir / "Recon_Output.xlsx"
        await run_in_threadpool(write_workbook, out, workbook_path)
        if not out["matched"].empty:
            out["matched"]["match_ledger_id"] = \
                out["matched"]["match_id"].map(ledger_ids)
        if not out["queue"].empty and "match_id" in out["queue"].columns:
            out["queue"]["match_ledger_id"] = \
                out["queue"]["match_id"].map(ledger_ids)
        # selfcheck must see the statement's FULL gold credits, not the
        # pool — the pool is a designed subset (ledger-consumed credits
        # excluded) and would mismatch the printed totals on every run
        def statement_selfcheck():
            with SessionLocal() as session:
                bank_df = reconcile_gold.statement_credits_frame(
                    session, statement_bronze_id)
            return _gold_selfcheck(bank_adapter, bank_adapter_params,
                                   bank_df, stmt_path, customer_pk,
                                   statement_bronze_id)

        selfcheck = await run_in_threadpool(statement_selfcheck)
        payload = _build_payload(
            out, form_config, selfcheck, names, customer_key, "incremental",
            extra_meta={"ledger": ledger_stats,
                        "statement_bronze_id": statement_bronze_id},
            rules=rules)
        await run_in_threadpool(
            runs.persist_success, customer_pk, rule_set_id, "incremental",
            form_config, payload, selfcheck, workbook_path,
            _frame_records(out), links, run_id)
        return payload
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {"detail": e.detail}
        logger.warning("run.failed", extra={
            "event_type": "run.failed",
            "details": {"customer_id": customer_pk, "run_id": run_id,
                        "detail": detail}})
        runs.persist_failure(customer_pk, "incremental", form_config,
                             detail, run_id=run_id)
        raise
    except Exception as e:
        logger.exception("run.reconcile_failed", extra={
            "event_type": "run.reconcile_failed",
            "details": {"customer_id": customer_pk, "run_id": run_id}})
        runs.persist_failure(customer_pk, "incremental", form_config,
                             {"error": "RECONCILE_FAILED",
                              "detail": f"{type(e).__name__}: {e}"},
                             run_id=run_id)
        _fail(422, "RECONCILE_FAILED", f"{type(e).__name__}: {e}")


# --- per-customer configuration -----------------------------------------

@router.get("/adapters")
def list_adapters():
    """Adapter registry for the ingest-slot dropdowns."""
    out: dict = {}
    for (source_type, key), adapter in sorted(REGISTRY.items()):
        out.setdefault(source_type, []).append({
            "key": key,
            "label": adapter.label or key.replace("_", " ").upper(),
            "system": adapter.system or "",
            "file_kinds": list(adapter.file_kinds),
        })
    return out


class ExactSignalBody(BaseModel):
    bank_field: str
    bill_field: str
    weight: int = 2
    key: str | None = None


class FieldMapBody(BaseModel):
    bank_amount_field: str
    bill_amount_field: str
    bank_date_field: str
    bill_date_primary: str
    bill_date_fallback: str | None = None
    exact_signals: list[ExactSignalBody] = []
    eligibility_field: str
    fallback_due_statuses: list[str] = []


class RulesBody(BaseModel):
    date_tolerance_days: int = 2
    amount_tolerance: float = 0.0
    window_days: int = 0
    co7_lookback_days: int = 5
    allow_batched: bool = True
    max_batch_size: int = 3
    paid_statuses: list[str]
    weights: dict[str, int]
    field_map: FieldMapBody


def _validate_rules(body: RulesBody):
    bank_fields = set(GOLD_COLUMNS["bank_txns"])
    bill_fields = set(GOLD_COLUMNS["bills"])
    fm = body.field_map

    def need(value, pool, what):
        if value not in pool:
            _fail(400, "INVALID_INPUT",
                  f"{what} '{value}' is not a gold column")

    need(fm.bank_amount_field, bank_fields, "bank amount field")
    need(fm.bank_date_field, bank_fields, "bank date field")
    need(fm.bill_amount_field, bill_fields, "bill amount field")
    need(fm.bill_date_primary, bill_fields, "primary bill date field")
    if fm.bill_date_fallback is not None:
        need(fm.bill_date_fallback, bill_fields, "fallback bill date field")
    need(fm.eligibility_field, bill_fields, "eligibility field")
    for sig in fm.exact_signals:
        need(sig.bank_field, bank_fields, "signal bank field")
        need(sig.bill_field, bill_fields, "signal bill field")
        if sig.weight < 1:
            _fail(400, "INVALID_INPUT", "signal weights must be positive")
    if any(w < 1 for w in body.weights.values()):
        _fail(400, "INVALID_INPUT", "weights must be positive")
    if body.date_tolerance_days < 0 or body.amount_tolerance < 0 \
            or body.window_days < 0 or body.co7_lookback_days < 0:
        _fail(400, "INVALID_INPUT", "tolerances must be >= 0")
    if body.max_batch_size < 2:
        _fail(400, "INVALID_INPUT", "max_batch_size must be >= 2")
    if not body.paid_statuses:
        _fail(400, "INVALID_INPUT", "paid_statuses must not be empty")


def _rules_to_dict(rules: MatchRuleSet) -> dict:
    return {
        "date_tolerance_days": rules.date_tolerance_days,
        "amount_tolerance": rules.amount_tolerance,
        "window_days": rules.window_days,
        "co7_lookback_days": rules.co7_lookback_days,
        "allow_batched": rules.allow_batched,
        "max_batch_size": rules.max_batch_size,
        "paid_statuses": sorted(rules.paid_statuses),
        "weights": rules.weights,
        "field_map": rules.field_map.to_dict(),
    }


@router.get("/customers/{customer_key}/config")
def get_customer_config(customer_key: str):
    """The customer's EFFECTIVE rule set (merged over defaults, so the
    UI always sees concrete values even for a NULL row) + its sources."""
    with SessionLocal() as session:
        customer, source_configs, rule_row = _load_customer_context(
            session, customer_key)
        return {
            "key": customer.key,
            "name": customer.name,
            "sources": {st: sc.adapter_key
                        for st, sc in source_configs.items()},
            "rules": _rules_to_dict(_effective_rules(rule_row, None)),
        }


@router.put("/customers/{customer_key}/config")
def put_customer_config(customer_key: str, body: RulesBody):
    """Save the customer's matching configuration (six tunables +
    paid_statuses + weights + field mapping)."""
    _validate_rules(body)
    # normalize the field map through the dataclass round-trip
    field_map = FieldMapping.from_dict(body.field_map.model_dump()).to_dict()
    with SessionLocal() as session:
        customer, _sources, rule_row = _load_customer_context(
            session, customer_key)
        customer_id_var.set(customer_key)
        if rule_row is None:
            rule_row = MatchRuleSetRow(customer_id=customer.id,
                                       name="default", is_default=True)
            session.add(rule_row)
        rule_row.date_tolerance_days = body.date_tolerance_days
        rule_row.amount_tolerance = body.amount_tolerance
        rule_row.window_days = body.window_days
        rule_row.co7_lookback_days = body.co7_lookback_days
        rule_row.allow_batched = body.allow_batched
        rule_row.max_batch_size = body.max_batch_size
        rule_row.paid_statuses = body.paid_statuses
        rule_row.weights = body.weights
        rule_row.field_map = field_map
        session.flush()
        record_event(session, logger, event_type="config.rules_updated",
                     customer_id=customer.id, entity_type="match_rule_set",
                     entity_id=rule_row.id,
                     details={"signals": len(body.field_map.exact_signals)})
        session.commit()
    return get_customer_config(customer_key)


class SourcesBody(BaseModel):
    sources: dict[str, str]


@router.put("/customers/{customer_key}/sources")
def put_customer_sources(customer_key: str, body: SourcesBody):
    """Persist adapter choice per source_type (partial map allowed)."""
    for source_type, adapter_key in body.sources.items():
        if (source_type, adapter_key) not in REGISTRY:
            _fail(400, "INVALID_INPUT",
                  f"no adapter '{adapter_key}' for source_type "
                  f"'{source_type}'")
    with SessionLocal() as session:
        customer, source_configs, _rule_row = _load_customer_context(
            session, customer_key)
        customer_id_var.set(customer_key)
        for source_type, adapter_key in body.sources.items():
            sc = source_configs.get(source_type)
            if sc is None:
                session.add(SourceConfig(customer_id=customer.id,
                                         source_type=source_type,
                                         adapter_key=adapter_key, params={}))
            elif sc.adapter_key != adapter_key:
                # adapter changed: params are adapter-specific, reset them;
                # a no-op save keeps them (the seeded {"sheet": 0} survives)
                sc.adapter_key = adapter_key
                sc.params = {}
        record_event(session, logger, event_type="config.sources_updated",
                     customer_id=customer.id,
                     details={"sources": body.sources})
        session.commit()
        updated = {sc.source_type: sc.adapter_key for sc in session.execute(
            select(SourceConfig)
            .where(SourceConfig.customer_id == customer.id,
                   SourceConfig.is_active.is_(True))).scalars()}
    return {"key": customer_key, "sources": updated}


class CustomerBody(BaseModel):
    key: str
    name: str


@router.post("/customers")
def create_customer(body: CustomerBody):
    """Create a customer with the default sources + rule set cloned —
    the fast path for onboarding someone on a different bank/ERP: create,
    flip adapters, edit the matching config."""
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", body.key):
        _fail(400, "INVALID_INPUT",
              "key must be 1-64 chars of a-z 0-9 _ -")
    if not body.name.strip():
        _fail(400, "INVALID_INPUT", "name must not be empty")
    from db.seeds import DEFAULT_SOURCES
    with SessionLocal() as session:
        exists = session.execute(
            select(Customer).where(Customer.key == body.key)
        ).scalar_one_or_none()
        if exists is not None:
            _fail(409, "CUSTOMER_EXISTS", f"customer '{body.key}' exists")
        customer = Customer(key=body.key, name=body.name.strip())
        session.add(customer)
        session.flush()
        for source_type, adapter_key, params in DEFAULT_SOURCES:
            session.add(SourceConfig(customer_id=customer.id,
                                     source_type=source_type,
                                     adapter_key=adapter_key, params=params))
        session.add(MatchRuleSetRow(
            customer_id=customer.id, name="default", is_default=True,
            paid_statuses=["PAYMENT MADE", "CO7 DONE"],
            weights={"advice_date": 4, "zone": 2, "co7_date": 1}))
        record_event(session, logger, event_type="customer.created",
                     customer_id=customer.id, entity_type="customer",
                     entity_id=customer.id, details={"key": body.key})
        session.commit()
        return {
            "key": customer.key,
            "name": customer.name,
            "sources": {st: key for st, key, _p in DEFAULT_SOURCES},
        }
