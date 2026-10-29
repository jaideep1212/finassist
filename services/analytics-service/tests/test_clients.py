"""Tests for the calls to nav-service and portfolio-service. Fake services answer; no network."""

import httpx

from app import clients

ISIN = "INF846K01768"


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


# ------------------------------------------------------------------ portfolio-service


def test_several_holders_becomes_holder_required(monkeypatch):
    fake_services(monkeypatch, answer(400, {"detail": {"message": "has 2 holders",
                                                       "holders": ["User1", "User2"]}}))
    result = clients.fetch_holding(ISIN, None, None)
    assert result["status"] == "holder_required"
    assert result["holders"] == ["User1", "User2"]


def test_portfolio_service_unreachable_becomes_unavailable(monkeypatch):
    def down(request):
        raise httpx.ReadTimeout("too slow")
    fake_services(monkeypatch, down)
    assert clients.fetch_holding(ISIN, None, None)["status"] == "unavailable"
