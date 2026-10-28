"""analytics-service: XIRR calculations. Stateless; owns no data.

One endpoint answers everything the screen needs for one fund (and one holding):
- customer:     the customer's own XIRR from real transactions (method 1)
- fund:         the fund's SIP returns over 1Y/3Y/5Y/10Y/since inception (method 2)
- transactions: the customer's transactions with the cash flow each one contributed
"""
import os
import re
import socket

from fastapi import FastAPI, HTTPException

from app import clients
from app.customer import customer_xirr
from app.periods import fund_sip_returns

SERVICE = "analytics-service"
POD_NAME = socket.gethostname()

# An Indian ISIN: "IN", then 10 letters or digits. Same rule as the other services.
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")

app = FastAPI(title="FinAssist analytics-service", version="0.1.0")


@app.get("/healthz")
def healthz():
    """Liveness. analytics-service has no database, so there is nothing else to check."""
    return {"status": "ok", "service": SERVICE, "pod": POD_NAME}


def validate_isin(raw: str) -> str:
    """Return the ISIN in upper case, or answer 400. Runs BEFORE the ISIN goes into any URL."""
    isin = raw.strip().upper()
    if not ISIN_PATTERN.match(isin):
        raise HTTPException(status_code=400,
                            detail=f"'{raw}' is not a valid ISIN (expected IN + 10 letters/digits)")
    return isin


@app.get("/api/v1/xirr")
def xirr_for_fund(isin: str, holder: str | None = None, folio: str | None = None):
    """Customer XIRR, fund SIP returns and transactions for one fund (and one holding).

    holder and folio follow portfolio-service's rule: needed only when there is a choice.
    Only an invalid ISIN or a nav-service failure fails the whole request; anything wrong on
    the customer side is reported in customer.status while the fund part is still answered.
    """
    clean_isin = validate_isin(isin)

    # 1. NAVs: both methods need them, so a failure here stops everything (raises 404/502/503).
    scheme_name, history = clients.fetch_nav_history(clean_isin)
    if not history:
        raise HTTPException(status_code=404, detail=f"No NAV data found for ISIN {clean_isin}")
    nav_date, nav = history[-1]                  # the latest NAV = last point of the history

    # 2. Method 2: the fund's SIP returns. Needs only the history.
    fund = fund_sip_returns(history)

    # 3. Method 1: the customer's XIRR. A problem here only affects the customer section.
    holding = clients.fetch_holding(clean_isin, holder, folio)
    if holding["status"] == "ok":
        customer, transactions = customer_xirr(holding["data"], nav, nav_date)
    else:

        customer, transactions = holding, []

    return {
        "isin": clean_isin,
        "scheme_name": scheme_name,
        "nav": nav,
        "nav_date": nav_date.isoformat(),
        "customer": customer,
        "fund": fund,
        "transactions": transactions,
        "served_by": POD_NAME,
    }
