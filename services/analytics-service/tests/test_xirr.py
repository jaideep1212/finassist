"""Tests for the XIRR calculation. Pure maths: no web, no network, no database."""
from datetime import date

import pytest

from app.xirr import net_present_value, xirr


def test_matches_excel_documentation_example():
    """The example from Microsoft's XIRR documentation; Excel answers 0.373362535 (37.34 %)."""
    flows = [
        (date(2008, 1, 1), -10000),
        (date(2008, 3, 1), 2750),
        (date(2008, 10, 30), 4250),
        (date(2009, 2, 15), 3250),
        (date(2009, 4, 1), 2750),
    ]
    assert xirr(flows) == pytest.approx(0.373362535, abs=1e-8)


def test_exactly_ten_percent_in_one_year():
    flows = [(date(2023, 1, 1), -1000), (date(2024, 1, 1), 1100)]   # 365 days
    assert xirr(flows) == pytest.approx(0.10, abs=1e-9)


def test_loss_gives_negative_rate():
    flows = [(date(2023, 1, 1), -1000), (date(2024, 1, 1), 800)]
    assert xirr(flows) == pytest.approx(-0.20, abs=1e-9)


def test_no_gain_gives_zero():
    flows = [(date(2023, 1, 1), -500), (date(2023, 6, 1), -500), (date(2024, 1, 1), 1000)]
    assert xirr(flows) == pytest.approx(0.0, abs=1e-9)


def test_monthly_sip_answer_really_makes_value_zero():
    """Whatever the answer, putting it back into the formula must give (almost) zero."""
    flows = [(date(2024, m, 1), -10000) for m in range(1, 13)] + [(date(2025, 1, 1), 130000)]
    rate = xirr(flows)
    years = [((d - date(2024, 1, 1)).days / 365, a) for d, a in flows]
    assert abs(net_present_value(rate, years)) < 1e-4
    assert 0.14 < rate < 0.16                          # ~15 % for a 10,000 gain on 120,000 in a year


def test_order_of_cash_flows_does_not_matter():
    flows = [(date(2023, 1, 1), -1000), (date(2023, 7, 1), -1000), (date(2024, 1, 1), 2300)]
    assert xirr(list(reversed(flows))) == pytest.approx(xirr(flows), abs=1e-12)


@pytest.mark.parametrize("flows", [
    [],                                                             # nothing
    [(date(2023, 1, 1), -1000)],                                    # only one flow
    [(date(2023, 1, 1), -1000), (date(2024, 1, 1), -50)],           # only payments
    [(date(2023, 1, 1), 1000), (date(2024, 1, 1), 50)],             # only receipts
])
def test_impossible_cash_flows_raise_value_error(flows):
    with pytest.raises(ValueError):
        xirr(flows)


def test_total_loss_is_out_of_range():
    flows = [(date(2023, 1, 1), -1000), (date(2024, 1, 1), 0.0000001)]
    with pytest.raises(ValueError):
        xirr(flows)
