"""Tests for nav-service. The real provider is never called: a fake one answers instead."""
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

SERVICE_DIR = Path(__file__).resolve().parents[1]

# What the provider returns for one fund (shortened history).
PROVIDER_ANSWER = {
    "ISIN": "INF209K01322",
    "name": "Aditya Birla Sun Life MNC Fund - Growth - Regular Plan",
    "nav": 1234.5678,
    "date": "2026-10-27",
    "historical_nav": [["2026-10-24", 1220.1], ["2026-10-27", 1234.5678]],
}


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the service's HTTP client with one that talks to a fake provider.

    Returns a list in which every request the service made is recorded,
    so tests can check WHAT the service asked the provider.
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/INF209K01322"):
            return httpx.Response(200, json=PROVIDER_ANSWER)
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setattr(main, "http_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


@pytest.fixture
def client():
    return TestClient(main.app)


# ------------------------------------------------------------------ health
def test_healthz_reports_ok_and_pod(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "nav-service"
    assert body["pod"]


# ------------------------------------------------------------------ latest NAV
def test_latest_nav_returns_our_format(client, fake_provider):
    response = client.get("/api/v1/nav/INF209K01322")
    assert response.status_code == 200
    body = response.json()
    assert body["isin"] == "INF209K01322"
    assert body["scheme_name"] == PROVIDER_ANSWER["name"]
    assert body["nav"] == 1234.5678
    assert body["nav_date"] == "2026-10-27"
    assert body["source"] == "mf.captnemo.in"
    assert "fetched_at" in body and "served_by" in body


def test_latest_nav_does_not_leak_provider_history(client, fake_provider):
    body = client.get("/api/v1/nav/INF209K01322").json()
    assert "historical_nav" not in body          # the adapter drops the big list
    assert "ISIN" not in body and "date" not in body  # provider field names never leak


def test_calls_the_configured_provider_url(client, fake_provider):
    client.get("/api/v1/nav/INF209K01322")
    assert len(fake_provider) == 1
    assert str(fake_provider[0].url) == "https://provider.test/nav/INF209K01322"


# ------------------------------------------------------------------ configuration
def test_service_refuses_to_start_without_provider_url(tmp_path):
    """Fail fast: without NAV_PROVIDER_URL the service must not start at all."""
    env = {k: v for k, v in os.environ.items() if k != "NAV_PROVIDER_URL"}
    env["PYTHONPATH"] = str(SERVICE_DIR)
    result = subprocess.run([sys.executable, "-c", "import app.main"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "NAV_PROVIDER_URL is not set" in result.stderr


# ------------------------------------------------------------------ not built yet (Step 4.4)
@pytest.mark.xfail(strict=True, reason="Step 4.4 adds ISIN validation")
def test_invalid_isin_is_rejected_with_400(client, fake_provider):
    response = client.get("/api/v1/nav/ABC")
    assert response.status_code == 400
    assert fake_provider == []                   # the provider must not even be called


@pytest.mark.xfail(strict=True, reason="Step 4.4 maps provider errors to clean codes")
def test_unknown_isin_gives_404(client, fake_provider):
    assert client.get("/api/v1/nav/INF000000000").status_code == 404