"""Tests for GET /api/v1/xirr. Fake nav-service and portfolio-service answer; no network."""
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients, main

ISIN = "INF846K01768"
URL = "/api/v1/xirr"


def nav_history(first=date(2020, 1, 1), last=date(2026, 10, 27)):
    """nav-service's /history answer: a NAV on every weekday, growing 10 % a year."""
    points, current = [], first
    while current <= last:
        if current.weekday() < 5:
            years = (current - first).days / 365
            points.append({"date": current.isoformat(), "nav": round(100 * 1.1 ** years, 4)})
        current += timedelta(days=1)
    return {"isin": ISIN, "scheme_name": "Some Fund", "count": len(points), "points": points}


HOLDING = {
    "isin": ISIN, "holder": "User1", "folio_no": "9024151391/0", "units_held": 10.0, "count": 1,
    "transactions": [{"txn_id": 1, "txn_date": "2024-01-10", "txn_type": "PUR", "amount": 1000.0,
                      "realized_amt": 999.95, "nav": 100.0, "units": 10.0,
                      "stt": 0.0, "tds": 0.0, "stamp_duty": 0.05}],
}


@pytest.fixture
def services(monkeypatch):
    """Fake nav-service and portfolio-service. Change `answers` in a test to change behaviour."""
    answers = {"nav.test": (200, nav_history()), "portfolio.test": (200, HOLDING)}
    calls = []

    def handler(request):
        calls.append(request)
        answer = answers[request.url.host]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)

    monkeypatch.setattr(clients, "http_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return answers, calls


@pytest.fixture
def client():
    return TestClient(main.app)


# ----------------------------------- the normal case ---------------------------------
def test_full_answer_has_customer_fund_and_transactions(client, services):
    response = client.get(URL, params={"isin": ISIN, "holder": "User1", "folio": "9024151391/0"})
    assert response.status_code == 200
    body = response.json()
    assert body["isin"] == ISIN and body["scheme_name"] == "Some Fund"
    assert body["nav_date"] == "2026-10-27"

    assert body["customer"]["status"] == "ok"
    assert body["customer"]["folio_no"] == "9024151391/0"
    assert body["customer"]["position"] == "holding"
    assert body["customer"]["unrealized_xirr_pct"] is not None

    assert body["fund"]["status"] == "ok"
    assert [r["period"] for r in body["fund"]["results"]] == ["1Y", "3Y", "5Y", "Since inception"]

    assert [t["txn_type"] for t in body["transactions"]] == ["PUR", "VALUE"]


# --------------------------------- customer problems: still 200 ---------------------------------


def test_portfolio_service_down_still_returns_the_fund_part(client, services):
    answers, _ = services
    answers["portfolio.test"] = httpx.ConnectError("connection refused")
    response = client.get(URL, params={"isin": ISIN})
    assert response.status_code == 200
    assert response.json()["customer"]["status"] == "unavailable"
    assert response.json()["fund"]["status"] == "ok"
