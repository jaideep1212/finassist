"""Calls from analytics-service to the other two services.

analytics-service owns no data: NAVs come from nav-service, holdings and transactions from
portfolio-service. This file is the ONLY place that knows their URLs and answer formats,
and it turns their errors into answers analytics-service can use.

The two calls fail differently, on purpose:
- nav-service is needed by BOTH methods: if it fails, the whole request fails (HTTPException).
- portfolio-service is needed only by the customer XIRR: if it fails, we return a status
  for the customer section and the fund's SIP returns are still shown.
The ISIN must already be validated by the caller (it goes into a URL path).
"""
import logging
import os
from datetime import date

import httpx
from dotenv import find_dotenv, load_dotenv
from fastapi import HTTPException

load_dotenv(find_dotenv(usecwd=True))
log = logging.getLogger("analytics-service")


def required_url(name: str) -> str:
    """Read a service address from the environment, or stop at startup with a clear message."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to the .env file at the project root.")
    return value.rstrip("/")


NAV_SERVICE_URL = required_url("NAV_SERVICE_URL")             # e.g. http://localhost:8000
PORTFOLIO_SERVICE_URL = required_url("PORTFOLIO_SERVICE_URL") # e.g. http://localhost:8001

# Calls inside our own system should be fast. Short timeouts: a hanging service must not
# hang analytics-service. No retries here: nav-service already retries its own provider.
TIMEOUT = httpx.Timeout(float(os.getenv("SERVICE_READ_TIMEOUT", "15")),
                        connect=float(os.getenv("SERVICE_CONNECT_TIMEOUT", "3")))
http_client = httpx.Client(timeout=TIMEOUT)


# ------------------------------------------------------------------ nav-service
def fetch_nav_history(isin: str) -> tuple[str, list[tuple[date, float]]]:
    """Return (scheme_name, [(date, nav), ...] oldest first) for the fund.

    Any failure stops the whole request, because both XIRR methods need these NAVs.
    """
    try:
        response = http_client.get(f"{NAV_SERVICE_URL}/api/v1/nav/{isin}/history")
    except httpx.HTTPError as error:                 # timeout, connection refused, ...
        log.warning("nav-service not reachable: %s", error)
        raise HTTPException(status_code=503, detail="nav-service not available") from None

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No NAV data found for ISIN {isin}")
    if response.status_code != 200:
        log.warning("nav-service answered %s for %s", response.status_code, isin)
        raise HTTPException(status_code=503, detail="nav-service not available")

    try:
        body = response.json()
        history = [(date.fromisoformat(p["date"]), float(p["nav"])) for p in body["points"]]
        return body["scheme_name"], history
    except (ValueError, KeyError, TypeError):
        log.error("nav-service sent unexpected data for %s", isin)
        raise HTTPException(status_code=502, detail="nav-service sent unexpected data") from None


# ------------------------------------------------------------------ portfolio-service
def fetch_holding(isin: str, holder: str | None, folio: str | None) -> dict:
    """Return the transactions of one holding, or a status explaining why there are none.

    Never raises: the customer section gets a status, the rest of the answer still works.
      {"status": "ok", "data": {...portfolio-service's /transactions answer...}}
      {"status": "holder_required", "message": ..., "holders": [...]}
      {"status": "folio_required",  "message": ..., "folios":  [...]}
      {"status": "no_investments",  "message": ...}     nobody holds this fund
      {"status": "not_found",       "message": ...}     the chosen holder/folio does not exist
      {"status": "unavailable",     "message": ...}     portfolio-service or database down
    """
    params = {key: value for key, value in (("holder", holder), ("folio", folio)) if value}
    try:
        # params=... encodes values safely, e.g. the "/" in folio "9024151391/0".
        response = http_client.get(f"{PORTFOLIO_SERVICE_URL}/api/v1/isins/{isin}/transactions",
                                   params=params)
    except httpx.HTTPError as error:
        log.warning("portfolio-service not reachable: %s", error)
        return {"status": "unavailable", "message": "portfolio-service not available"}

    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None

    if response.status_code == 200:
        return {"status": "ok", "data": body}
    if response.status_code == 400 and isinstance(detail, dict):
        if "holders" in detail:
            return {"status": "holder_required", "message": detail.get("message"),
                    "holders": detail["holders"]}
        if "folios" in detail:
            return {"status": "folio_required", "message": detail.get("message"),
                    "folios": detail["folios"]}
    if response.status_code == 404:
        if params:                                   # the caller chose a holder or folio
            return {"status": "not_found", "message": detail}
        return {"status": "no_investments", "message": "No investments in this fund"}

    log.warning("portfolio-service answered %s for %s", response.status_code, isin)
    return {"status": "unavailable", "message": "portfolio-service not available"}
