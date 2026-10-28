"""Shared test setup, loaded by pytest before any test file."""
import os

# The service refuses to start without these. Defaults point at a local test database;
# real environment variables win, so you can point the tests elsewhere.
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "15432")
os.environ.setdefault("PGDATABASE", "portfolio_db")
os.environ.setdefault("PGUSER", "postgres")
os.environ.setdefault("PGPASSWORD", "test-only")
