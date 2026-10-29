"""Tests for the portfolio-service skeleton: settings check, liveness and readiness."""

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:    # "with" runs the lifespan: opens and closes the pool
        yield c


def test_readyz_503_when_database_is_down(client, monkeypatch):
    def database_down(*args, **kwargs):
        raise main.psycopg.OperationalError("connection refused")
    monkeypatch.setattr(main.pool, "connection", database_down)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "connection refused" not in response.text      # internal details never leak
