"""Tests that web-ui serves the page and its files."""
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_page_is_served_at_the_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for element in ('id="amc"', 'id="fund"', 'id="holder"', 'id="folio"', "/static/app.js"):
        assert element in response.text
