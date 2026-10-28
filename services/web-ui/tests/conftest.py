"""Shared test setup, loaded by pytest before any test file."""
import os

# web-ui refuses to start without these. Fake addresses: a fake HTTP transport answers in tests.
os.environ["PORTFOLIO_SERVICE_URL"] = "http://portfolio.test"
os.environ["ANALYTICS_SERVICE_URL"] = "http://analytics.test"
