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


# -------------------------------- helpers ----------------------------------
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


def find_holdings(isin: str) -> dict[str, list[tuple[str, int]]]:
    """Who holds this fund, and in which folios: {holder: [(folio_no, fund_id), ...]}.

    Holders and their folios come back sorted, so dropdowns never change order.
    404 if nobody holds the fund. One query answers both dropdowns.
    """
    rows = query("""
        SELECT first_holder, folio_no, fund_id
        FROM mf_base
        WHERE isin = %s
        ORDER BY first_holder, folio_no
    """, (isin,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"No investments for ISIN {isin}")
    holdings: dict[str, list[tuple[str, int]]] = {}
    for holder, folio_no, fund_id in rows:
        holdings.setdefault(holder, []).append((folio_no, fund_id))
    return holdings


# ----------------------------------- endpoints -------------------------------
@app.get("/api/v1/isins/{isin}/holders")
def holders_of_isin(isin: str):
    """Everyone who holds this fund, with their folios, sorted.

    The web UI shows a customer dropdown only when there is more than one holder, and a
    folio dropdown only when the chosen holder has more than one folio.
    """
    clean_isin = validate_isin(isin)
    holdings = find_holdings(clean_isin)
    return {
        "isin": clean_isin,
        "count": len(holdings),
        "holders": [{"name": holder, "folios": [folio for folio, _ in folios]}
                    for holder, folios in holdings.items()],
    }


# How each transaction type changes the units held. DIV is a cash payout: units unchanged.
# REV reverses an earlier purchase, so its units are taken away again.
UNIT_EFFECT = {"PUR": 1, "REI": 1, "RED": -1, "REV": -1, "DIV": 0}


def pick_one(kind: str, options: list[str], requested: str | None, context: str) -> str:
    """The same rule for holders and folios: the service enforces it, whatever the caller does.

    - exactly 1 option:   may be omitted; that one is used.
    - several options:    required; without it -> 400 listing the options.
    - given but not one of the options -> 404.
    """
    requested = requested.strip() if requested else None
    if requested is None:
        if len(options) == 1:
            return options[0]
        raise HTTPException(status_code=400, detail={
            "message": f"{context} has {len(options)} {kind}s; choose one with ?{kind}=",
            f"{kind}s": options,
        })
    if requested not in options:
        raise HTTPException(status_code=404, detail=f"{kind} {requested} not found for {context}")
    return requested


def choose_holding(isin: str, holder: str | None, folio: str | None) -> tuple[str, str, int]:
    """Holder first, then that holder's folio. Returns (holder, folio_no, fund_id).

    ISIN + holder + folio is exactly one row of mf_base (its UNIQUE key), i.e. one fund_id.
    """
    holdings = find_holdings(isin)
    chosen_holder = pick_one("holder", list(holdings), holder, f"ISIN {isin}")
    folios = dict(holdings[chosen_holder])
    chosen_folio = pick_one("folio", list(folios), folio, f"{chosen_holder} in ISIN {isin}")
    return chosen_holder, chosen_folio, folios[chosen_folio]


@app.get("/api/v1/isins/{isin}/transactions")
def transactions_of_holding(isin: str, holder: str | None = None, folio: str | None = None):
    """All transactions of ONE holding (ISIN + holder + folio), oldest first, plus the units
    held today. This is the input for the customer XIRR.
    """
    clean_isin = validate_isin(isin)
    chosen_holder, chosen_folio, fund_id = choose_holding(clean_isin, holder, folio)

    rows = query("""
        SELECT txn_id, txn_date, txn_type, amount, realized_amt,
               nav, units, stt, tds, stamp_duty
        FROM mf_txns
        WHERE fund_id = %s
        ORDER BY txn_date, txn_id
    """, (fund_id,))

    transactions, units_held = [], Decimal(0)
    for (txn_id, txn_date, txn_type, amount, realized_amt,
         nav, units, stt, tds, stamp_duty) in rows:
        units_held += UNIT_EFFECT[txn_type] * units
        transactions.append({
            "txn_id": txn_id, "txn_date": txn_date.isoformat(),
            "txn_type": txn_type, "amount": float(amount), "realized_amt": float(realized_amt),
            "nav": float(nav), "units": float(units),
            "stt": float(stt), "tds": float(tds), "stamp_duty": float(stamp_duty),
        })

    return {
        "isin": clean_isin,
        "holder": chosen_holder,
        "folio_no": chosen_folio,
        "units_held": float(units_held),
        "count": len(transactions),
        "transactions": transactions,
    }

# -------------------------------- Fund choice ----------------------------------------
@app.get("/api/v1/amcs")
def list_amcs():
    """All fund houses (AMCs) in the portfolio, sorted: the first dropdown."""
    rows = query("SELECT DISTINCT entity_name FROM mf_base ORDER BY entity_name", ())
    amcs = [row[0] for row in rows]
    return {"amcs": amcs, "count": len(amcs)}


@app.get("/api/v1/schemes")
def list_schemes(amc: str | None = None):
    """The funds (one per ISIN) held with one AMC, sorted by name: the second dropdown.

    The AMC is a query parameter (?amc=...), not part of the path, because AMC names
    contain spaces, '&' and possibly '/'.
    """
    amc = amc.strip() if amc else None
    if not amc:
        raise HTTPException(status_code=400, detail="choose an AMC with ?amc=")
    # One row per ISIN. GROUP BY: if the same ISIN was stored with slightly different scheme
    # names (e.g. renamed fund), it still appears once in the dropdown.
    rows = query("""
        SELECT isin, MIN(scheme_name) AS scheme_name
        FROM mf_base
        WHERE entity_name = %s
        GROUP BY isin
        ORDER BY scheme_name
    """, (amc,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"No funds found for AMC {amc}")
    schemes = [{"isin": isin.strip(), "scheme_name": name} for isin, name in rows]
    return {"amc": amc, "schemes": schemes, "count": len(schemes)}