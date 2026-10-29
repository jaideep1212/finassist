"""Tests for nav-service. The real provider is never called: a fake one answers instead."""

import httpx
import pytest
from fastapi.testclient import TestClient
from datetime import date

from app import main


# What the provider returns for one fund (shortened history).
PROVIDER_ANSWER = {
    "ISIN": "INF209K01322",
    "name": "Aditya Birla Sun Life MNC Fund - Growth - Regular Plan",
    "nav": 1234.5678,
    "date": "2026-10-27",
    "historical_nav": [["2026-10-24", 1220.1], ["2026-10-27", 1234.5678]],
}

HISTORY_ANSWER = {**PROVIDER_ANSWER, "historical_nav": [
    ["2026-10-20", 1210.0], ["2026-10-21", 1212.5],
    # 2026-10-22 missing: a holiday
    ["2026-10-23", 1215.0], ["2026-10-24", 1220.1], ["2026-10-27", 1234.5678],
]}
HISTORY_URL = "/api/v1/nav/INF209K01322/history"

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

@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    """Retries wait between attempts. In tests, record the waits instead of sleeping."""
    waits = []
    monkeypatch.setattr(main.time, "sleep", waits.append)
    return waits


# --------------------------- latest NAV --------------------------------
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


# -------------------------- validation and errors -------------------------------


def test_path_tricks_never_reach_the_provider(client, fake_provider):
    response = client.get("/api/v1/nav/..%2F..%2Fsecret")
    assert response.status_code in (400, 404)    # rejected by validation or routing
    assert fake_provider == []


def test_unknown_isin_gives_404(client, fake_provider):
    response = client.get("/api/v1/nav/INF000000000")
    assert response.status_code == 404
    assert "INF000000000" in response.json()["detail"]


def use_provider(monkeypatch, handler):
    """Install a fake provider that behaves the way `handler` says."""
    monkeypatch.setattr(main, "http_client", httpx.Client(transport=httpx.MockTransport(handler)))


# --------------------------- retries ---------------------------
def provider_that_answers(monkeypatch, *answers):
    """Fake provider giving these answers in order (a Response, or an exception to raise)."""
    calls = []
    queue = list(answers)

    def handler(request):
        calls.append(request)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]   # last answer repeats
        if isinstance(answer, Exception):
            raise answer
        return answer

    use_provider(monkeypatch, handler)
    return calls


def test_brief_provider_error_is_retried_and_succeeds(client, monkeypatch, no_real_waiting):
    calls = provider_that_answers(monkeypatch,
                                  httpx.Response(503), httpx.Response(200, json=PROVIDER_ANSWER))
    response = client.get("/api/v1/nav/INF209K01322")
    assert response.status_code == 200
    assert len(calls) == 2
    assert no_real_waiting == [0.5]


def test_gives_up_after_all_attempts(client, monkeypatch, no_real_waiting):
    calls = provider_that_answers(monkeypatch, httpx.Response(500))
    response = client.get("/api/v1/nav/INF209K01322")
    assert response.status_code == 503
    assert len(calls) == 3                       # 1 attempt + 2 retries
    assert len(no_real_waiting) == 2             # no pointless wait after the last attempt


# --------------------------- data cleaning ---------------------------

def test_clean_history_drops_bad_points_dedupes_and_sorts():
    raw = [
        ["2026-10-27", 1234.5],
        ["2026-10-20", 1210.0],
        ["2026-10-20", 1211.0],      # duplicate date: last value wins
        ["2026-10-21", 0],           # zero
        ["2026-10-22", None],        # missing
        ["2026-10-23", "abc"],       # not a number
        ["2026-10-24", float("nan")],# NaN
        ["bad-date", 1.0],           # bad date
        ["2026-10-25"],              # incomplete
    ]
    assert main.clean_history("X", raw) == [(date(2026, 10, 20), 1211.0),
                                             (date(2026, 10, 27), 1234.5)]


# --------------------------- fetch history ---------------------------


def test_history_range_is_inclusive_and_skips_holidays(client, monkeypatch):
    use_provider(monkeypatch, lambda r: httpx.Response(200, json=HISTORY_ANSWER))
    body = client.get(HISTORY_URL, params={"from": "2026-10-22", "to": "2026-10-24"}).json()
    assert [p["date"] for p in body["points"]] == ["2026-10-23", "2026-10-24"]
    assert body["from"] == "2026-10-23"          # real first date, not the requested holiday
