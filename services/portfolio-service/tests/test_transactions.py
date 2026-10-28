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


# ------------------------------------------------------------------ holder rule
def test_single_holder_can_be_omitted(client, db):
    fund = add_holding(db, ISIN, "F-1", "User3")
    add_txn(db, fund, "2024-01-10", "PUR", units=5, nav=1000, amount=5000, stamp_duty=0.25)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["holder"] == "User3"
    assert body["count"] == 1
    assert body["transactions"][0] == {
        "txn_id": 1, "folio_no": "F-1", "txn_date": "2024-01-10", "txn_type": "PUR",
        "amount": 5000.0, "realized_amt": 4999.75, "nav": 1000.0, "units": 5.0,
        "stt": 0.0, "tds": 0.0, "stamp_duty": 0.25,
    }


def test_several_holders_without_holder_gives_400_with_the_list(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    add_holding(db, ISIN, "F-2", "User1")
    response = client.get(URL)
    assert response.status_code == 400
    assert response.json()["detail"]["holders"] == ["User1", "User3"]


def test_several_holders_with_holder_returns_only_theirs(client, db):
    mine = add_holding(db, ISIN, "F-1", "User3")
    other = add_holding(db, ISIN, "F-2", "User1")
    add_txn(db, mine, "2024-01-10", "PUR", units=5, nav=1000, amount=5000)
    add_txn(db, other, "2024-01-11", "PUR", units=9, nav=1000, amount=9000)
    body = client.get(URL, params={"holder": "User3"}).json()
    assert body["holder"] == "User3"
    assert [t["units"] for t in body["transactions"]] == [5.0]


def test_holder_who_does_not_hold_the_fund_gives_404(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    response = client.get(URL, params={"holder": "User9"})
    assert response.status_code == 404
    assert "User9" in response.json()["detail"]


def test_fund_nobody_holds_gives_404(client, db):
    assert client.get("/api/v1/isins/INF000000000/transactions").status_code == 404


def test_invalid_isin_gives_400(client, db):
    assert client.get("/api/v1/isins/ABC/transactions").status_code == 400


# ------------------------------------------------------------------ which transactions
def test_all_folios_of_the_holder_merged_and_sorted_by_date(client, db):
    folio_a = add_holding(db, ISIN, "F-A", "User3")
    folio_b = add_holding(db, ISIN, "F-B", "User3")
    add_txn(db, folio_a, "2024-03-01", "PUR", units=1, nav=10, amount=10)
    add_txn(db, folio_b, "2024-01-01", "PUR", units=1, nav=10, amount=10)
    add_txn(db, folio_a, "2024-02-01", "PUR", units=1, nav=10, amount=10)
    body = client.get(URL).json()
    assert [(t["txn_date"], t["folio_no"]) for t in body["transactions"]] == [
        ("2024-01-01", "F-B"), ("2024-02-01", "F-A"), ("2024-03-01", "F-A")]


def test_same_holder_in_another_fund_is_not_included(client, db):
    here = add_holding(db, ISIN, "F-1", "User3")
    elsewhere = add_holding(db, "INF846K01768", "F-1", "User3")
    add_txn(db, here, "2024-01-01", "PUR", units=1, nav=10, amount=10)
    add_txn(db, elsewhere, "2024-01-01", "PUR", units=7, nav=10, amount=70)
    assert client.get(URL).json()["count"] == 1


def test_holding_without_transactions_is_200_and_empty(client, db):
    add_holding(db, ISIN, "F-1", "User3")
    body = client.get(URL).json()
    assert body["count"] == 0 and body["transactions"] == [] and body["units_held"] == 0


# ------------------------------------------------------------------ units held
def test_units_held_adds_and_subtracts_by_type(client, db):
    fund = add_holding(db, ISIN, "F-1", "User3")
    add_txn(db, fund, "2024-01-01", "PUR", units=10, nav=10, amount=100)   # +10
    add_txn(db, fund, "2024-02-01", "REI", units=1, nav=10, amount=10)     # +1
    add_txn(db, fund, "2024-03-01", "DIV", amount=5)                       #  0
    add_txn(db, fund, "2024-04-01", "RED", units=4, nav=10, amount=40)     # -4
    add_txn(db, fund, "2024-05-01", "REV", units=2, nav=10, amount=20)     # -2
    assert client.get(URL).json()["units_held"] == 5.0


def test_units_held_has_no_float_rounding_errors(client, db):
    fund = add_holding(db, ISIN, "F-1", "User3")
    for _ in range(3):
        add_txn(db, fund, "2024-01-01", "PUR", units=0.1, nav=10, amount=1)
    assert client.get(URL).json()["units_held"] == 0.3     # float 0.1+0.1+0.1 would be 0.30000000000000004


def test_fully_redeemed_holding_shows_zero_units(client, db):
    fund = add_holding(db, ISIN, "F-1", "User3")
    add_txn(db, fund, "2024-01-01", "PUR", units=5, nav=10, amount=50)
    add_txn(db, fund, "2024-06-01", "RED", units=5, nav=12, amount=60)
    body = client.get(URL).json()
    assert body["units_held"] == 0.0 and body["count"] == 2
