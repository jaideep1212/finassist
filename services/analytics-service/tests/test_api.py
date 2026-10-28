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


def test_latest_nav_is_the_last_history_point(client, services):
    body = client.get(URL, params={"isin": ISIN}).json()
    assert body["nav"] == nav_history()["points"][-1]["nav"]
    assert body["customer"]["nav"] == body["nav"]


def test_one_call_to_each_service(client, services):
    _, calls = services
    client.get(URL, params={"isin": ISIN})
    assert sorted(c.url.host for c in calls) == ["nav.test", "portfolio.test"]


def test_holder_and_folio_are_passed_on(client, services):
    _, calls = services
    client.get(URL, params={"isin": ISIN, "holder": "User1", "folio": "9024151391/0"})
    portfolio_call = next(c for c in calls if c.url.host == "portfolio.test")
    assert portfolio_call.url.params["holder"] == "User1"
    assert portfolio_call.url.params["folio"] == "9024151391/0"


# --------------------------------- customer problems: still 200 ---------------------------------
def test_holder_required_still_returns_the_fund_part(client, services):
    answers, _ = services
    answers["portfolio.test"] = (400, {"detail": {"message": "2 holders", "holders": ["User1", "User2"]}})
    response = client.get(URL, params={"isin": ISIN})
    assert response.status_code == 200
    body = response.json()
    assert body["customer"]["status"] == "holder_required"
    assert body["customer"]["holders"] == ["User1", "User2"]
    assert body["fund"]["status"] == "ok"
    assert body["transactions"] == []


def test_portfolio_service_down_still_returns_the_fund_part(client, services):
    answers, _ = services
    answers["portfolio.test"] = httpx.ConnectError("connection refused")
    response = client.get(URL, params={"isin": ISIN})
    assert response.status_code == 200
    assert response.json()["customer"]["status"] == "unavailable"
    assert response.json()["fund"]["status"] == "ok"


def test_no_units_left_gives_no_investments_with_transactions(client, services):
    answers, _ = services
    answers["portfolio.test"] = (200, {**HOLDING, "units_held": 0.0})
    body = client.get(URL, params={"isin": ISIN}).json()
    assert body["customer"]["status"] == "no_investments"
    assert body["customer"]["message"] == "XIRR can't be calculated as no investments are available"
    assert [t["txn_type"] for t in body["transactions"]] == ["PUR"]   # still shown


# --------------------------------- whole request fails ---------------------------------
def test_invalid_isin_gives_400_without_calling_anyone(client, services):
    _, calls = services
    assert client.get(URL, params={"isin": "ABC"}).status_code == 400
    assert calls == []


def test_missing_isin_is_rejected(client, services):
    assert client.get(URL).status_code in (400, 422)


def test_nav_service_down_gives_503(client, services):
    answers, _ = services
    answers["nav.test"] = httpx.ConnectError("connection refused")
    assert client.get(URL, params={"isin": ISIN}).status_code == 503


def test_unknown_fund_at_nav_service_gives_404(client, services):
    answers, _ = services
    answers["nav.test"] = (404, {"detail": "No NAV found"})
    assert client.get(URL, params={"isin": ISIN}).status_code == 404


def test_empty_history_gives_404(client, services):
    answers, _ = services
    answers["nav.test"] = (200, {**nav_history(), "points": [], "count": 0})
    assert client.get(URL, params={"isin": ISIN}).status_code == 404


def test_healthz(client):
    assert client.get("/healthz").json()["service"] == "analytics-service"
