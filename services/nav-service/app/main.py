"""nav-service: gives the rest of FinAssist the latest NAV of a mutual fund, by ISIN."""
import os
import socket
from datetime import datetime, timezone

import httpx
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI

load_dotenv(find_dotenv(usecwd=True))

SERVICE = "nav-service"
POD_NAME = os.getenv("POD_NAME", socket.gethostname())   # in Kubernetes: the pod's name

# Where the NAVs come from. A setting, not hard-coded logic, so it can be changed
# per environment (for example to a test server) without touching the code.
PROVIDER_URL = os.getenv("NAV_PROVIDER_URL")
if not PROVIDER_URL:
    raise RuntimeError("NAV_PROVIDER_URL is not set. Add it to the .env file at the project root.")
PROVIDER_URL = PROVIDER_URL.rstrip("/")


# One HTTP client for the whole service: it re-uses connections to the provider (faster),
# and tests can swap it for a fake one, so tests never call the real internet.
http_client = httpx.Client(timeout=10)

# Create web application
app = FastAPI(title="FinAssist nav-service", version="0.2.0")


@app.get("/healthz")
def healthz():
    """Liveness: Kubernetes calls this to check the process is alive."""
    return {"status": "ok", "service": SERVICE, "pod": POD_NAME}


@app.get("/api/v1/nav/{isin}")
def latest_nav(isin: str):
    """Latest NAV of one mutual fund, looked up by its ISIN."""
    # 1. Call the external REST API.
    response = http_client.get(f"{PROVIDER_URL}/{isin}")
    response.raise_for_status()            # stop here if the provider answered with an error
    data = response.json()                 # the JSON body as a Python dict

    # 2. Translate the provider's format into OUR format (and drop the big history list).
    return {
        "isin": data["ISIN"],
        "scheme_name": data["name"],
        "nav": data["nav"],
        "nav_date": data["date"],
        "source": "mf.captnemo.in",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "served_by": POD_NAME,
    }