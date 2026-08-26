"""Command line entry point: python -m recon --help"""

import argparse

from logging_setup import configure_logging

from .config import ReconConfig
from .engine import run
from .report import write_workbook


def build_parser():
    p = argparse.ArgumentParser(
        prog="recon",
        description="Reconcile an HSBC daily statement against IREPS Bill Status.",
    )
    p.add_argument("--statement", required=True, help="HSBC daily statement PDF")
    p.add_argument("--bills", required=True, help="IREPS Bill Status xlsx")
    p.add_argument("--rnote", help="RNOTE IREPS xlsx (optional)")
    p.add_argument("--crn", help="CRN IREPS xlsx (optional)")
    p.add_argument("-o", "--out", default="Recon_Output.xlsx")
    p.add_argument("--window-days", type=int, default=0,
                   help="how far either side of the statement dates a payment "
                        "advice can sit and still be expected here (default 0)")
    p.add_argument("--co7-lookback-days", type=int, default=5)
    p.add_argument("--date-tolerance-days", type=int, default=2)
    p.add_argument("--amount-tolerance", type=float, default=0.0)
    p.add_argument("--no-batched", action="store_true",
                   help="skip the subset-sum pass for credits covering several bills")
    return p


def main(argv=None):
    configure_logging()
    a = build_parser().parse_args(argv)
    cfg = ReconConfig(
        statement_pdf=a.statement,
        bill_status=a.bills,
        rnote=a.rnote,
        crn=a.crn,
        output_xlsx=a.out,
        window_days=a.window_days,
        co7_lookback_days=a.co7_lookback_days,
        date_tolerance_days=a.date_tolerance_days,
        amount_tolerance=a.amount_tolerance,
        allow_batched=not a.no_batched,
    )
    out = run(cfg)
    write_workbook(out, cfg.output_xlsx)
    print()
    print(out["summary"].to_string(index=False))
    print(f"\nwritten: {cfg.output_xlsx}")
    return out


if __name__ == "__main__":
    main()
