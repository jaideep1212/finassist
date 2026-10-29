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


def test_holder_with_two_folios_is_listed_once_with_both_folios_sorted(client, db):
    add_holding(db, ISIN, "F-9", "User1")
    add_holding(db, ISIN, "F-2", "User1")
    body = client.get(URL).json()
    assert body["holders"] == [{"name": "User1", "folios": ["F-2", "F-9"]}]
    assert body["count"] == 1


def test_sql_injection_attempt_is_just_an_invalid_isin(client, db):
    add_holding(db, ISIN, "F-1", "User1")
    response = client.get("/api/v1/isins/' OR '1'='1/holders")
    assert response.status_code in (400, 404)
    assert "User1" not in response.text
