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


def test_script_and_style_are_served():
    script = client.get("/static/app.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    style = client.get("/static/style.css")
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")


def test_unknown_static_file_gives_404():
    assert client.get("/static/nothing.js").status_code == 404


def test_files_outside_the_static_folder_cannot_be_read():
    assert client.get("/static/../main.py").status_code == 404
    assert client.get("/static/%2e%2e/main.py").status_code == 404
