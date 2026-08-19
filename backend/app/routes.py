"""
The API surface. One POST runs the whole reconciliation; the GETs serve
what that run already produced. Parsers take file paths, so uploads are
written to a per-run temp directory first.
"""

import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from recon import ReconConfig, bank_selfcheck, run, write_workbook

from .runs import get_run, new_run
from .serialize import clean, df_to_records, summary_records

router = APIRouter(prefix="/api")

ALLOWED = {
    "statement": (".pdf",),
    "bills": (".xlsx", ".xlsm"),
    "rnote": (".xlsx", ".xlsm"),
    "crn": (".xlsx", ".xlsm"),
}

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


def _save_upload(upload: UploadFile, field: str, tmpdir: Path) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED[field]:
        _fail(400, "INVALID_INPUT",
              f"{field}: expected {' or '.join(ALLOWED[field])}, got "
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
def default_files():
    """Which repo sample document backs each field when nothing is
    uploaded. null means no default exists for that field."""
    return {
        field: ({"name": p.name, "size": p.stat().st_size} if p else None)
        for field in ALLOWED
        for p in [_default_file(field)]
    }


def _resolve_input(field, upload, tmpdir, required):
    """An uploaded file wins; otherwise fall back to the repo default.
    Returns (path, display_name)."""
    if upload is not None and upload.filename:
        path = _save_upload(upload, field, tmpdir)
        return path, upload.filename
    p = _default_file(field)
    if p is None:
        if required:
            _fail(400, "INVALID_INPUT",
                  f"{field}: nothing uploaded and no repo sample found")
        return None, None
    return p, p.name


@router.post("/runs")
async def create_run(
    statement: Optional[UploadFile] = File(None),
    bills: Optional[UploadFile] = File(None),
    rnote: Optional[UploadFile] = File(None),
    crn: Optional[UploadFile] = File(None),
    window_days: int = Form(0),
    co7_lookback_days: int = Form(5),
    date_tolerance_days: int = Form(2),
    amount_tolerance: float = Form(0.0),
    allow_batched: bool = Form(True),
    max_batch_size: int = Form(3),
):
    tmpdir = Path(tempfile.mkdtemp(prefix="recon_run_"))
    try:
        stmt_path, stmt_name = _resolve_input("statement", statement, tmpdir, True)
        bills_path, bills_name = _resolve_input("bills", bills, tmpdir, True)
        rnote_path, rnote_name = _resolve_input("rnote", rnote, tmpdir, False)
        crn_path, crn_name = _resolve_input("crn", crn, tmpdir, False)
        cfg = ReconConfig(
            statement_pdf=stmt_path,
            bill_status=bills_path,
            rnote=rnote_path,
            crn=crn_path,
            output_xlsx=tmpdir / "Recon_Output.xlsx",
            window_days=window_days,
            co7_lookback_days=co7_lookback_days,
            date_tolerance_days=date_tolerance_days,
            amount_tolerance=amount_tolerance,
            allow_batched=allow_batched,
            max_batch_size=max_batch_size,
        )
        try:
            out = run(cfg, verbose=False)
        except ValueError as e:
            # the statement parse did not tie to HSBC's printed totals
            _fail(422, "BANK_SELFCHECK_FAILED", str(e))
        except HTTPException:
            raise
        except Exception as e:
            _fail(422, "PARSE_FAILED", f"{type(e).__name__}: {e}")

        write_workbook(out, cfg.output_xlsx)

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
                "selfcheck": clean(bank_selfcheck(out["bank"], cfg.statement_pdf)),
                "config": {
                    "window_days": window_days,
                    "co7_lookback_days": co7_lookback_days,
                    "date_tolerance_days": date_tolerance_days,
                    "amount_tolerance": amount_tolerance,
                    "allow_batched": allow_batched,
                    "max_batch_size": max_batch_size,
                },
                "filenames": {
                    "statement": stmt_name,
                    "bills": bills_name,
                    "rnote": rnote_name,
                    "crn": crn_name,
                },
            },
        }
        new_run(tmpdir, payload, Path(cfg.output_xlsx), frames={
            "bank": out["bank_all"],
            "bills": out["bills"],
            # the Bills + lineage tab shows attempts combined per bill;
            # the per-attempt data is embedded in each row's Attempts list
            "bills_enriched": out["bills_grouped"],
            "recoveries": out["recoveries"],
        })
        return payload
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


@router.get("/runs/{run_id}")
def read_run(run_id: str):
    rec = get_run(run_id)
    if rec is None:
        _fail(404, "RUN_NOT_FOUND", f"no run {run_id}")
    return rec.payload


@router.get("/runs/{run_id}/frames/{name}")
def read_frame(run_id: str, name: str):
    """One source frame of a completed run, serialized on first request."""
    rec = get_run(run_id)
    if rec is None:
        _fail(404, "RUN_NOT_FOUND", f"no run {run_id}")
    if name not in rec.frames:
        _fail(404, "FRAME_NOT_FOUND",
              f"unknown frame '{name}'; have: {sorted(rec.frames)}")
    if name not in rec.frame_cache:
        rec.frame_cache[name] = df_to_records(rec.frames[name])
    rows = rec.frame_cache[name]
    return {"name": name, "count": len(rows), "rows": rows}


@router.get("/runs/{run_id}/workbook")
def download_workbook(run_id: str):
    rec = get_run(run_id)
    if rec is None or not rec.workbook_path.exists():
        _fail(404, "RUN_NOT_FOUND", f"no workbook for run {run_id}")
    stem = Path(rec.payload["meta"]["filenames"]["statement"] or "statement").stem
    safe = re.sub(r"[^\w. -]", "_", stem)[:60]
    return FileResponse(
        rec.workbook_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Recon_{safe}.xlsx",
    )
