"""nav-service: gives the rest of FinAssist the latest NAV of a mutual fund, by ISIN."""
import logging
import os
import re
import socket
from datetime import datetime, timezone

import httpx
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException

# Local development: read settings from the nearest .env file (searching upwards from the
# folder you start uvicorn in, so the one at the project root is found).
# Real environment variables (e.g. set by Kubernetes) are NOT overwritten by .env.
load_dotenv(find_dotenv(usecwd=True))

SERVICE = "nav-service"
POD_NAME = os.getenv("POD_NAME", socket.gethostname())   # in Kubernetes: the pod's name
log = logging.getLogger(SERVICE)

# Where the NAVs come from. Required setting: stop at startup with a clear message if missing,
# rather than failing later on the first request.
PROVIDER_URL = os.getenv("NAV_PROVIDER_URL")
if not PROVIDER_URL:
    raise RuntimeError("NAV_PROVIDER_URL is not set. Add it to the .env file at the project root.")
PROVIDER_URL = PROVIDER_URL.rstrip("/")

# An Indian ISIN: "IN", then 10 letters or digits (12 characters in total).
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")

# One HTTP client for the whole service: it re-uses connections to the provider (faster),
# and tests can swap it for a fake one, so tests never call the real internet.
http_client = httpx.Client(timeout=10)

app = FastAPI(title="FinAssist nav-service", version="0.3.0")


@app.get("/healthz")
def healthz():
    """Liveness: Kubernetes calls this to check the process is alive."""
    return {"status": "ok", "service": SERVICE, "pod": POD_NAME}


def validate_isin(raw: str) -> str:
    """Return the ISIN in upper case, or answer 400 Bad Request.

    This runs BEFORE the ISIN is put into the provider's URL. Without it, a caller could
    send something like '..%2Fother' and make our service request other addresses (SSRF).
    """
    isin = raw.strip().upper()
    if not ISIN_PATTERN.match(isin):
        raise HTTPException(status_code=400,
                            detail=f"'{raw}' is not a valid ISIN (expected IN + 10 letters/digits)")
    return isin


def fetch_from_provider(isin: str) -> dict:
    """Call the provider and turn every kind of failure into a clean HTTP error for OUR caller."""
    try:
        response = http_client.get(f"{PROVIDER_URL}/{isin}")
    except httpx.TimeoutException:
        log.warning("provider timed out for %s", isin)
        raise HTTPException(status_code=503, detail="NAV provider did not answer in time") from None
    except httpx.HTTPError as error:             # DNS failure, connection refused, ...
        log.warning("provider unreachable for %s: %s", isin, error)
        raise HTTPException(status_code=503, detail="NAV provider is unreachable") from None

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No NAV found for ISIN {isin}")
    if response.status_code != 200:
        log.warning("provider answered %s for %s", response.status_code, isin)
        raise HTTPException(status_code=503, detail="NAV provider is having problems")

    try:
        data = response.json()
        return {
            "isin": data["ISIN"],
            "scheme_name": data["name"],
            "nav": float(data["nav"]),
            "nav_date": data["date"],
        }
    except (ValueError, KeyError, TypeError):    # not JSON, or fields missing / wrong type
        log.error("provider sent unexpected data for %s", isin)
        raise HTTPException(status_code=502, detail="NAV provider sent unexpected data") from None


@app.get("/api/v1/nav/{isin}")
def latest_nav(isin: str):
    """Latest NAV of one mutual fund, looked up by its ISIN."""
    clean_isin = validate_isin(isin)
    nav = fetch_from_provider(clean_isin)
    return {
        **nav,
        "source": "mf.captnemo.in",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "served_by": POD_NAME,
    }