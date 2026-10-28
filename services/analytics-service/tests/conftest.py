"""Shared test setup, loaded by pytest before any test file."""
import os

# analytics-service refuses to start without these. Fake addresses: tests never call real
# services; a fake HTTP transport answers instead (set BEFORE app modules are imported).
os.environ["NAV_SERVICE_URL"] = "http://nav.test"
os.environ["PORTFOLIO_SERVICE_URL"] = "http://portfolio.test"
