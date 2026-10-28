"""portfolio-service: owns the investment data (holdings and transactions) in portfolio_db."""
import logging
import os
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
POD_NAME = os.getenv("POD_NAME", socket.gethostname())
log = logging.getLogger(SERVICE)

# Database settings use PostgreSQL's standard names (PGHOST, PGPORT, ...), the same as the
# loader. psycopg reads them from the environment by itself, so no connection string with a
# password is ever built in our code. Fail fast at startup if one is missing.
REQUIRED_SETTINGS = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
if missing:
    raise RuntimeError(f"Missing database settings: {', '.join(missing)}. "
                       "Set them in .env (PGPASSWORD in your shell) or in Kubernetes.")

DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))                 # max open connections per pod
DB_WAIT_SECONDS = float(os.getenv("DB_WAIT_SECONDS", "5"))       # max wait for a free connection

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
    pool.close()                       # on shutdown: close connections cleanly


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
    except Exception as error:         # pool timeout, connection refused, auth failure ...
        log.warning("not ready: %s", error)
        raise HTTPException(status_code=503, detail="database not reachable") from None
    return {"status": "ready", "service": SERVICE, "pod": POD_NAME}
