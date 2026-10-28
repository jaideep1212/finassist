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


def test_no_holdings_gives_an_empty_list(client, db):
    assert client.get("/api/v1/amcs").json() == {"amcs": [], "count": 0}


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


def test_same_isin_with_two_names_appears_once(client, db):
    add_holding(db, "INF846K01768", "F-1", "User1", scheme_name="Axis Multi Asset", entity=AXIS)
    add_holding(db, "INF846K01768", "F-2", "User2", scheme_name="Axis Multi Asset Fund", entity=AXIS)
    assert client.get("/api/v1/schemes", params={"amc": AXIS}).json()["count"] == 1


def test_amc_name_with_ampersand_and_slash_works(client, db):
    name = "Kotak Mahindra & Co / AMC"
    add_holding(db, "INF174K01LS2", "F-1", "User1", entity=name)
    body = client.get("/api/v1/schemes", params={"amc": name}).json()
    assert body["schemes"][0]["isin"] == "INF174K01LS2"


def test_unknown_amc_gives_404(client, db):
    add_holding(db, "INF846K01768", "F-1", "User1", entity=AXIS)
    assert client.get("/api/v1/schemes", params={"amc": "Nobody AMC"}).status_code == 404


@pytest.mark.parametrize("params", [{}, {"amc": ""}, {"amc": "   "}])
def test_missing_amc_gives_400(client, db, params):
    assert client.get("/api/v1/schemes", params=params).status_code == 400
