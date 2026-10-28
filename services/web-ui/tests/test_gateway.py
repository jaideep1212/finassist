"""Tests for the web-ui gateway. Fake backend services answer; no network."""
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

SERVICE_DIR = Path(__file__).resolve().parents[1]


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
    ("/api/v1/amcs", "portfolio.test", "/api/v1/amcs"),
    ("/api/v1/schemes?amc=Axis", "portfolio.test", "/api/v1/schemes"),
    ("/api/v1/isins/INF846K01768/holders", "portfolio.test", "/api/v1/isins/INF846K01768/holders"),
    ("/api/v1/xirr?isin=INF846K01768", "analytics.test", "/api/v1/xirr"),
])
def test_each_call_goes_to_the_right_service(client, backends, url, host, path):
    _, calls = backends
    response = client.get(url)
    assert response.status_code == 200
    assert calls[0].url.host == host
    assert calls[0].url.path == path


def test_parameters_are_passed_on_encoded(client, backends):
    _, calls = backends
    client.get("/api/v1/xirr", params={"isin": "INF846K01768", "holder": "User1",
                                       "folio": "9024151391/0"})
    assert dict(calls[0].url.params) == {"isin": "INF846K01768", "holder": "User1",
                                         "folio": "9024151391/0"}


def test_amc_with_special_characters_is_passed_on(client, backends):
    _, calls = backends
    client.get("/api/v1/schemes", params={"amc": "Kotak Mahindra & Co / AMC"})
    assert calls[0].url.params["amc"] == "Kotak Mahindra & Co / AMC"


def test_empty_parameters_are_not_sent(client, backends):
    _, calls = backends
    client.get("/api/v1/xirr", params={"isin": "INF846K01768", "holder": "", "folio": ""})
    assert dict(calls[0].url.params) == {"isin": "INF846K01768"}


# ------------------------------------------------------------------ answers passed on
def test_backend_answer_is_passed_on_unchanged(client, backends):
    answers, _ = backends
    answers["analytics.test"] = (200, {"customer": {"status": "ok"}, "fund": {"results": []}})
    assert client.get("/api/v1/xirr?isin=INF846K01768").json() == {
        "customer": {"status": "ok"}, "fund": {"results": []}}


@pytest.mark.parametrize("status", [400, 404])
def test_backend_errors_are_passed_on_with_their_details(client, backends, status):
    answers, _ = backends
    answers["portfolio.test"] = (status, {"detail": "No funds found for AMC X"})
    response = client.get("/api/v1/schemes?amc=X")
    assert response.status_code == status
    assert response.json()["detail"] == "No funds found for AMC X"


def test_backend_unreachable_gives_503(client, backends):
    answers, _ = backends
    answers["analytics.test"] = httpx.ConnectError("connection refused")
    response = client.get("/api/v1/xirr?isin=INF846K01768")
    assert response.status_code == 503
    assert "connection refused" not in response.text


def test_backend_sending_html_gives_502(client, backends):
    answers, _ = backends
    answers["portfolio.test"] = (500, "<html>Internal Server Error</html>")
    assert client.get("/api/v1/amcs").status_code == 502


# ------------------------------------------------------------------ safety
@pytest.mark.parametrize("bad", ["ABC", "..%2F..%2Fadmin", "INF846K0176"])
def test_invalid_isin_never_reaches_a_backend(client, backends, bad):
    _, calls = backends
    assert client.get(f"/api/v1/isins/{bad}/holders").status_code in (400, 404)
    assert calls == []


@pytest.mark.parametrize("url", [
    "/api/v1/isins/INF846K01768/transactions",     # exists in portfolio-service, not allowlisted
    "/api/v1/nav/INF846K01768",                    # nav-service is not reachable at all
    "/api/v1/anything",
])
def test_calls_outside_the_allowlist_are_not_forwarded(client, backends, url):
    _, calls = backends
    assert client.get(url).status_code == 404
    assert calls == []


def test_healthz(client):
    assert client.get("/healthz").json()["service"] == "web-ui"


def test_refuses_to_start_without_service_urls(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "ANALYTICS_SERVICE_URL"}
    env["PYTHONPATH"] = str(SERVICE_DIR)
    result = subprocess.run([sys.executable, "-c", "import app.main"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "ANALYTICS_SERVICE_URL is not set" in result.stderr
