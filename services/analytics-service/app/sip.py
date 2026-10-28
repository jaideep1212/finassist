"""Simulated SIP: "what if someone had invested a fixed amount in this fund every month?"

Uses only the fund's daily NAV history; no customer data is involved.
"""
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date

# The simulation's two assumptions. Fixed, and echoed in the API answer so readers know them.
SIP_MONTHLY_AMOUNT = 1000.0    # does not change the XIRR %; only makes rupee figures readable
SIP_DAY = 1                    # day of the month of each instalment (1-28 exists in every month)


@dataclass
class Instalment:
    scheduled: date
    bought_on: date
    nav: float
    units: float


@dataclass
class SipResult:
    instalments: list[Instalment]
    invested: float
    units: float
    nav_date: date
    final_value: float

    def cash_flows(self) -> list[tuple[date, float]]:
        """The investor's cash flows for xirr(): every instalment paid (-), final value (+)."""
        flows = [(i.bought_on, -SIP_MONTHLY_AMOUNT) for i in self.instalments]
        flows.append((self.nav_date, self.final_value))
        return flows


def monthly_dates(start: date, end: date, day: int = SIP_DAY) -> list[date]:
    """Every `day`-th of the month from `start` to `end`, both included.

    The first one is the first `day`-th ON or AFTER start:
    start 2025-10-27, day 1  ->  2025-11-01, 2025-12-01, ...
    """
    if not 1 <= day <= 28:
        raise ValueError("SIP day must be between 1 and 28")
    year, month = start.year, start.month
    if start.day > day:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    dates = []
    while (current := date(year, month, day)) <= end:
        dates.append(current)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return dates


def simulate_sip(history: list[tuple[date, float]], start: date,
                 amount: float = SIP_MONTHLY_AMOUNT, day: int = SIP_DAY) -> SipResult:
    """Invest amount every month from `start` until the latest NAV, then value all units.

    history is nav-service's series: (date, nav) pairs, sorted oldest first, with no
    entries on weekends and holidays.
    """
    if not history:
        raise ValueError("no NAV history")
    nav_dates = [d for d, _ in history]
    latest_date, latest_nav = history[-1]

    instalments = []
    for scheduled in monthly_dates(start, latest_date, day):
        # No NAV on the SIP day (weekend/holiday)? Use the next day that has one, like a real
        # SIP. bisect_left finds the first NAV date >= scheduled in the sorted list, quickly.
        position = bisect_left(nav_dates, scheduled)
        bought_on, nav = history[position]    # always exists: scheduled <= latest_date
        instalments.append(Instalment(scheduled, bought_on, nav, amount / nav))

    units = sum(i.units for i in instalments)
    return SipResult(
        instalments=instalments,
        invested=amount * len(instalments),
        units=units,
        nav_date=latest_date,
        final_value=units * latest_nav,
    )
