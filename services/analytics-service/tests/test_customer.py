"""Tests for the customer's Unrealized and Realized XIRR. Pure calculation: no network."""
from datetime import date

import pytest

from app.customer import NO_INVESTMENTS, customer_xirr
from app.xirr import xirr

NAV_DATE = date(2026, 10, 27)


def txn(day, kind, amount=0.0, units=0.0, nav=0.0, realized=None):
    """One transaction in the shape portfolio-service returns."""
    return {"txn_date": day, "txn_type": kind, "amount": amount, "units": units, "nav": nav,
            "realized_amt": amount if realized is None else realized}


def holding(units_held, *transactions):
    return {"holder": "User1", "folio_no": "F-1", "units_held": units_held,
            "transactions": list(transactions)}


def flows_for(rows, kind):
    """The cash flows of the rows used for `kind`, exactly as someone would copy them to Excel."""
    return [(date.fromisoformat(r["txn_date"]), r["cash_flow"])
            for r in rows if kind in r["used_in"] and r["cash_flow"] != 0]


# ----------------------------- still holding: Unrealized only -------------------------------------
def test_still_holding_gives_unrealized_only():
    """Worked example: 2 purchases, 1 partial sale, 18 units left, today's NAV 700."""
    portfolio = holding(18,
                        txn("2024-01-10", "PUR", amount=5000, units=10, nav=500),
                        txn("2024-06-10", "PUR", amount=5000, units=12, nav=416.67),
                        txn("2025-03-03", "RED", amount=2400, units=4, nav=600))
    customer, rows = customer_xirr(portfolio, nav=700, nav_date=NAV_DATE)
    assert customer["status"] == "ok"
    assert customer["position"] == "holding"
    assert customer["unrealized_xirr_pct"] == 19.33
    assert customer["realized_xirr_pct"] is None
    assert customer["exit_date"] is None
    assert customer["current_value"] == 12600 and customer["unrealized_value"] == 12600
    assert customer["total_invested"] == 10000


# ----------------------------- fully sold: Realized + Unrealized -------------------------------------
SOLD = holding(0,
               txn("2023-01-02", "PUR", amount=1000, units=10, nav=100),
               txn("2023-07-03", "PUR", amount=1000, units=8, nav=125),
               txn("2024-01-02", "RED", amount=600, units=4, nav=150),          # partial sale
               txn("2025-01-02", "RED", amount=2800, units=14, nav=200, realized=2790))  # exit


def test_realized_uses_the_real_cash_flows_including_the_final_sale_and_no_nav():
    customer, rows = customer_xirr(SOLD, nav=250, nav_date=NAV_DATE)
    assert flows_for(rows, "realized") == [
        (date(2023, 1, 2), -1000), (date(2023, 7, 3), -1000),
        (date(2024, 1, 2), 600), (date(2025, 1, 2), 2790)]          # net amount received
    assert customer["realized_xirr_pct"] == round(xirr(flows_for(rows, "realized")) * 100, 2)


def test_unrealized_drops_the_final_sale_and_values_its_units_today():
    customer, rows = customer_xirr(SOLD, nav=250, nav_date=NAV_DATE)
    assert flows_for(rows, "unrealized") == [
        (date(2023, 1, 2), -1000), (date(2023, 7, 3), -1000),
        (date(2024, 1, 2), 600),                                     # earlier sale still counts
        (NAV_DATE, 14 * 250)]                                        # 14 units x today's NAV
    assert customer["unrealized_value"] == 3500
    assert customer["unrealized_xirr_pct"] == round(xirr(flows_for(rows, "unrealized")) * 100, 2)


# ----------------------------- nothing to calculate -------------------------------------
@pytest.mark.parametrize("portfolio", [
    holding(0.05, txn("2025-01-01", "PUR", amount=5, units=0.05, nav=100)),   # tiny, never sold
])
def test_nothing_held_and_never_sold_gives_no_investments(portfolio):
    customer, _ = customer_xirr(portfolio, nav=100, nav_date=NAV_DATE)
    assert customer["status"] == "no_investments"
    assert customer["message"] == NO_INVESTMENTS
    assert customer["unrealized_xirr_pct"] is None and customer["realized_xirr_pct"] is None
