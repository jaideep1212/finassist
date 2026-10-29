"""Tests for the XIRR calculation. Pure maths: no web, no network, no database."""
from datetime import date

import pytest

from app.xirr import xirr


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


def test_loss_gives_negative_rate():
    flows = [(date(2023, 1, 1), -1000), (date(2024, 1, 1), 800)]
    assert xirr(flows) == pytest.approx(-0.20, abs=1e-9)


@pytest.mark.parametrize("flows", [
    [(date(2023, 1, 1), -1000), (date(2024, 1, 1), -50)],           # only payments
])
def test_impossible_cash_flows_raise_value_error(flows):
    with pytest.raises(ValueError):
        xirr(flows)
