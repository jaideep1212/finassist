"""web-ui: the browser's single entry point.

Two jobs:
1. Serve the web page: index.html, app.js and style.css from app/static.
2. Act as a gateway: the browser calls ONLY web-ui, and web-ui forwards the few API calls the
   page needs to portfolio-service and analytics-service, which stay internal.

Only an explicit list of calls is forwarded (an allowlist), not "anything under /api". The
browser can therefore never reach endpoints the page does not need.
"""
import logging
import os
import re
import socket
from pathlib import Path
import httpx
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(find_dotenv(usecwd=True))

SERVICE = "web-ui"
POD_NAME = socket.gethostname()
log = logging.getLogger(SERVICE)


def required_url(name: str) -> str:
    """Read a service address from the environment, or stop at startup with a clear message."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to the .env file at the project root.")
    return value.rstrip("/")


PORTFOLIO_SERVICE_URL = required_url("PORTFOLIO_SERVICE_URL")
ANALYTICS_SERVICE_URL = required_url("ANALYTICS_SERVICE_URL")

# analytics-service may itself wait for nav-service and portfolio-service, so allow a bit more.
http_client = httpx.Client(timeout=httpx.Timeout(30.0, connect=3.0))

ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")

app = FastAPI(title="FinAssist web-ui", version="0.1.0")

# The page: one HTML file plus its script and style, served as plain files.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", include_in_schema=False)
def page():
    """The web page. Everything else it needs is under /static and /api/v1."""
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": SERVICE, "pod": POD_NAME}


def forward(base_url: str, path: str, params: dict) -> JSONResponse:
    """Call a backend service and hand its answer (status code + JSON) back to the browser.

    The backend's own error answers (400/404 with details) are passed on unchanged, so the
    page can show them. Only "cannot reach the service" is turned into our own 503.
    """
    params = {key: value for key, value in params.items() if value}   # drop empty parameters
    service = base_url.split("//")[-1]
    try:
        response = http_client.get(f"{base_url}{path}", params=params)
    except httpx.HTTPError as error:
        log.warning("%s not reachable: %s", service, error)
        return JSONResponse(status_code=503, content={"detail": "service not available, try again later"})
    try:
        body = response.json()
    except ValueError:
        log.error("%s sent a non-JSON answer (%s)", service, response.status_code)
        return JSONResponse(status_code=502, content={"detail": "unexpected answer from a service"})
    return JSONResponse(status_code=response.status_code, content=body)


def validate_isin(raw: str) -> str:
    """The ISIN goes into a backend URL path, so it must be checked here first."""
    isin = raw.strip().upper()
    if not ISIN_PATTERN.match(isin):
        raise HTTPException(status_code=400, detail=f"'{raw}' is not a valid ISIN")
    return isin


# ------------------------------------------------------------------ the allowlist
@app.get("/api/v1/amcs")
def amcs():
    """Dropdown 1: the AMCs."""
    return forward(PORTFOLIO_SERVICE_URL, "/api/v1/amcs", {})


@app.get("/api/v1/schemes")
def schemes(amc: str | None = None):
    """Dropdown 2: the funds of one AMC."""
    return forward(PORTFOLIO_SERVICE_URL, "/api/v1/schemes", {"amc": amc})


@app.get("/api/v1/isins/{isin}/holders")
def holders(isin: str):
    """Dropdowns 3 and 4: the holders of a fund, each with their folios."""
    return forward(PORTFOLIO_SERVICE_URL, f"/api/v1/isins/{validate_isin(isin)}/holders", {})


@app.get("/api/v1/xirr")
def xirr(isin: str | None = None, holder: str | None = None, folio: str | None = None):
    """The results: customer XIRR, fund SIP returns, transactions."""
    return forward(ANALYTICS_SERVICE_URL, "/api/v1/xirr",
                   {"isin": isin, "holder": holder, "folio": folio})
