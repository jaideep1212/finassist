"""Tests for the simulated SIP. Pure calculation: no web, no network."""
from datetime import date, timedelta

import pytest

from app.sip import SIP_MONTHLY_AMOUNT, monthly_dates, simulate_sip
from app.xirr import xirr


def trading_days(start: date, end: date, nav: float = 100.0):
    """A NAV on every weekday (no weekends), all at the same value."""
    days, current = [], start
    while current <= end:
        if current.weekday() < 5:
            days.append((current, nav))
        current += timedelta(days=1)
    return days


# -------------------------------- the schedule ----------------------------------


def test_one_year_back_gives_twelve_instalments():
    assert len(monthly_dates(date(2025, 10, 27), date(2026, 10, 27))) == 12


# -------------------------------------- buying --------------------------------------
def test_holiday_uses_the_next_available_nav():
    history = [(date(2026, 2, 27), 90.0),     # Friday
               (date(2026, 3, 2), 100.0),     # Monday: 1 March 2026 is a Sunday
               (date(2026, 3, 3), 110.0)]
    result = simulate_sip(history, start=date(2026, 2, 28))
    first = result.instalments[0]
    assert first.scheduled == date(2026, 3, 1)
    assert first.bought_on == date(2026, 3, 2)
    assert first.nav == 100.0
    assert first.units == pytest.approx(SIP_MONTHLY_AMOUNT / 100.0)


def test_flat_nav_gives_zero_xirr():
    """NAV never moves -> the fund earned nothing -> XIRR must be 0."""
    history = trading_days(date(2024, 1, 1), date(2025, 12, 31))
    result = simulate_sip(history, start=date(2024, 1, 1))
    assert result.final_value == pytest.approx(result.invested)
    assert xirr(result.cash_flows()) == pytest.approx(0.0, abs=1e-9)
