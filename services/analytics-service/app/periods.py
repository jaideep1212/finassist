"""The fund's SIP returns over standard periods: 1Y, 3Y, 5Y, 10Y and since inception.

Rules:
- a standard period is shown only if the fund is at least that old;
- "since inception" is always shown, starting at the first NAV in the history;
- a period shorter than 1 year is NOT annualised (that would exaggerate): it gets the
  absolute return instead, clearly marked;
- fewer than 2 instalments is "not enough history".
"""
from datetime import date

from app.sip import SIP_DAY, SIP_MONTHLY_AMOUNT, simulate_sip
from app.xirr import xirr

STANDARD_YEARS = [1, 3, 5, 10]
MIN_INSTALMENTS = 2


def years_before(day: date, years: int) -> date:
    """The same calendar day `years` earlier. 29 February becomes 28 February if needed."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:                                   # 29 Feb in a non-leap year
        return day.replace(year=day.year - years, day=28)


def period_result(label: str, history: list[tuple[date, float]], start: date) -> dict | None:
    """Simulate the SIP from `start` and describe its return. None if under 2 instalments."""
    sip = simulate_sip(history, start)
    if len(sip.instalments) < MIN_INSTALMENTS:
        return None

    at_least_a_year = start <= years_before(sip.nav_date, 1)
    if at_least_a_year:
        try:
            return_pct, return_type = xirr(sip.cash_flows()) * 100, "XIRR"
        except ValueError:                               # extreme data: no XIRR in range
            return_pct, return_type = None, "XIRR"
    else:
        return_pct, return_type = (sip.final_value / sip.invested - 1) * 100, "absolute"

    return {
        "period": label,
        "from": start.isoformat(),
        "first_instalment": sip.instalments[0].bought_on.isoformat(),
        "instalments": len(sip.instalments),
        "invested": round(sip.invested, 2),
        "current_value": round(sip.final_value, 2),
        "return_pct": None if return_pct is None else round(return_pct, 2),
        "return_type": return_type,            # "XIRR" = per year; "absolute" = not annualised
    }


def fund_sip_returns(history: list[tuple[date, float]]) -> dict:
    """All period results for one fund, from its NAV history (sorted, oldest first)."""
    if not history:
        return {"status": "not_enough_history", "results": []}

    inception, latest = history[0][0], history[-1][0]
    results = []
    for years in STANDARD_YEARS:
        start = years_before(latest, years)
        if start >= inception:                           # fund is at least this old
            row = period_result(f"{years}Y", history, start)
            if row:
                results.append(row)

    since_inception = period_result("Since inception", history, inception)
    if since_inception:
        results.append(since_inception)

    return {
        "status": "ok" if results else "not_enough_history",
        "inception_date": inception.isoformat(),
        "nav_date": latest.isoformat(),
        "fund_age_years": round((latest - inception).days / 365.25, 2),
        "monthly_amount": SIP_MONTHLY_AMOUNT,
        "sip_day": SIP_DAY,
        "results": results,
    }
