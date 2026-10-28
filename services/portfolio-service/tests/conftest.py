"""Shared test setup, loaded by pytest before any test file.

Tests run against a SEPARATE database (portfolio_test by default), never portfolio_db:
the fixtures below empty the tables before every test.
"""
import os
from pathlib import Path

# Set BEFORE app.main is imported. The test database always wins over .env, so tests can
# never touch your real data by accident. Host/port/user come from .env as usual.
os.environ["PGDATABASE"] = os.getenv("TEST_PGDATABASE", "portfolio_test")
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "15432")
os.environ.setdefault("PGUSER", "postgres")
os.environ.setdefault("PGPASSWORD", "test-only")

import psycopg  # noqa: E402
import pytest  # noqa: E402

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture
def db():
    """An empty test database with the real schema. Yields a connection to insert test rows."""
    name = os.environ["PGDATABASE"]
    if not name.endswith("_test"):     # last safety net before deleting anything
        raise RuntimeError(f"refusing to run tests against database {name!r}")
    with psycopg.connect(autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text())              # creates tables if missing
        conn.execute("TRUNCATE mf_txns, mf_base RESTART IDENTITY")
        yield conn


def add_holding(conn, isin, folio_no, holder, scheme_name="Some Fund", entity="Some AMC"):
    """Insert one holding and return its new fund_id."""
    return conn.execute(
        """INSERT INTO mf_base (entity_name, scheme_name, isin, folio_no, first_holder)
           VALUES (%s, %s, %s, %s, %s) RETURNING fund_id""",
        (entity, scheme_name, isin, folio_no, holder),
    ).fetchone()[0]
