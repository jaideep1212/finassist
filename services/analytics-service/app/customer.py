"""The customer's own XIRR, from the real transactions of ONE holding (ISIN + holder + folio).

Input: portfolio-service's /transactions answer, plus the latest NAV from nav-service.
Sign convention (investor's point of view): money paid in is negative, money received positive.

Two kinds of XIRR:
- Unrealized: as if the units were sold TODAY at today's NAV.
    still holding  -> all transactions + units held x today's NAV
    fully sold     -> all transactions EXCEPT the final sale, + the units that sale sold
                      (and any tiny leftover) x today's NAV: "what if I had never sold?"
- Realized: what the customer actually earned. Only for a fully sold holding: all real cash
  flows including the final sale; no NAV needed.
"""
from datetime import date

from app.xirr import xirr

MIN_UNITS = 0.1     # at most this many units left = fully sold ("approximately zero")
NO_INVESTMENTS = "XIRR can't be calculated as no investments are available"
REALIZED, UNREALIZED = "realized", "unrealized"


def cash_flow_of(txn: dict) -> float:
    """The signed cash flow one transaction contributes to the XIRR."""
    kind = txn["txn_type"]
    if kind == "PUR":
        return -txn["amount"]          # full amount paid, charges included: what left the pocket
    if kind in ("RED", "DIV"):
        return txn["realized_amt"]     # what actually arrived, after STT/TDS
    if kind == "REV":
        return txn["amount"]           # a purchase undone: the money comes back
    return 0.0                         # REI: dividend reinvested, no money moved


def row(txn: dict, used_in: list[str]) -> dict:
    """One transaction as shown in the API answer, with its cash flow and where it is used."""
    return {"txn_date": txn["txn_date"], "txn_type": txn["txn_type"], "amount": txn["amount"],
            "units": txn["units"], "nav": txn["nav"], "cash_flow": round(cash_flow_of(txn), 2),
            "used_in": used_in}

def safe_xirr_pct(rows: list[dict], kind: str) -> float | None:
    """XIRR in % over the rows used for `kind`, or None if no XIRR exists for them."""
    flows = [(date.fromisoformat(r["txn_date"]), r["cash_flow"])
             for r in rows if kind in r["used_in"] and r["cash_flow"] != 0]
    try:
        return round(xirr(flows) * 100, 2)
    except ValueError:
        return None


def customer_xirr(portfolio: dict, nav: float, nav_date: date) -> tuple[dict, list[dict]]:
    """Return (customer section, transaction rows) for the API answer.

    Every row says which XIRR it is used in ("used_in"), so anyone can re-check each XIRR
    in Excel: take the rows whose used_in contains "realized" (or "unrealized").
    """
    units_held = portfolio["units_held"]
    txns = portfolio["transactions"]
    fully_sold = units_held <= MIN_UNITS
    sales = [i for i, t in enumerate(txns) if t["txn_type"] == "RED"]

    customer = {
        "holder": portfolio["holder"],
        "folio_no": portfolio["folio_no"],
        "units_held": units_held,
        "nav": nav,
        "nav_date": nav_date.isoformat(),
        "current_value": round(units_held * nav, 2),
        "total_invested": round(sum(t["amount"] for t in txns if t["txn_type"] == "PUR"), 2),
    }

    if fully_sold and not sales:
        rows = [row(t, [UNREALIZED]) for t in txns]
        return {**customer, "status": "no_investments", "message": NO_INVESTMENTS,
                "position": None, "unrealized_xirr_pct": None, "realized_xirr_pct": None}, rows

    if not fully_sold:
        # Still holding: Unrealized only, valued at today's NAV.
        rows = [row(t, [UNREALIZED]) for t in txns]
        value_units = units_held
        position, exit_date = "holding", None
    else:
        # Fully sold: the final sale counts for Realized, but not for Unrealized, where its
        # units are instead valued at today's NAV as if they had never been sold.
        last_sale = sales[-1]
        rows = [row(t, [REALIZED] if i == last_sale else [REALIZED, UNREALIZED])
                for i, t in enumerate(txns)]
        value_units = units_held + txns[last_sale]["units"]
        position, exit_date = "exited", txns[last_sale]["txn_date"]

    value = value_units * nav
    rows.append({"txn_date": nav_date.isoformat(), "txn_type": "VALUE", "amount": round(value, 2),
                 "units": value_units, "nav": nav, "cash_flow": round(value, 2),
                 "used_in": [UNREALIZED]})

    unrealized = safe_xirr_pct(rows, UNREALIZED)
    realized = safe_xirr_pct(rows, REALIZED) if fully_sold else None
    result = {**customer, "position": position, "exit_date": exit_date,
              "unrealized_value": round(value, 2),
              "unrealized_xirr_pct": unrealized, "realized_xirr_pct": realized}

    if unrealized is None and realized is None:
        return {**result, "status": "not_calculable",
                "message": "no XIRR exists for these cash flows"}, rows
    return {**result, "status": "ok"}, rows

