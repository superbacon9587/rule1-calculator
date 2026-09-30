"""
Load a Town dump (one TICKER_PERMNO folder of CSVs per company) into rule1.db.

Writes six tables, replacing them if they already exist:

    companies               one row per ticker (name, currency, permno, gvkey, cusip)
    fundamentals_annual     <- fundamentals_annual.csv        (Compustat comp.funda)
    fundamentals_quarterly  <- fundamentals_quarterly.csv     (Compustat comp.fundq)
    analyst_growth          <- analyst_long_term_growth.csv   (I/B/E/S long-term growth)
    prices_daily            <- daily_prices.csv               (CRSP daily, as traded)
    dividends               <- dividends.csv                  (CRSP distributions)

Every CSV column is kept as-is, with a leading `ticker` column added (the
analyst file's own I/B/E/S `ticker` column is renamed `ibes_ticker`). Any
other table already in the database (backtest_signals, backtest_outcomes)
is left untouched.

Usage:
    python scripts/load_town_dump.py ../../town_dump_2026-09-14
    python scripts/load_town_dump.py DUMP_DIR --db path/to/rule1.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(__file__).resolve().parent.parent / "rule1.db"

# table name -> CSV file name inside each company folder
TABLES = {
    "fundamentals_annual": "fundamentals_annual.csv",
    "fundamentals_quarterly": "fundamentals_quarterly.csv",
    "analyst_growth": "analyst_long_term_growth.csv",
    "prices_daily": "daily_prices.csv",
    "dividends": "dividends.csv",
}

# Identifier columns with leading zeros (gvkey "012141") or letters in them.
ID_COLUMNS = {"gvkey": str, "cusip": str, "permno": str}


def load(dump_dir: Path, db_path: Path) -> "dict[str, int]":
    folders = sorted(p for p in dump_dir.iterdir() if p.is_dir() and "_" in p.name)
    if not folders:
        raise SystemExit(f"No TICKER_PERMNO folders found in {dump_dir}")

    frames: "dict[str, list[pd.DataFrame]]" = {t: [] for t in TABLES}
    companies = []
    for folder in folders:
        ticker, permno = folder.name.split("_", 1)
        for table, filename in TABLES.items():
            path = folder / filename
            if not path.exists():
                print(f"  warning: {path} missing, skipping", file=sys.stderr)
                continue
            df = pd.read_csv(path, dtype=ID_COLUMNS)
            # analyst_long_term_growth.csv carries I/B/E/S's own ticker; keep
            # it, but key every table on the folder's (exchange) ticker.
            df = df.rename(columns={"ticker": "ibes_ticker"})
            df.insert(0, "ticker", ticker)
            frames[table].append(df)

        annual = frames["fundamentals_annual"][-1] if frames["fundamentals_annual"] else None
        growth = frames["analyst_growth"][-1] if frames["analyst_growth"] else None
        companies.append({
            "ticker": ticker,
            "permno": permno,
            "gvkey": annual["gvkey"].dropna().iloc[-1] if annual is not None and len(annual) else None,
            "cusip": growth["cusip"].dropna().iloc[-1] if growth is not None and len(growth) else None,
            # I/B/E/S carries the only company name in the dump (upper case).
            "name": growth["cname"].dropna().iloc[-1] if growth is not None and len(growth) else ticker,
            "currency": annual["curcd"].dropna().iloc[-1] if annual is not None and len(annual) else None,
        })

    counts = {}
    conn = sqlite3.connect(db_path)
    try:
        pd.DataFrame(companies).to_sql("companies", conn, if_exists="replace", index=False)
        counts["companies"] = len(companies)
        for table, dfs in frames.items():
            df = pd.concat(dfs, ignore_index=True)
            df.to_sql(table, conn, if_exists="replace", index=False)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ticker ON {table} (ticker)")
            counts[table] = len(df)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_daily_ticker_date ON prices_daily (ticker, date)")
        conn.commit()
    finally:
        conn.close()
    return counts


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump_dir", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    counts = load(args.dump_dir, args.db)
    print(f"Wrote {args.db}:")
    for table, n in counts.items():
        print(f"  {table:24s} {n:>8,} rows")


if __name__ == "__main__":
    main()
