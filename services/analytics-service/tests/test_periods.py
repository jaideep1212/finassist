"""Tests for the period rule (1Y/3Y/5Y/10Y/since inception). Pure calculation: no network."""
from datetime import date, timedelta

import pytest
from app.periods import fund_sip_returns, years_before
from app.sip import SIP_DAY, SIP_MONTHLY_AMOUNT

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
    (12.0, ["1Y", "3Y", "5Y", "10Y", "Since inception"]),
    (3.5,  ["1Y", "3Y", "Since inception"]),
    (1.7,  ["1Y", "Since inception"]),
    (0.6,  ["Since inception"]),
])
def test_periods_depend_on_fund_age(age_years, expected):
    first = LATEST - timedelta(days=round(age_years * 365.25))
    assert periods(fund_sip_returns(growing_fund(first))) == expected


def test_fund_exactly_three_years_old_gets_3y_and_since_inception():
    first = years_before(LATEST, 3)
    assert periods(fund_sip_returns(growing_fund(first))) == ["1Y", "3Y", "Since inception"]


# ----------------------------------- numbers -------------------------------
def test_steady_10_percent_fund_gives_10_percent_xirr_in_every_period():
    """If the NAV grows exactly 10 % a year, every SIP instalment earns exactly 10 % a year."""
    answer = fund_sip_returns(growing_fund(date(2014, 1, 1)))
    for row in answer["results"]:
        assert row["return_type"] == "XIRR"
        assert row["return_pct"] == pytest.approx(10.0, abs=0.01), row["period"]


def test_one_year_row_details():
    row = fund_sip_returns(growing_fund(date(2020, 1, 1)))["results"][0]
    assert row["period"] == "1Y"
    assert row["from"] == "2025-10-27"
    assert row["first_instalment"] == "2025-11-03"     # 1 Nov 2025 is a Saturday -> Monday
    assert row["instalments"] == 12
    assert row["invested"] == 12 * SIP_MONTHLY_AMOUNT


def test_fund_under_a_year_gets_absolute_return_not_xirr():
    first = LATEST - timedelta(days=200)
    row = fund_sip_returns(growing_fund(first))["results"][0]
    assert row["period"] == "Since inception"
    assert row["return_type"] == "absolute"
    expected = (row["current_value"] / row["invested"] - 1) * 100
    assert row["return_pct"] == pytest.approx(expected, abs=0.01)


def test_answer_header():
    answer = fund_sip_returns(growing_fund(date(2023, 4, 17)))
    assert answer["status"] == "ok"
    assert answer["inception_date"] == "2023-04-17"
    assert answer["nav_date"] == "2026-10-27"
    assert answer["fund_age_years"] == pytest.approx(3.53, abs=0.01)
    assert answer["monthly_amount"] == SIP_MONTHLY_AMOUNT and answer["sip_day"] == SIP_DAY


# ------------------------------------- too little data -------------------------------
def test_less_than_two_instalments_is_not_enough_history():
    history = growing_fund(date(2026, 9, 20))          # only the 1 October instalment
    answer = fund_sip_returns(history)
    assert answer["status"] == "not_enough_history"
    assert answer["results"] == []


def test_empty_history_is_not_enough_history():
    assert fund_sip_returns([])["status"] == "not_enough_history"


# ------------------------------------ helper -------------------------------
@pytest.mark.parametrize("day, years, expected", [
    (date(2026, 10, 27), 3, date(2023, 10, 27)),
    (date(2024, 2, 29), 1, date(2023, 2, 28)),         # leap day -> 28 February
])
def test_years_before(day, years, expected):
    assert years_before(day, years) == expected
