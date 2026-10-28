"""Tests for GET /api/v1/isins/{isin}/holders, against a real (test) PostgreSQL."""
import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import add_holding

ISIN = "INF846K01768"


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_single_holder(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    response = client.get(f"/api/v1/isins/{ISIN}/holders")
    assert response.status_code == 200
    assert response.json() == {"isin": ISIN, "holders": ["User1"], "count": 1}


def test_several_holders_sorted(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    add_holding(db, ISIN, "F-2", "User1")
    assert client.get(f"/api/v1/isins/{ISIN}/holders").json()["holders"] == ["User1", "User3"]


def test_holder_with_two_folios_is_listed_once(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    add_holding(db, ISIN, "F-2", "User1")
    body = client.get(f"/api/v1/isins/{ISIN}/holders").json()
    assert body["holders"] == ["User1"] and body["count"] == 1


def test_other_funds_are_not_mixed_in(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    add_holding(db, "INF209K01322", "F-9", "User2")
    assert client.get(f"/api/v1/isins/{ISIN}/holders").json()["holders"] == ["User1"]


def test_lowercase_isin_is_accepted(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    response = client.get(f"/api/v1/isins/{ISIN.lower()}/holders")
    assert response.status_code == 200
    assert response.json()["isin"] == ISIN


def test_fund_nobody_holds_gives_404(client, db):
    response = client.get("/api/v1/isins/INF000000000/holders")
    assert response.status_code == 404
    assert "No investments" in response.json()["detail"]


@pytest.mark.parametrize("bad", ["ABC", "US0378331005", "INF846K0176", "INF846K01768X"])
def test_invalid_isin_gives_400(client, db, bad):
    assert client.get(f"/api/v1/isins/{bad}/holders").status_code == 400


def test_sql_injection_attempt_is_just_an_invalid_isin(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    response = client.get("/api/v1/isins/' OR '1'='1/holders")
    assert response.status_code in (400, 404)
    assert "User1" not in response.text


def test_database_down_gives_503(client, db, monkeypatch):
    def database_down(*args, **kwargs):
        raise main.psycopg.OperationalError("connection refused")
    monkeypatch.setattr(main.pool, "connection", database_down)
    response = client.get(f"/api/v1/isins/{ISIN}/holders")
    assert response.status_code == 503
    assert "connection refused" not in response.text
