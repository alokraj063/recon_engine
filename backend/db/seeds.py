"""
Idempotent defaults so a blank database serves today's single-customer
flow unchanged: customer 'default' wired to the HSBC/IREPS adapters with
the stock rule set.
"""

from sqlalchemy import select

from .models import Customer, MatchRuleSetRow, SourceConfig

DEFAULT_CUSTOMER_KEY = "default"

# The historical IREPS/railway advisory text, verbatim. Engine defaults
# (recon.engine.DEFAULT_COPY) are SOURCE-NEUTRAL since 2026-09-01; this
# tenant-flavoured wording belongs to the seeded default customer as its
# copy_overrides (migration a91b7c3e5d20 backfills pre-existing rows;
# the code-rename migration rewrites stored keys to the current codes).
RAILWAY_COPY = {
    "gap_type": {
        "SIGNAL_BILL_NOT_FOUND":
            "Zone identified from narrative but no bill in the export. "
            "Check whether the bill sits under a different IREPS module "
            "or a later export.",
        "UNRECOGNISED_RECEIPT":
            "No railway zone in the narrative. Likely intercompany, "
            "metro, customs or a direct customer receipt. Route to the "
            "relevant sub-ledger.",
    },
    "expected_basis": {
        "ADVICE_DATE":
            "IREPS advised the bank but no credit landed. Chase the "
            "railway or check the next statement.",
        "PAYMENT_ORDER_NO_ADVICE":
            "CO7 raised, advice not yet issued. Expected to settle in a "
            "later statement, monitor only.",
    },
    "labels": {
        "SIGNAL_BILL_NOT_FOUND": "Zone found, bill missing",
        "UNRECOGNISED_RECEIPT": "Non-IREPS receipt",
        "PAYMENT_ORDER_NO_ADVICE": "CO7 issued, no advice",
    },
}

# (slot source_type, role, adapter_key, params) — lineage is 0..N slots
# per customer; these two are the seeded IREPS pair.
DEFAULT_SOURCES = [
    ("bank_statement", "bank_statement", "hsbc", {}),
    ("bill_status", "bill_status", "ireps", {}),
    ("lineage_rnote", "lineage", "ireps_rnote", {"sheet": 0}),
    ("lineage_crn", "lineage", "ireps_crn", {"sheet": 0}),
]


def seed_defaults(session):
    customer = session.execute(
        select(Customer).where(Customer.key == DEFAULT_CUSTOMER_KEY)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(key=DEFAULT_CUSTOMER_KEY, name="Wabtec")
        session.add(customer)
        session.flush()

    for source_type, role, adapter_key, params in DEFAULT_SOURCES:
        exists = session.execute(
            select(SourceConfig).where(SourceConfig.customer_id == customer.id,
                                       SourceConfig.source_type == source_type)
        ).scalar_one_or_none()
        if exists is None:
            session.add(SourceConfig(customer_id=customer.id,
                                     source_type=source_type,
                                     role=role,
                                     adapter_key=adapter_key,
                                     params=params))
        elif exists.role is None:
            exists.role = role

    rules = session.execute(
        select(MatchRuleSetRow).where(MatchRuleSetRow.customer_id == customer.id,
                                      MatchRuleSetRow.is_default.is_(True))
    ).scalar_one_or_none()
    if rules is None:
        session.add(MatchRuleSetRow(
            customer_id=customer.id,
            name="default",
            is_default=True,
            paid_statuses=["PAYMENT MADE", "CO7 DONE"],
            weights={"advice_date": 4, "zone": 2, "co7_date": 1},
            copy_overrides=RAILWAY_COPY,
        ))
    return customer
