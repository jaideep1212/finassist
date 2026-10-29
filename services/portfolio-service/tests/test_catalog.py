"""Tests for the AMC and fund lists (the first two dropdowns), against a real (test) PostgreSQL."""
import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import add_holding

AXIS, ABSL = "Axis Asset Management", "Aditya Birla Sun Life AMC"


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


# -------------------------------- AMCs ----------------------------------
def test_amcs_are_distinct_and_sorted(client, db):
    add_holding(db, "INF846K01768", "F-1", "User1", entity=AXIS)
    add_holding(db, "INF846K01768", "F-2", "User2", entity=AXIS)
    add_holding(db, "INF209K01322", "F-3", "User1", entity=ABSL)
    assert client.get("/api/v1/amcs").json() == {"amcs": [ABSL, AXIS], "count": 2}


# -------------------------------- Funds of an AMC ----------------------------------
def test_schemes_of_an_amc_one_per_isin_sorted_by_name(client, db):
    add_holding(db, "INF846K01768", "F-1", "User1", scheme_name="Axis Multi Asset", entity=AXIS)
    add_holding(db, "INF846K01768", "F-2", "User2", scheme_name="Axis Multi Asset", entity=AXIS)
    add_holding(db, "INF846K01131", "F-3", "User2", scheme_name="Axis Bluechip", entity=AXIS)
    add_holding(db, "INF209K01322", "F-4", "User1", scheme_name="ABSL MNC", entity=ABSL)
    body = client.get("/api/v1/schemes", params={"amc": AXIS}).json()
    assert body == {"amc": AXIS, "count": 2, "schemes": [
        {"isin": "INF846K01131", "scheme_name": "Axis Bluechip"},
        {"isin": "INF846K01768", "scheme_name": "Axis Multi Asset"}]}
