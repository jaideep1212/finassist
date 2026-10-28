"""XIRR: the yearly return rate of cash flows that happen on different dates.

Sign convention (investor's point of view):
    money paid IN  (a SIP instalment, a purchase)       -> negative
    money received (a redemption, dividend, final value) -> positive

XIRR is the rate r at which the "net present value" of all cash flows is zero:

    sum( amount_i / (1 + r) ** years_i ) = 0

where years_i = days since the first cash flow / 365 (the same convention as Excel's XIRR).
There is no formula that solves this for r, so we search for it numerically.
"""
from datetime import date

DAYS_PER_YEAR = 365.0
LOWEST_RATE = -0.9999     # -99.99 %: (1 + r) must stay above 0
HIGHEST_RATE = 100.0      # +10,000 % a year: far beyond any real fund
TOLERANCE = 1e-10         # stop when the rate is known to 10 decimal places
MAX_STEPS = 200


def net_present_value(rate: float, flows: list[tuple[float, float]]) -> float:
    """Value of all (years, amount) cash flows, discounted to the first date at `rate`."""
    return sum(amount / (1.0 + rate) ** years for years, amount in flows)


def xirr(cash_flows: list[tuple[date, float]]) -> float:
    """Return the XIRR as a fraction (0.1234 = 12.34 % a year).

    Raises ValueError if the cash flows cannot have an XIRR.
    """
    if len(cash_flows) < 2:
        raise ValueError("XIRR needs at least two cash flows")
    if not any(amount < 0 for _, amount in cash_flows) or not any(amount > 0 for _, amount in cash_flows):
        raise ValueError("XIRR needs at least one payment (negative) and one receipt (positive)")

    first_day = min(day for day, _ in cash_flows)
    flows = [((day - first_day).days / DAYS_PER_YEAR, amount) for day, amount in cash_flows]

    # Bisection: if the value is positive at one rate and negative at another, the answer lies
    # between them. Halve that range again and again until it is tiny.
    # For investor cash flows (pay first, receive later) the value falls as the rate rises,
    # so there is exactly one answer, and bisection always finds it.
    low, high = LOWEST_RATE, HIGHEST_RATE
    value_low = net_present_value(low, flows)
    value_high = net_present_value(high, flows)
    if value_low * value_high > 0:                      # same sign at both ends: no answer in range
        raise ValueError("no XIRR between -99.99 % and +10,000 % a year")

    for _ in range(MAX_STEPS):
        middle = (low + high) / 2
        value_middle = net_present_value(middle, flows)
        if value_middle == 0 or (high - low) / 2 < TOLERANCE:
            return middle
        if (value_middle > 0) == (value_low > 0):        # answer is in the upper half
            low, value_low = middle, value_middle
        else:                                            # answer is in the lower half
            high = middle
    return (low + high) / 2
