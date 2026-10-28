"""Tests for the portfolio-service skeleton: settings check, liveness and readiness."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

SERVICE_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    with TestClient(main.app) as c:    # "with" runs the lifespan: opens and closes the pool
        yield c


def test_healthz_ok_without_touching_the_database(client, monkeypatch):
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("liveness must not use the database")
    monkeypatch.setattr(main.pool, "connection", must_not_be_called)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "portfolio-service"


def test_readyz_ok_when_database_answers(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_503_when_database_is_down(client, monkeypatch):
    def database_down(*args, **kwargs):
        raise main.psycopg.OperationalError("connection refused")
    monkeypatch.setattr(main.pool, "connection", database_down)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "connection refused" not in response.text      # internal details never leak


def test_service_refuses_to_start_without_db_settings(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PGPASSWORD"}
    env["PYTHONPATH"] = str(SERVICE_DIR)
    result = subprocess.run([sys.executable, "-c", "import app.main"],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "PGPASSWORD" in result.stderr
