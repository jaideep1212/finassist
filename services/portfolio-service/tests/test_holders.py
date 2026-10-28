"""Tests for GET /api/v1/isins/{isin}/holders, against a real (test) PostgreSQL."""
import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import add_holding

ISIN = "INF846K01768"
URL = f"/api/v1/isins/{ISIN}/holders"


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_single_holder_with_single_folio(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"isin": ISIN, "count": 1,
                               "holders": [{"name": "User1", "folios": ["F-1"]}]}


def test_several_holders_sorted(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    add_holding(db, ISIN, "F-2", "User1")
    body = client.get(URL).json()
    assert [h["name"] for h in body["holders"]] == ["User1", "User3"]
    assert body["count"] == 2


def test_holder_with_two_folios_is_listed_once_with_both_folios_sorted(client, db):
    add_holding(db, ISIN, "F-9", "User1")
    add_holding(db, ISIN, "F-2", "User1")
    body = client.get(URL).json()
    assert body["holders"] == [{"name": "User1", "folios": ["F-2", "F-9"]}]
    assert body["count"] == 1


def test_other_funds_are_not_mixed_in(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    add_holding(db, "INF209K01322", "F-9", "User2")
    add_holding(db, "INF209K01322", "F-8", "User1")
    assert client.get(URL).json()["holders"] == [{"name": "User1", "folios": ["F-1"]}]


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
    response = client.get(URL)
    assert response.status_code == 503
    assert "connection refused" not in response.text
