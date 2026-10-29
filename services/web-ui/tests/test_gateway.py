"""Tests for the web-ui gateway. Fake backend services answer; no network."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def backends(monkeypatch):
    """Fake portfolio-service and analytics-service. Returns (answers, recorded requests)."""
    answers = {"portfolio.test": (200, {"from": "portfolio"}),
               "analytics.test": (200, {"from": "analytics"})}
    calls = []

    def handler(request):
        calls.append(request)
        answer = answers[request.url.host]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(main, "http_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return answers, calls


@pytest.fixture
def client():
    return TestClient(main.app)


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("url, host, path", [
    ("/api/v1/xirr?isin=INF846K01768", "analytics.test", "/api/v1/xirr"),
])
def test_each_call_goes_to_the_right_service(client, backends, url, host, path):
    _, calls = backends
    response = client.get(url)
    assert response.status_code == 200
    assert calls[0].url.host == host
    assert calls[0].url.path == path


# ------------------------------------------------------------------ answers passed on


@pytest.mark.parametrize("status", [404])
def test_backend_errors_are_passed_on_with_their_details(client, backends, status):
    answers, _ = backends
    answers["portfolio.test"] = (status, {"detail": "No funds found for AMC X"})
    response = client.get("/api/v1/schemes?amc=X")
    assert response.status_code == status
    assert response.json()["detail"] == "No funds found for AMC X"


# ------------------------------------------------------------------ safety
@pytest.mark.parametrize("bad", ["..%2F..%2Fadmin"])
def test_invalid_isin_never_reaches_a_backend(client, backends, bad):
    _, calls = backends
    assert client.get(f"/api/v1/isins/{bad}/holders").status_code in (400, 404)
    assert calls == []


@pytest.mark.parametrize("url", [
    "/api/v1/isins/INF846K01768/transactions",     # exists in portfolio-service, not allowlisted
])
def test_calls_outside_the_allowlist_are_not_forwarded(client, backends, url):
    _, calls = backends
    assert client.get(url).status_code == 404
    assert calls == []
