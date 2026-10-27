"""Load the FinAssist investment workbook into PostgreSQL (tables mf_base and mf_txns)."""
import argparse
import os
import re
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import openpyxl
import psycopg

# Columns we need from each sheet. Found by NAME, so moving a column in Excel doesn't break us.
BASE_COLUMNS = ["ID", "FolioNo", "SchemeName", "ISIN", "SchemeCode", "SchemeCategory",
                "FirstHolder", "EntityName", "TotalUnitsHeld"]
TXN_COLUMNS = ["ID", "FundId", "TransactionDate", "TransactionType", "RealizedAmount",
               "TransactionAmount", "TransactionNAV", "TransactionUnits", "TransactionSTT",
               "TransactionTDS", "TransactionStampDuty"]

TXN_TYPES = {"PUR", "RED", "REI", "DIV", "REV"}
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")   # Indian ISINs: IN + 10 letters/digits


class RowError(ValueError):
    """A value in one row is missing or invalid, so that row is rejected."""


# ----------------------------------------------------------------- reading
def read_sheet(workbook, name, required):
    """Return a list of (excel_row_number, {column_name: value}) for every non-empty row."""
    if name not in workbook.sheetnames:
        raise SystemExit(f"Sheet '{name}' not found. Sheets in file: {workbook.sheetnames}")

    rows = workbook[name].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else None for h in next(rows)]

    missing = [c for c in required if c not in header]
    if missing:
        raise SystemExit(f"Sheet '{name}' is missing columns: {missing}")

    records = []
    for excel_row, values in enumerate(rows, start=2):          # row 1 is the header
        record = {h: v for h, v in zip(header, values) if h is not None}
        if any(v not in (None, "") for v in record.values()):   # skip completely empty rows
            records.append((excel_row, record))
    return records


# ----------------------------------------------------------------- cleaning helpers
def to_text(value, column, max_len, required=True):
    """Trim text; numbers become text without '.0'; empty -> error or None."""
    if value is None or str(value).strip() == "":
        if required:
            raise RowError(f"{column} is empty")
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)                          # 1016126706.0 -> 1016126706
    text = " ".join(str(value).split())             # trim and collapse double spaces
    if len(text) > max_len:
        raise RowError(f"{column} is longer than {max_len} characters")
    return text


def to_int(value, column):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise RowError(f"{column} is not a number: {value!r}") from None
    if number != number.to_integral_value():
        raise RowError(f"{column} is not a whole number: {value!r}")
    return int(number)


def to_decimal(value, column, places):
    """Excel gives floats (inexact). Convert to an exact Decimal with fixed decimal places."""
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise RowError(f"{column} is not a number: {value!r}") from None


def to_date(value, column):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise RowError(f"{column} is not a date: {value!r}") from None


# ----------------------------------------------------------------- validation
def clean_base(rows, rejected, warnings):
    """Turn MFBase rows into clean dicts for table mf_base. Bad rows go to `rejected`."""
    clean, seen_ids, seen_holdings = [], set(), {}
    for excel_row, r in rows:
        try:
            fund_id = to_int(r["ID"], "ID")
            isin = to_text(r["ISIN"], "ISIN", 12).upper()
            if not ISIN_PATTERN.match(isin):
                raise RowError(f"ISIN {isin!r} is not a valid Indian ISIN")

            category = to_text(r["SchemeCategory"], "SchemeCategory", 100, required=False)
            if category and not any(ch.isalpha() for ch in category):
                warnings.append(f"MFBase row {excel_row} (ID {fund_id}): "
                                f"SchemeCategory {category!r} is not a category -> stored as empty")
                category = None

            record = {
                "fund_id": fund_id,
                "entity_name": to_text(r["EntityName"], "EntityName", 120),
                "scheme_code": to_int(r["SchemeCode"], "SchemeCode"),
                "scheme_name": to_text(r["SchemeName"], "SchemeName", 200),
                "isin": isin,
                "scheme_category": category,
                "folio_no": to_text(r["FolioNo"], "FolioNo", 30),
                "first_holder": to_text(r["FirstHolder"], "FirstHolder", 50),
            }
            holding = (record["scheme_code"], record["folio_no"], record["first_holder"])
            if fund_id in seen_ids:
                raise RowError(f"duplicate ID {fund_id}")
            if holding in seen_holdings:
                raise RowError(f"same scheme, folio and holder as ID {seen_holdings[holding]}")
        except RowError as error:
            rejected.append(f"MFBase row {excel_row}: {error}")
            continue
        seen_ids.add(fund_id)
        seen_holdings[holding] = fund_id
        clean.append(record)
    return clean


def clean_txns(rows, valid_fund_ids, rejected):
    """Turn MFTxns rows into clean dicts for table mf_txns. Bad rows go to `rejected`."""
    clean, seen_ids, today = [], set(), date.today()
    for excel_row, r in rows:
        try:
            record = {
                "txn_id": to_int(r["ID"], "ID"),
                "fund_id": to_int(r["FundId"], "FundId"),
                "txn_date": to_date(r["TransactionDate"], "TransactionDate"),
                "txn_type": to_text(r["TransactionType"], "TransactionType", 3).upper(),
                "amount": to_decimal(r["TransactionAmount"], "TransactionAmount", 2),
                "realized_amt": to_decimal(r["RealizedAmount"], "RealizedAmount", 2),
                "nav": to_decimal(r["TransactionNAV"], "TransactionNAV", 4),
                "units": to_decimal(r["TransactionUnits"], "TransactionUnits", 4),
                "stt": to_decimal(r["TransactionSTT"], "TransactionSTT", 2),
                "tds": to_decimal(r["TransactionTDS"], "TransactionTDS", 2),
                "stamp_duty": to_decimal(r["TransactionStampDuty"], "TransactionStampDuty", 2),
            }
            if record["txn_id"] in seen_ids:
                raise RowError(f"duplicate ID {record['txn_id']}")
            if record["fund_id"] not in valid_fund_ids:
                raise RowError(f"FundId {record['fund_id']} has no valid row in MFBase")
            if record["txn_type"] not in TXN_TYPES:
                raise RowError(f"unknown TransactionType {record['txn_type']!r}")
            if record["txn_date"] > today:
                raise RowError(f"TransactionDate {record['txn_date']} is in the future")
            if record["units"] < 0 or record["nav"] < 0:
                raise RowError("units or NAV is negative")
            if record["txn_type"] != "DIV" and (record["units"] == 0 or record["nav"] == 0):
                raise RowError(f"{record['txn_type']} needs units and NAV greater than 0")
        except RowError as error:
            rejected.append(f"MFTxns row {excel_row}: {error}")
            continue
        seen_ids.add(record["txn_id"])
        clean.append(record)
    return clean


# ----------------------------------------------------------------- writing
# INSERT a row; if that ID is already in the table (ON CONFLICT), UPDATE it instead.
# This "upsert" makes the loader safe to run again: same file twice = same data, no duplicates.
UPSERT_BASE = """
    INSERT INTO mf_base (fund_id, entity_name, scheme_code, scheme_name, isin,
                         scheme_category, folio_no, first_holder)
    VALUES (%(fund_id)s, %(entity_name)s, %(scheme_code)s, %(scheme_name)s, %(isin)s,
            %(scheme_category)s, %(folio_no)s, %(first_holder)s)
    ON CONFLICT (fund_id) DO UPDATE SET
        entity_name = EXCLUDED.entity_name,   scheme_code = EXCLUDED.scheme_code,
        scheme_name = EXCLUDED.scheme_name,   isin = EXCLUDED.isin,
        scheme_category = EXCLUDED.scheme_category, folio_no = EXCLUDED.folio_no,
        first_holder = EXCLUDED.first_holder, loaded_at = now()
"""

UPSERT_TXN = """
    INSERT INTO mf_txns (txn_id, fund_id, txn_date, txn_type, amount, realized_amt,
                         nav, units, stt, tds, stamp_duty)
    VALUES (%(txn_id)s, %(fund_id)s, %(txn_date)s, %(txn_type)s, %(amount)s, %(realized_amt)s,
            %(nav)s, %(units)s, %(stt)s, %(tds)s, %(stamp_duty)s)
    ON CONFLICT (txn_id) DO UPDATE SET
        fund_id = EXCLUDED.fund_id,   txn_date = EXCLUDED.txn_date,   txn_type = EXCLUDED.txn_type,
        amount = EXCLUDED.amount,     realized_amt = EXCLUDED.realized_amt,
        nav = EXCLUDED.nav,           units = EXCLUDED.units,          stt = EXCLUDED.stt,
        tds = EXCLUDED.tds,           stamp_duty = EXCLUDED.stamp_duty, loaded_at = now()
"""


def write_to_database(base, txns):
    """Write all rows in ONE transaction: everything is saved, or nothing is."""
    if not os.getenv("PGPASSWORD"):
        raise SystemExit("PGPASSWORD is not set. Run the $env:PGPASSWORD line from Step 3.6 first.")

    # Connection details: environment variables if set, otherwise the port-forward defaults.
    # The password is read by psycopg itself from PGPASSWORD; it is never in this file.
    with psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "15432"),
        dbname=os.getenv("PGDATABASE", "portfolio_db"),
        user=os.getenv("PGUSER", "postgres"),
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_BASE, base)     # parents first (the foreign key needs them)
            cur.executemany(UPSERT_TXN, txns)
            holdings = cur.execute("SELECT count(*) FROM mf_base").fetchone()[0]
            transactions = cur.execute("SELECT count(*) FROM mf_txns").fetchone()[0]
    # Leaving the "with psycopg.connect" block COMMITS. Any error inside it ROLLS BACK everything.
    print(f"Database now holds {holdings} holdings and {transactions} transactions.")


# ----------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Load the investment workbook")
    parser.add_argument("--file", required=True, type=Path, help="path to the .xlsx file")
    parser.add_argument("--dry-run", action="store_true", help="validate only, write nothing")
    args = parser.parse_args()

    workbook = openpyxl.load_workbook(args.file, read_only=True, data_only=True)
    rejected, warnings = [], []

    base = clean_base(read_sheet(workbook, "MFBase", BASE_COLUMNS), rejected, warnings)
    valid_fund_ids = {b["fund_id"] for b in base}
    txns = clean_txns(read_sheet(workbook, "MFTxns", TXN_COLUMNS), valid_fund_ids, rejected)

    print(f"Valid: {len(base)} holdings, {len(txns)} transactions. "
          f"Rejected: {len(rejected)}. Warnings: {len(warnings)}.")
    for line in rejected:
        print("  REJECTED", line)
    for line in warnings:
        print("  WARNING ", line)

    if not base:
        print("Nothing valid to load.")
        return 2
    if args.dry_run:
        print("Dry run: the database was not touched.")
    else:
        try:
            write_to_database(base, txns)
        except psycopg.Error as error:
            print(f"Load FAILED, nothing was changed in the database: {error}")
            return 2
    return 1 if rejected else 0      # exit code: 0 = all good, 1 = some rows rejected, 2 = failed


if __name__ == "__main__":
    sys.exit(main())