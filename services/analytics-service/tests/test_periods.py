"""Tests for the period rule (1Y/3Y/5Y/10Y/since inception). Pure calculation: no network."""
from datetime import date, timedelta

import pytest
from app.periods import fund_sip_returns

LATEST = date(2026, 10, 27)


def growing_fund(first: date, last: date = LATEST, yearly_growth: float = 0.10):
    """A NAV on every weekday, growing smoothly at `yearly_growth` per 365 days."""
    days, current = [], first
    while current <= last:
        if current.weekday() < 5:
            years = (current - first).days / 365
            days.append((current, 100 * (1 + yearly_growth) ** years))
        current += timedelta(days=1)
    return days


def periods(answer):
    return [r["period"] for r in answer["results"]]


# ----------------------------------- which periods -------------------------------
@pytest.mark.parametrize("age_years, expected", [
    (3.5,  ["1Y", "3Y", "Since inception"]),
    (0.6,  ["Since inception"]),
])
def test_periods_depend_on_fund_age(age_years, expected):
    first = LATEST - timedelta(days=round(age_years * 365.25))
    assert periods(fund_sip_returns(growing_fund(first))) == expected


# ----------------------------------- numbers -------------------------------
def test_steady_10_percent_fund_gives_10_percent_xirr_in_every_period():
    """If the NAV grows exactly 10 % a year, every SIP instalment earns exactly 10 % a year."""
    answer = fund_sip_returns(growing_fund(date(2014, 1, 1)))
    for row in answer["results"]:
        assert row["return_type"] == "XIRR"
        assert row["return_pct"] == pytest.approx(10.0, abs=0.01), row["period"]
