"""portfolio-service: owns the investment data (holdings and transactions) in portfolio_db."""
import logging
import os
import re
import socket
from contextlib import asynccontextmanager
from decimal import Decimal

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

DB_POOL_MAX = 5                 # max open connections per pod
DB_WAIT_SECONDS = 5.0           # max wait for a free connection

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


# ----------------------------------- helpers -------------------------------
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


def find_holders(isin: str) -> list[str]:
    """All holders of this fund, in any folio, sorted by name. 404 if nobody holds it."""
    rows = query("""
        SELECT DISTINCT first_holder
        FROM mf_base
        WHERE isin = %s
        ORDER BY first_holder
    """, (isin,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"No investments for ISIN {isin}")
    return [row[0] for row in rows]


# ------------------------------- endpoints -------------------------------
@app.get("/api/v1/isins/{isin}/holders")
def holders_of_isin(isin: str):
    """Everyone who holds this fund, in any folio, sorted by name.

    The web UI shows a customer combobox only when there is more than one holder.
    """
    clean_isin = validate_isin(isin)
    holders = find_holders(clean_isin)
    return {"isin": clean_isin, "holders": holders, "count": len(holders)}


# How each transaction type changes the units held. DIV is a cash payout: units unchanged.
# REV reverses an earlier purchase, so its units are taken away again.
UNIT_EFFECT = {"PUR": 1, "REI": 1, "RED": -1, "REV": -1, "DIV": 0}


def choose_holder(isin: str, requested: str | None) -> str:
    """Apply the holder rule. The service enforces it, whatever the caller (UI, script, ...) does.

    - exactly 1 holder:   'holder' may be omitted; that holder is used.
    - several holders:    'holder' is required; without it -> 400 listing the holders.
    - holder given but does not hold this fund -> 404.
    """
    holders = find_holders(isin)                         # 404 if nobody holds the fund
    requested = requested.strip() if requested else None
    if requested is None:
        if len(holders) == 1:
            return holders[0]
        raise HTTPException(status_code=400, detail={
            "message": f"ISIN {isin} has {len(holders)} holders; choose one with ?holder=",
            "holders": holders,
        })
    if requested not in holders:
        raise HTTPException(status_code=404, detail=f"{requested} holds no investments in ISIN {isin}")
    return requested


@app.get("/api/v1/isins/{isin}/transactions")
def transactions_of_holder(isin: str, holder: str | None = None):
    """All transactions of one holder in one fund, across ALL of the holder's folios,
    oldest first, plus the units held today. This is the input for the customer XIRR.
    """
    clean_isin = validate_isin(isin)
    chosen = choose_holder(clean_isin, holder)

    rows = query("""
        SELECT t.txn_id, b.folio_no, t.txn_date, t.txn_type, t.amount, t.realized_amt,
               t.nav, t.units, t.stt, t.tds, t.stamp_duty
        FROM mf_txns t
        JOIN mf_base b ON b.fund_id = t.fund_id
        WHERE b.isin = %s AND b.first_holder = %s
        ORDER BY t.txn_date, t.txn_id
    """, (clean_isin, chosen))

    transactions, units_held = [], Decimal(0)
    for (txn_id, folio_no, txn_date, txn_type, amount, realized_amt,
         nav, units, stt, tds, stamp_duty) in rows:
        units_held += UNIT_EFFECT[txn_type] * units      # Decimal: exact, no float rounding
        transactions.append({
            "txn_id": txn_id, "folio_no": folio_no, "txn_date": txn_date.isoformat(),
            "txn_type": txn_type, "amount": float(amount), "realized_amt": float(realized_amt),
            "nav": float(nav), "units": float(units),
            "stt": float(stt), "tds": float(tds), "stamp_duty": float(stamp_duty),
        })

    return {
        "isin": clean_isin,
        "holder": chosen,
        "units_held": float(units_held),
        "count": len(transactions),
        "transactions": transactions,
    }
