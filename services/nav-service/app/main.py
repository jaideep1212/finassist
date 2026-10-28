"""nav-service: gives the rest of FinAssist the latest NAV of a mutual fund, by ISIN."""
import logging
import os
import re
import socket
import time
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

# Resilience settings (optional, with sensible defaults; can be changed in .env or Kubernetes).
CONNECT_TIMEOUT = float(os.getenv("NAV_PROVIDER_CONNECT_TIMEOUT", "3"))   # seconds to connect
READ_TIMEOUT = float(os.getenv("NAV_PROVIDER_READ_TIMEOUT", "10"))        # seconds for the answer
RETRIES = int(os.getenv("NAV_PROVIDER_RETRIES", "2"))                     # extra attempts after the 1st
BACKOFF_SECONDS = float(os.getenv("NAV_PROVIDER_BACKOFF_SECONDS", "0.5")) # first wait; doubles each time

# Provider answers worth retrying: overloaded or temporarily broken. A 404 is NOT among them:
# asking again for a fund that does not exist will never help.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# One HTTP client for the whole service: it re-uses connections to the provider (faster),
# and tests can swap it for a fake one, so tests never call the real internet.
http_client = httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT))

app = FastAPI(title="FinAssist nav-service", version="0.4.0")


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


def call_provider_with_retries(isin: str) -> httpx.Response:
    """Ask the provider, retrying brief failures with exponential backoff.

    Attempt 1 -> wait 0.5 s -> attempt 2 -> wait 1 s -> attempt 3 -> give up (with RETRIES = 2).
    Returns the provider's response, or raises 503 if every attempt failed.
    """
    attempts = RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            response = http_client.get(f"{PROVIDER_URL}/{isin}")
            if response.status_code not in RETRYABLE_STATUS:
                return response                          # success, or an answer retrying cannot fix
            problem = f"provider answered {response.status_code}"
        except httpx.TimeoutException:
            problem = "provider did not answer in time"
        except httpx.TransportError as error:            # DNS failure, connection refused, reset ...
            problem = f"provider unreachable ({type(error).__name__})"

        if attempt < attempts:
            wait = BACKOFF_SECONDS * 2 ** (attempt - 1)   # 0.5, 1, 2, ...
            log.warning("%s for %s, attempt %d/%d, retrying in %.1fs", problem, isin, attempt, attempts, wait)
            time.sleep(wait)

    log.warning("%s for %s, giving up after %d attempts", problem, isin, attempts)
    raise HTTPException(status_code=503, detail=f"NAV provider unavailable: {problem}")


def fetch_from_provider(isin: str) -> dict:
    """Get one fund from the provider and turn every kind of failure into a clean HTTP error."""
    response = call_provider_with_retries(isin)

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