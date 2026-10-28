"""Tests for the calls to nav-service and portfolio-service. Fake services answer; no network."""
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from app import clients

ISIN = "INF846K01768"
SERVICE_DIR = Path(__file__).resolve().parents[1]


def fake_services(monkeypatch, handler):
    """Replace the HTTP client with one whose answers come from `handler`. Returns the requests."""
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(clients, "http_client", httpx.Client(transport=httpx.MockTransport(recording)))
    return calls


def answer(status, body=None):
    return lambda request: httpx.Response(status, json=body if body is not None else {})


# ------------------------------------------------------------------ nav-service
NAV_HISTORY = {"isin": ISIN, "scheme_name": "Some Fund", "count": 2,
               "points": [{"date": "2026-10-26", "nav": 50.1}, {"date": "2026-10-27", "nav": 52.43}]}


def test_nav_history_is_parsed_into_dates_and_numbers(monkeypatch):
    calls = fake_services(monkeypatch, answer(200, NAV_HISTORY))
    name, history = clients.fetch_nav_history(ISIN)
    assert name == "Some Fund"
    assert history == [(date(2026, 10, 26), 50.1), (date(2026, 10, 27), 52.43)]
    assert str(calls[0].url) == f"http://nav.test/api/v1/nav/{ISIN}/history"


@pytest.mark.parametrize("status, expected", [(404, 404), (503, 503), (500, 503), (400, 503)])
def test_nav_service_errors_stop_the_request(monkeypatch, status, expected):
    fake_services(monkeypatch, answer(status, {"detail": "x"}))
    with pytest.raises(HTTPException) as error:
        clients.fetch_nav_history(ISIN)
    assert error.value.status_code == expected


def test_nav_service_unreachable_gives_503(monkeypatch):
    def down(request):
        raise httpx.ConnectError("connection refused")
    fake_services(monkeypatch, down)
    with pytest.raises(HTTPException) as error:
        clients.fetch_nav_history(ISIN)
    assert error.value.status_code == 503


def test_nav_service_sending_garbage_gives_502(monkeypatch):
    fake_services(monkeypatch, answer(200, {"points": [{"date": "yesterday", "nav": 1}]}))
    with pytest.raises(HTTPException) as error:
        clients.fetch_nav_history(ISIN)
    assert error.value.status_code == 502


# ------------------------------------------------------------------ portfolio-service
HOLDING = {"isin": ISIN, "holder": "User1", "folio_no": "9024151391/0", "units_held": 10.0,
           "count": 0, "transactions": []}


def test_holding_ok_and_parameters_are_passed_safely_encoded(monkeypatch):
    calls = fake_services(monkeypatch, answer(200, HOLDING))
    result = clients.fetch_holding(ISIN, "User1", "9024151391/0")
    assert result == {"status": "ok", "data": HOLDING}
    request = calls[0]
    assert request.url.path == f"/api/v1/isins/{ISIN}/transactions"
    assert request.url.params["holder"] == "User1"
    assert request.url.params["folio"] == "9024151391/0"     # "/" survives as a value


def test_missing_holder_and_folio_are_not_sent(monkeypatch):
    calls = fake_services(monkeypatch, answer(200, HOLDING))
    clients.fetch_holding(ISIN, None, None)
    assert "holder" not in calls[0].url.params and "folio" not in calls[0].url.params


def test_several_holders_becomes_holder_required(monkeypatch):
    fake_services(monkeypatch, answer(400, {"detail": {"message": "has 2 holders",
                                                       "holders": ["User1", "User2"]}}))
    result = clients.fetch_holding(ISIN, None, None)
    assert result["status"] == "holder_required"
    assert result["holders"] == ["User1", "User2"]


def test_several_folios_becomes_folio_required(monkeypatch):
    fake_services(monkeypatch, answer(400, {"detail": {"message": "has 2 folios",
                                                       "folios": ["F-1", "F-2"]}}))
    result = clients.fetch_holding(ISIN, "User1", None)
    assert result["status"] == "folio_required"
    assert result["folios"] == ["F-1", "F-2"]


def test_nobody_holds_the_fund_becomes_no_investments(monkeypatch):
    fake_services(monkeypatch, answer(404, {"detail": f"No investments for ISIN {ISIN}"}))
    assert clients.fetch_holding(ISIN, None, None)["status"] == "no_investments"


def test_unknown_holder_becomes_not_found_with_the_reason(monkeypatch):
    fake_services(monkeypatch, answer(404, {"detail": "holder User9 not found"}))
    result = clients.fetch_holding(ISIN, "User9", None)
    assert result == {"status": "not_found", "message": "holder User9 not found"}


@pytest.mark.parametrize("status", [500, 503])
def test_portfolio_service_errors_become_unavailable_not_an_exception(monkeypatch, status):
    fake_services(monkeypatch, answer(status, {"detail": "database not available"}))
    assert clients.fetch_holding(ISIN, None, None)["status"] == "unavailable"


def test_portfolio_service_unreachable_becomes_unavailable(monkeypatch):
    def down(request):
        raise httpx.ReadTimeout("too slow")
    fake_services(monkeypatch, down)
    assert clients.fetch_holding(ISIN, None, None)["status"] == "unavailable"


# ------------------------------------------------------------------ settings
def test_refuses_to_start_without_service_urls(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PORTFOLIO_SERVICE_URL"}
    env["PYTHONPATH"] = str(SERVICE_DIR)
    result = subprocess.run([sys.executable, "-c", "import app.clients"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "PORTFOLIO_SERVICE_URL is not set" in result.stderr
