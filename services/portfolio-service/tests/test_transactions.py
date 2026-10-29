"""Tests for GET /api/v1/isins/{isin}/transactions, against a real (test) PostgreSQL."""
import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import add_holding, add_txn

ISIN = "INF209K01322"
URL = f"/api/v1/isins/{ISIN}/transactions"


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


# ----------------------------------- holder rule ----------------------------------


def test_several_holders_without_holder_gives_400_with_the_list(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    add_holding(db, ISIN, "F-2", "User1")
    response = client.get(URL)
    assert response.status_code == 400
    assert response.json()["detail"]["holders"] == ["User1", "User3"]


# ------------------------------- folio rule ----------------------------------


def test_chosen_folio_returns_only_that_folio(client, db):
    folio_a = add_holding(db, ISIN, "F-A", "User3")
    folio_b = add_holding(db, ISIN, "F-B", "User3")
    add_txn(db, folio_a, "2024-01-01", "PUR", units=1, nav=10, amount=10)
    add_txn(db, folio_b, "2024-02-01", "PUR", units=7, nav=10, amount=70)
    body = client.get(URL, params={"folio": "F-B"}).json()
    assert body["folio_no"] == "F-B"
    assert [t["units"] for t in body["transactions"]] == [7.0]
    assert body["units_held"] == 7.0


# ------------------------------- units held ----------------------------------
def test_units_held_adds_and_subtracts_by_type(client, db):
    fund = add_holding(db, ISIN, "F-1", "User3")
    add_txn(db, fund, "2024-01-01", "PUR", units=10, nav=10, amount=100)   # +10
    add_txn(db, fund, "2024-02-01", "REI", units=1, nav=10, amount=10)     # +1
    add_txn(db, fund, "2024-03-01", "DIV", amount=5)                       #  0
    add_txn(db, fund, "2024-04-01", "RED", units=4, nav=10, amount=40)     # -4
    add_txn(db, fund, "2024-05-01", "REV", units=2, nav=10, amount=20)     # -2
    assert client.get(URL).json()["units_held"] == 5.0
