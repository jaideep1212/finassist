"""Shared test setup, loaded by pytest before any test file."""
import os

# The service refuses to start without this setting. Give tests a fake address, so a test
# can never reach the real provider by accident (it is set BEFORE app.main is imported).
os.environ["NAV_PROVIDER_URL"] = "https://provider.test/nav"