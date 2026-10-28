"""portfolio-service: owns the investment data (holdings and transactions) in portfolio_db."""
import logging
import os
import re
import socket
from contextlib import asynccontextmanager

import psycopg
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException
from psycopg_pool import ConnectionPool

# Local development: read settings from the nearest .env file (the one at the project root).
# Real environment variables (e.g. set by Kubernetes) are NOT overwritten by .env.
load_dotenv(find_dotenv(usecwd=True))

SERVICE = "portfolio-service"
POD_NAME = socket.gethostname()
log = logging.getLogger(SERVICE)

# Database settings use PostgreSQL's standard names (PGHOST, PGPORT, ...), the same as the
# loader. psycopg reads them from the environment by itself, so no connection string with a
# password is ever built in our code. Fail fast at startup if one is missing.
REQUIRED_SETTINGS = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
if missing:
    raise RuntimeError(f"Missing database settings: {', '.join(missing)}. "
                       "Set them in .env (PGPASSWORD in your shell) or in Kubernetes.")

DB_POOL_MAX = 5              # max open connections per pod
DB_WAIT_SECONDS = 5.0        # max wait for a free connection

# A pool keeps a few connections open and lends them to requests. Opening a new PostgreSQL
# connection per request costs a network round trip plus authentication each time.
# It is created when the app STARTS (lifespan below), not when this file is imported.
pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    # open=True, wait=False: start even if the database is not reachable yet. The pool keeps
    # trying in the background, and /readyz reports "not ready" until it succeeds. In
    # Kubernetes the pod simply gets no traffic until then, instead of crashing in a loop.
    pool = ConnectionPool(conninfo="", min_size=1, max_size=DB_POOL_MAX, open=False,
                          kwargs={"connect_timeout": 5})
    pool.open(wait=False)
    yield
    pool.close()


app = FastAPI(title="FinAssist portfolio-service", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    """Liveness: is the PROCESS alive? Deliberately does NOT touch the database.

    If it did, a database outage would make Kubernetes restart every pod, which cannot fix
    the database and only adds load when it comes back.
    """
    return {"status": "ok", "service": SERVICE, "pod": POD_NAME}


@app.get("/readyz")
def readyz():
    """Readiness: can this pod serve requests right now? Needs a working database connection.

    If not ready, Kubernetes stops sending traffic to this pod (but does not restart it).
    """
    try:
        with pool.connection(timeout=DB_WAIT_SECONDS) as conn:
            conn.execute("SELECT 1")
    except Exception as error:
        log.warning("not ready: %s", error)
        raise HTTPException(status_code=503, detail="database not reachable") from None
    return {"status": "ready", "service": SERVICE, "pod": POD_NAME}


# -------------------------- helpers ----------------------------------------
# An Indian ISIN: "IN", then 10 letters or digits. Same rule as nav-service and the loader.
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")


def validate_isin(raw: str) -> str:
    """Return the ISIN in upper case, or answer 400 Bad Request."""
    isin = raw.strip().upper()
    if not ISIN_PATTERN.match(isin):
        raise HTTPException(status_code=400,
                            detail=f"'{raw}' is not a valid ISIN (expected IN + 10 letters/digits)")
    return isin


def query(sql: str, params: tuple) -> list[tuple]:
    """Run one read-only query and return all rows.

    Values are passed separately from the SQL (%s placeholders), never pasted into it,
    so a caller can never inject SQL.

    Database UNAVAILABLE (connection lost, no free connection in time) -> clean 503; the
    real error goes to the log, not to the caller. A mistake in our SQL is NOT caught here:
    it stays a 500, so a bug is never disguised as an outage.
    """
    try:
        with pool.connection(timeout=DB_WAIT_SECONDS) as conn:
            return conn.execute(sql, params).fetchall()
    except psycopg.OperationalError as error:
        log.error("database not available: %s", error)
        raise HTTPException(status_code=503, detail="database not available") from None


# -------------------------------- endpoints ----------------------------------
@app.get("/api/v1/isins/{isin}/holders")
def holders_of_isin(isin: str):
    """Everyone who holds this fund, in any folio, sorted by name.

    The web UI shows a customer combobox only when there is more than one holder.
    """
    clean_isin = validate_isin(isin)
    rows = query("""
        SELECT DISTINCT first_holder
        FROM mf_base
        WHERE isin = %s
        ORDER BY first_holder
    """, (clean_isin,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"No investments for ISIN {clean_isin}")
    holders = [row[0] for row in rows]
    return {"isin": clean_isin, "holders": holders, "count": len(holders)}
