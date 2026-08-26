"""
Idempotent defaults so a blank database serves today's single-customer
flow unchanged: customer 'default' wired to the HSBC/IREPS adapters with
the stock rule set.
"""

from sqlalchemy import select

from .models import Customer, MatchRuleSetRow, SourceConfig

DEFAULT_CUSTOMER_KEY = "default"

DEFAULT_SOURCES = [
    ("bank_statement", "hsbc", {}),
    ("bill_status", "ireps", {}),
    ("lineage_rnote", "ireps_rnote", {"sheet": 0}),
    ("lineage_crn", "ireps_crn", {"sheet": 0}),
]


def seed_defaults(session):
    customer = session.execute(
        select(Customer).where(Customer.key == DEFAULT_CUSTOMER_KEY)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(key=DEFAULT_CUSTOMER_KEY, name="Default (HSBC / IREPS)")
        session.add(customer)
        session.flush()

    for source_type, adapter_key, params in DEFAULT_SOURCES:
        exists = session.execute(
            select(SourceConfig).where(SourceConfig.customer_id == customer.id,
                                       SourceConfig.source_type == source_type)
        ).scalar_one_or_none()
        if exists is None:
            session.add(SourceConfig(customer_id=customer.id,
                                     source_type=source_type,
                                     adapter_key=adapter_key,
                                     params=params))

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
        ))
    return customer
