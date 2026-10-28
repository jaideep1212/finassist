"""nav-service: gives the rest of FinAssist the latest NAV of a mutual fund, by ISIN."""
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone, date

import httpx
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException, Query


# Real environment variables (e.g. set by Kubernetes) are NOT overwritten by .env.
load_dotenv(find_dotenv(usecwd=True))

SERVICE = "nav-service"
POD_NAME = os.getenv("POD_NAME", socket.gethostname())   # for Kubernetes
log = logging.getLogger(SERVICE)

# Where the NAVs come from. Required setting: stop at startup with a clear message if missing,
# rather than failing later on the first request.
PROVIDER_URL = os.getenv("NAV_PROVIDER_URL")
if not PROVIDER_URL:
    raise RuntimeError("NAV_PROVIDER_URL is not set. Add it to the .env file at the project root.")
PROVIDER_URL = PROVIDER_URL.rstrip("/")

# An Indian ISIN: "IN", then 10 letters or digits (12 characters in total).
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")

# Resilience settings
CONNECT_TIMEOUT = 3.0   # seconds to connect
READ_TIMEOUT = 10.0     # seconds for the answer
RETRIES = 2             # number of retries after first attempt
BACKOFF_SECONDS = 0.5   # first wait; doubles each time

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
                return response
            problem = f"provider answered {response.status_code}"
        except httpx.TimeoutException:
            problem = "provider did not answer in time"
        except httpx.TransportError as error:
            problem = f"provider unreachable ({type(error).__name__})"

        if attempt < attempts:
            wait = BACKOFF_SECONDS * 2 ** (attempt - 1)   # 0.5, 1, 2, ...
            log.warning("%s for %s, attempt %d/%d, retrying in %.1fs", problem, isin, attempt, attempts, wait)
            time.sleep(wait)

    log.warning("%s for %s, giving up after %d attempts", problem, isin, attempts)
    raise HTTPException(status_code=503, detail=f"NAV provider unavailable: {problem}")


def clean_history(isin: str, raw) -> list[tuple[date, float]]:
    """Turn the provider's [["2016-11-28", 13.4288], ...] into a clean, sorted list."""
    if raw is None:
        log.warning("provider sent no history for %s", isin)
        return []
    if not isinstance(raw, list):
        raise ValueError("historical_nav is not a list")

    by_date: dict[date, float] = {}
    dropped = 0
    for point in raw:
        try:
            day = date.fromisoformat(point[0])
            nav = float(point[1])
            if not nav > 0:
                raise ValueError("NAV must be > 0")
        except (TypeError, ValueError, IndexError, KeyError):
            dropped += 1
            continue
        by_date[day] = nav
    if dropped:
        log.warning("dropped %d invalid history points for %s", dropped, isin)
    return sorted(by_date.items())


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
            "history": clean_history(isin, data.get("historical_nav")),
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        log.error("provider sent unexpected data for %s", isin)
        raise HTTPException(status_code=502, detail="NAV provider sent unexpected data") from None


@app.get("/api/v1/nav/{isin}")
def latest_nav(isin: str):
    """Latest NAV of one mutual fund, looked up by its ISIN."""
    clean_isin = validate_isin(isin)
    nav = fetch_from_provider(clean_isin)
    return {
        "isin": nav["isin"],
        "scheme_name": nav["scheme_name"],
        "nav": nav["nav"],
        "nav_date": nav["nav_date"],
        "source": "mf.captnemo.in",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "served_by": POD_NAME,
    }


def parse_date(raw: str | None, name: str) -> date | None:
    """Turn '2019-01-01' into a date, or answer 400 Bad Request."""
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"'{name}' must be a date like 2019-01-01, got '{raw}'") from None


@app.get("/api/v1/nav/{isin}/history")
def nav_history(isin: str,
                from_: str | None = Query(None, alias="from", description="first date, YYYY-MM-DD"),
                to: str | None = Query(None, description="last date, YYYY-MM-DD")):
    """Daily NAV history of one fund, oldest first. Without from/to: the full history.

    Days without a NAV (weekends, holidays) are simply absent: deciding which NAV a
    holiday SIP gets is a financial rule and belongs to analytics-service.
    """
    clean_isin = validate_isin(isin)
    start, end = parse_date(from_, "from"), parse_date(to, "to")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail=f"'from' ({start}) is after 'to' ({end})")

    fund = fetch_from_provider(clean_isin)

    points = [{"date": day.isoformat(), "nav": nav}
              for day, nav in fund["history"]
              if (start is None or day >= start) and (end is None or day <= end)]
    return {
        "isin": fund["isin"],
        "scheme_name": fund["scheme_name"],
        "from": points[0]["date"] if points else (start.isoformat() if start else None),
        "to": points[-1]["date"] if points else (end.isoformat() if end else None),
        "count": len(points),
        "points": points,
        "source": "mf.captnemo.in",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "served_by": POD_NAME,
    }