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
def test_first_instalment_is_next_sip_day_on_or_after_start():
    assert monthly_dates(date(2025, 10, 27), date(2026, 1, 15)) == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]


def test_start_on_the_sip_day_itself_counts():
    assert monthly_dates(date(2025, 11, 1), date(2025, 12, 1))[0] == date(2025, 11, 1)


def test_one_year_back_gives_twelve_instalments():
    assert len(monthly_dates(date(2025, 10, 27), date(2026, 10, 27))) == 12


def test_schedule_crosses_the_year_end():
    dates = monthly_dates(date(2025, 11, 15), date(2026, 3, 1), day=15)
    assert dates == [date(2025, 11, 15), date(2025, 12, 15), date(2026, 1, 15), date(2026, 2, 15)]


@pytest.mark.parametrize("day", [0, 29, 31])
def test_sip_day_must_exist_in_every_month(day):
    with pytest.raises(ValueError):
        monthly_dates(date(2025, 1, 1), date(2025, 12, 31), day=day)


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


def test_units_invested_and_final_value():
    history = [(date(2026, 7, 1), 100.0), (date(2026, 8, 3), 80.0),
               (date(2026, 9, 1), 125.0), (date(2026, 10, 27), 120.0)]
    result = simulate_sip(history, start=date(2026, 7, 1), amount=10000)
    assert [i.bought_on for i in result.instalments] == [
        date(2026, 7, 1), date(2026, 8, 3), date(2026, 9, 1), date(2026, 10, 27)]
    # October's instalment is bought on the 27th: the next NAV after 1 October.
    assert result.invested == 40000
    assert result.units == pytest.approx(100 + 125 + 80 + 10000 / 120)
    assert result.final_value == pytest.approx(result.units * 120)
    assert result.nav_date == date(2026, 10, 27)


def test_cash_flows_are_payments_then_final_value():
    history = trading_days(date(2025, 1, 1), date(2025, 12, 31))
    result = simulate_sip(history, start=date(2025, 1, 1))
    flows = result.cash_flows()
    assert all(amount == -SIP_MONTHLY_AMOUNT for _, amount in flows[:-1])
    assert flows[-1] == (result.nav_date, result.final_value)


def test_flat_nav_gives_zero_xirr():
    """NAV never moves -> the fund earned nothing -> XIRR must be 0."""
    history = trading_days(date(2024, 1, 1), date(2025, 12, 31))
    result = simulate_sip(history, start=date(2024, 1, 1))
    assert result.final_value == pytest.approx(result.invested)
    assert xirr(result.cash_flows()) == pytest.approx(0.0, abs=1e-9)


def test_start_after_latest_nav_gives_no_instalments():
    history = [(date(2026, 1, 5), 100.0)]
    result = simulate_sip(history, start=date(2026, 2, 1))
    assert result.instalments == [] and result.invested == 0


def test_empty_history_is_an_error():
    with pytest.raises(ValueError):
        simulate_sip([], start=date(2026, 1, 1))
