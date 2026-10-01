"""
Build rule1.db from a Town dump (one TICKER_PERMNO folder of CSVs per company:
WRDS Compustat / CRSP / I/B/E/S), cleaning while loading, then run sanity
checks on the result.

Tables written (dropped and recreated; any other table in the file, e.g. the
R pipeline's backtest_signals / backtest_outcomes, is left untouched):

    companies               one row per ticker: ticker, permno, gvkey
    prices_daily            <- daily_prices.csv               (CRSP daily, as traded)
    fundamentals_annual     <- fundamentals_annual.csv        (Compustat comp.funda)
    fundamentals_quarterly  <- fundamentals_quarterly.csv     (Compustat comp.fundq)
    analyst_growth          <- analyst_long_term_growth.csv   (I/B/E/S long-term growth)
    dividends               <- dividends.csv                  (CRSP distributions)

Cleaning rules:
  - Every row is tied to its company by permno (the folder's permno, checked
    against the file's own permno / gvkey columns where it has them), never by
    the raw ticker string. I/B/E/S uses its own tickers (XON for Exxon, HDI for
    Harley-Davidson, TSM2 for TSMC), so a string match would drop those rows.
  - high, low and volume stay NULL where CRSP didn't track them; nothing is
    filled from close.
  - oancf is NULL, never 0, before mandatory cash flow statements (SFAS 95,
    fiscal years ending after 1988-07-15) for JNJ, KO, PG and XOM. A real
    figure from an early adopter (JNJ FY1987) is kept.
  - known_from, source and pulled_at are copied through untouched.
  - eps_diluted = epsfx, else epspx (eps_diluted_source says which).
  - eps_ttm = epsf12, else epsx12 (eps_ttm_source says which). epsx12 is
    Compustat's 12-month *basic* EPS. epspxq is NOT used as a fallback: it is a
    single quarter's EPS, so it would understate trailing EPS about 4x.
  - Same-day distributions (e.g. a regular dividend plus an extra on one
    ex-date) are all kept; `seq` numbers them within an ex-date (0 = the
    regular dividend when there is one), so the key is (ticker, ex_date, seq).
  - TSM's pilot_only_*.csv files are never read (only the five files above are).

Usage (from backend/):
    python build_rule1_db.py "PATH/TO/town_dump_2026-09-14"
    python build_rule1_db.py DUMP_DIR --db path/to/rule1.db

Exits non-zero if any sanity check fails.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(__file__).resolve().parent / "rule1.db"

FILES = {
    "prices_daily": "daily_prices.csv",
    "fundamentals_annual": "fundamentals_annual.csv",
    "fundamentals_quarterly": "fundamentals_quarterly.csv",
    "analyst_growth": "analyst_long_term_growth.csv",
    "dividends": "dividends.csv",
}

# Identifier columns with leading zeros (gvkey "012141", cusip "03783310").
ID_DTYPES = {"gvkey": str, "cusip": str}

# Tickers whose history starts before cash flow statements were required, and
# the SFAS 95 cutoff: fiscal years ending after this date had to report one.
PRE_CASH_FLOW_TICKERS = {"JNJ", "KO", "PG", "XOM"}
SFAS95_EFFECTIVE = "1988-07-15"

DDL = {
    "companies": """
        CREATE TABLE companies (
            ticker TEXT PRIMARY KEY,
            permno INTEGER NOT NULL UNIQUE,
            gvkey  TEXT NOT NULL UNIQUE
        )""",
    "prices_daily": """
        CREATE TABLE prices_daily (
            ticker TEXT NOT NULL REFERENCES companies(ticker),
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
            cfacpr REAL NOT NULL,
            open_is_derived INTEGER,
            adj_close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )""",
    "fundamentals_annual": """
        CREATE TABLE fundamentals_annual (
            ticker TEXT NOT NULL REFERENCES companies(ticker),
            fyear INTEGER NOT NULL,
            revt REAL, sale REAL, eps_diluted REAL, eps_diluted_source TEXT,
            ceq REAL, dvc REAL, oancf REAL, ni REAL, dltt REAL, dlc REAL,
            capx REAL, txt REAL, csho REAL, ajex REAL,
            prcc_f REAL, prch_f REAL, prcl_f REAL,
            che REAL, oiadp REAL, ebit REAL, at REAL, lt REAL, xint REAL,
            known_from TEXT, source TEXT, pulled_at TEXT,
            PRIMARY KEY (ticker, fyear)
        )""",
    "fundamentals_quarterly": """
        CREATE TABLE fundamentals_quarterly (
            ticker TEXT NOT NULL REFERENCES companies(ticker),
            fyearq INTEGER NOT NULL, fqtr INTEGER NOT NULL,
            eps_ttm REAL, eps_ttm_source TEXT,
            revtq REAL, niq REAL, ceqq REAL, dlttq REAL, cshoq REAL,
            rdq TEXT, known_from TEXT, source TEXT,
            PRIMARY KEY (ticker, fyearq, fqtr)
        )""",
    "analyst_growth": """
        CREATE TABLE analyst_growth (
            ticker TEXT NOT NULL REFERENCES companies(ticker),
            statpers TEXT NOT NULL,
            medest REAL, meanest REAL, numest REAL,
            known_from TEXT,
            PRIMARY KEY (ticker, statpers)
        )""",
    "dividends": """
        CREATE TABLE dividends (
            ticker TEXT NOT NULL REFERENCES companies(ticker),
            ex_date TEXT NOT NULL,
            seq INTEGER NOT NULL,
            amount REAL,
            distribution_type TEXT,
            is_regular INTEGER,
            known_from TEXT,
            PRIMARY KEY (ticker, ex_date, seq)
        )""",
}

PRIMARY_KEYS = {
    "companies": ["ticker"],
    "prices_daily": ["ticker", "date"],
    "fundamentals_annual": ["ticker", "fyear"],
    "fundamentals_quarterly": ["ticker", "fyearq", "fqtr"],
    "analyst_growth": ["ticker", "statpers"],
    "dividends": ["ticker", "ex_date", "seq"],
}

ANNUAL_RAW_COLUMNS = ["revt", "sale", "ceq", "dvc", "oancf", "ni", "dltt", "dlc", "capx",
                      "txt", "csho", "ajex", "prcc_f", "prch_f", "prcl_f",
                      "che", "oiadp", "ebit", "at", "lt", "xint"]


class LoadError(Exception):
    pass


def _bool_to_int(s: pd.Series) -> pd.Series:
    """'true'/'false' (or real bools) -> 1/0, blanks stay NULL."""
    mapped = s.map(lambda v: v if isinstance(v, bool) else
                   {"true": True, "false": False}.get(str(v).strip().lower()))
    return mapped.map({True: 1, False: 0}).astype("Int64")


def _fallback(df: pd.DataFrame, chain: "list[str]") -> "tuple[pd.Series, pd.Series]":
    """First non-null value along `chain`, and the name of the column it came from."""
    value = pd.Series(float("nan"), index=df.index)
    source = pd.Series(None, index=df.index, dtype=object)
    for col in chain:
        take = value.isna() & df[col].notna()
        value[take] = df.loc[take, col]
        source[take] = col
    return value, source


# ---------------------------------------------------------------------------
# Reading and joining
# ---------------------------------------------------------------------------

def read_companies(dump_dir: Path) -> "tuple[pd.DataFrame, dict[str, Path]]":
    folders = sorted(p for p in dump_dir.iterdir() if p.is_dir() and "_" in p.name)
    if not folders:
        raise LoadError(f"No TICKER_PERMNO folders in {dump_dir}")
    rows, by_ticker = [], {}
    for folder in folders:
        ticker, permno = folder.name.split("_", 1)
        gvkeys = set()
        for name in ("fundamentals_annual.csv", "fundamentals_quarterly.csv"):
            gvkeys |= set(pd.read_csv(folder / name, usecols=["gvkey"], dtype=str)["gvkey"].dropna())
        if len(gvkeys) != 1:
            raise LoadError(f"{folder.name}: expected one gvkey, found {sorted(gvkeys)}")
        rows.append({"ticker": ticker, "permno": int(permno), "gvkey": gvkeys.pop()})
        by_ticker[ticker] = folder
    companies = pd.DataFrame(rows)
    for col in ("ticker", "permno", "gvkey"):
        if companies[col].duplicated().any():
            raise LoadError(f"Two company folders share a {col}: {companies[companies[col].duplicated()][col].tolist()}")
    return companies, by_ticker


def read_linked(folder: Path, filename: str, companies: pd.DataFrame) -> pd.DataFrame:
    """Read one company CSV and attach `ticker` by joining on permno.

    The file's own permno column is used where it has one (and has to match the
    folder's); prices and I/B/E/S files carry no permno, so the folder's is
    used. Files with a gvkey must also match the company's gvkey."""
    folder_permno = int(folder.name.split("_", 1)[1])
    df = pd.read_csv(folder / filename, dtype=ID_DTYPES, keep_default_na=True)
    df = df.drop(columns=["ticker"], errors="ignore")  # I/B/E/S's own ticker (XON, HDI, TSM2)
    if "permno" in df.columns:
        bad = df["permno"].isna() | (df["permno"].astype("Int64") != folder_permno)
        if bad.any():
            raise LoadError(f"{folder.name}/{filename}: {int(bad.sum())} rows with a permno other than {folder_permno}")
        df["permno"] = df["permno"].astype(int)
    else:
        df["permno"] = folder_permno
    linked = df.merge(companies, on="permno", how="left", validate="m:1", suffixes=("", "_company"))
    if linked["ticker"].isna().any():
        raise LoadError(f"{folder.name}/{filename}: {int(linked['ticker'].isna().sum())} rows match no company permno")
    if "gvkey" in df.columns:
        bad = linked["gvkey"] != linked["gvkey_company"]
        if bad.any():
            raise LoadError(f"{folder.name}/{filename}: {int(bad.sum())} rows with a gvkey other than the company's")
    return linked


# ---------------------------------------------------------------------------
# Per-table cleaning
# ---------------------------------------------------------------------------

def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["ticker", "date", "open", "high", "low", "close", "volume", "cfacpr"]].copy()
    out["open_is_derived"] = _bool_to_int(df["open_is_derived_from_close"])
    if (out["cfacpr"].isna() | (out["cfacpr"] <= 0)).any():
        raise LoadError(f"{out['ticker'].iat[0]}: cfacpr missing or non-positive, can't compute adj_close")
    out["adj_close"] = out["close"] / out["cfacpr"]
    return out  # high / low / volume deliberately left as-is, NULLs included


def clean_annual(df: pd.DataFrame) -> "tuple[pd.DataFrame, int]":
    out = df[["ticker", "fyear"]].copy()
    if (out["fyear"] % 1 != 0).any():
        raise LoadError(f"{out['ticker'].iat[0]}: non-integer fyear")
    out["fyear"] = out["fyear"].astype(int)
    out["eps_diluted"], out["eps_diluted_source"] = _fallback(df, ["epsfx", "epspx"])
    for col in ANNUAL_RAW_COLUMNS:
        out[col] = df[col]
    nulled = 0
    if out["ticker"].iat[0] in PRE_CASH_FLOW_TICKERS:
        # Before SFAS 95 there was no cash flow statement: "not available", not 0.
        # Only a 0 is treated as a stand-in for "missing"; a real figure from an
        # early adopter (JNJ reported FY1987) is kept.
        pre = (df["datadate"] < SFAS95_EFFECTIVE) & (out["oancf"] == 0)
        nulled = int(pre.sum())
        out.loc[pre, "oancf"] = float("nan")
    for col in ("known_from", "source", "pulled_at"):
        out[col] = df[col]
    cols = ["ticker", "fyear", "revt", "sale", "eps_diluted", "eps_diluted_source"] + ANNUAL_RAW_COLUMNS[2:] \
        + ["known_from", "source", "pulled_at"]
    return out[cols], nulled


def clean_quarterly(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["ticker"]].copy()
    for col in ("fyearq", "fqtr"):
        if (df[col] % 1 != 0).any():
            raise LoadError(f"{out['ticker'].iat[0]}: non-integer {col}")
        out[col] = df[col].astype(int)
    out["eps_ttm"], out["eps_ttm_source"] = _fallback(df, ["epsf12", "epsx12"])
    for col in ("revtq", "niq", "ceqq", "dlttq", "cshoq", "rdq", "known_from", "source"):
        out[col] = df[col]
    return out


def clean_analyst(df: pd.DataFrame) -> pd.DataFrame:
    return df[["ticker", "statpers", "medest", "meanest", "numest", "known_from"]].copy()


def clean_dividends(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["ticker", "ex_date", "amount", "distribution_type", "known_from"]].copy()
    out["is_regular"] = _bool_to_int(df["is_regular"])
    # Regular dividend first within an ex-date, then cash before stock/other, then file order.
    out["_order"] = range(len(out))
    out = out.sort_values(["ex_date", "is_regular", "distribution_type", "_order"],
                          ascending=[True, False, True, True], na_position="last")
    out["seq"] = out.groupby("ex_date").cumcount()
    return out[["ticker", "ex_date", "seq", "amount", "distribution_type", "is_regular", "known_from"]]


CLEANERS = {
    "prices_daily": clean_prices,
    "fundamentals_quarterly": clean_quarterly,
    "analyst_growth": clean_analyst,
    "dividends": clean_dividends,
}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def build(dump_dir: Path) -> "tuple[dict[str, pd.DataFrame], dict, dict]":
    """Returns (cleaned tables, raw row counts per (table, ticker), notes)."""
    companies, folders = read_companies(dump_dir)
    tables: "dict[str, list[pd.DataFrame]]" = {t: [] for t in FILES}
    raw_counts: "dict[tuple[str, str], int]" = {}
    raw_frames: "dict[tuple[str, str], pd.DataFrame]" = {}
    notes = {"oancf_nulled": {}}
    for ticker, folder in folders.items():
        for table, filename in FILES.items():
            raw = read_linked(folder, filename, companies)
            raw_counts[(table, ticker)] = len(raw)
            raw_frames[(table, ticker)] = raw
            if table == "fundamentals_annual":
                cleaned, nulled = clean_annual(raw)
                notes["oancf_nulled"][ticker] = nulled
            else:
                cleaned = CLEANERS[table](raw)
            tables[table].append(cleaned)
    out = {"companies": companies}
    out.update({t: pd.concat(dfs, ignore_index=True) for t, dfs in tables.items()})
    notes["raw_frames"] = raw_frames
    return out, raw_counts, notes


def write(db_path: Path, tables: "dict[str, pd.DataFrame]") -> None:
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            # Children first, so a REFERENCES on companies never blocks the drop.
            for name in [t for t in DDL if t != "companies"] + ["companies"]:
                conn.execute(f"DROP TABLE IF EXISTS {name}")
            for name in DDL:
                conn.execute(DDL[name])
                df = tables[name]
                table_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
                if list(df.columns) != table_cols:
                    raise LoadError(f"{name}: cleaned columns {list(df.columns)} != table columns {table_cols}")
                df = df.astype(object).where(df.notna(), None)  # NaN / <NA> -> SQL NULL
                cols = list(df.columns)
                conn.executemany(
                    f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    df.itertuples(index=False, name=None))
            conn.execute("CREATE INDEX idx_fundq_ticker_known ON fundamentals_quarterly (ticker, known_from)")
            conn.execute("CREATE INDEX idx_funda_ticker_known ON fundamentals_annual (ticker, known_from)")
            conn.execute("CREATE INDEX idx_div_ticker_date ON dividends (ticker, ex_date)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sanity checks (run against what's actually in the file)
# ---------------------------------------------------------------------------

def sanity_checks(db_path: Path, raw_counts: dict, notes: dict) -> "list[str]":
    """Returns a list of failures (empty = all passed); prints each check."""
    failures: "list[str]" = []
    raw_frames = notes["raw_frames"]

    def check(ok: bool, label: str, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label}: {detail}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    try:
        # 1. Duplicate primary keys
        for table, pk in PRIMARY_KEYS.items():
            k = ", ".join(pk)
            dups = q(f"SELECT COUNT(*) FROM (SELECT {k} FROM {table} GROUP BY {k} HAVING COUNT(*) > 1)")[0][0]
            check(dups == 0, f"no duplicate ({k}) in {table}", f"{dups} duplicated keys" if dups else "")

        # 2. Prices: no zero/negative prices
        for col in ("open", "high", "low", "close"):
            n = q(f"SELECT COUNT(*) FROM prices_daily WHERE {col} <= 0")[0][0]
            check(n == 0, f"no zero/negative prices_daily.{col}", f"{n} rows" if n else "")
        n = q("SELECT COUNT(*) FROM prices_daily WHERE close IS NULL")[0][0]
        check(n == 0, "no NULL prices_daily.close", f"{n} rows" if n else "")

        # 3. adj_close = close / cfacpr
        n = q("SELECT COUNT(*) FROM prices_daily WHERE adj_close IS NULL "
              "OR ABS(adj_close - close / cfacpr) > 1e-9 * MAX(1, ABS(adj_close))")[0][0]
        check(n == 0, "adj_close = close / cfacpr on every row", f"{n} rows off" if n else "")

        # 4. Row counts per ticker per table match the source CSV
        mismatches = []
        for (table, ticker), expected in sorted(raw_counts.items()):
            got = q(f"SELECT COUNT(*) FROM {table} WHERE ticker = ?", ticker)[0][0]
            if got != expected:
                mismatches.append(f"{table}/{ticker}: csv {expected}, db {got}")
        check(not mismatches, f"row count per ticker matches the CSV ({len(raw_counts)} ticker-tables)",
              "; ".join(mismatches))
        n = q("SELECT COUNT(*) FROM analyst_growth WHERE ticker = 'XOM'")[0][0]
        first = q("SELECT MIN(statpers) FROM analyst_growth WHERE ticker = 'XOM'")[0][0]
        check(n == raw_counts[("analyst_growth", "XOM")] and first is not None and first < "1999-12-01",
              "XOM analyst growth (I/B/E/S ticker XON) all loaded, incl. pre-merger Exxon Corp rows",
              f"{n} rows from {first}")

        # 5. high / low / volume left NULL exactly where the source is blank (nothing filled from close)
        bad = []
        for (table, ticker), raw in raw_frames.items():
            if table != "prices_daily":
                continue
            for col in ("high", "low", "volume"):
                got = q(f"SELECT COUNT(*) FROM prices_daily WHERE ticker = ? AND {col} IS NULL", ticker)[0][0]
                if got != int(raw[col].isna().sum()):
                    bad.append(f"{ticker}.{col}: csv {int(raw[col].isna().sum())} blank, db {got} NULL")
        total_null = q("SELECT COUNT(*) FROM prices_daily WHERE high IS NULL OR low IS NULL")[0][0]
        check(not bad, "high/low/volume NULLs preserved, nothing filled from close",
              "; ".join(bad) or f"{total_null:,} rows keep a NULL high/low")

        # 6. oancf: NULL (not 0) before cash flow statements for the four oldest tickers
        for t in sorted(PRE_CASH_FLOW_TICKERS):
            zeros = q("SELECT COUNT(*) FROM fundamentals_annual WHERE ticker = ? AND oancf = 0", t)[0][0]
            first = q("SELECT MIN(fyear) FROM fundamentals_annual WHERE ticker = ? AND oancf IS NOT NULL", t)[0][0]
            nulls = q("SELECT COUNT(*) FROM fundamentals_annual WHERE ticker = ? AND oancf IS NULL", t)[0][0]
            raw = raw_frames[("fundamentals_annual", t)]
            raw_nulls = int(raw["oancf"].isna().sum()) + notes["oancf_nulled"][t]
            check(zeros == 0 and nulls == raw_nulls and first is not None and first >= 1987,
                  f"{t} oancf NULL (not 0) before cash flow statements",
                  f"{nulls} NULL years, first reported FY{first}, {zeros} zeros, "
                  f"{notes['oancf_nulled'][t]} pre-{SFAS95_EFFECTIVE[:4]} zeros turned into NULL")

        # 7. Point-in-time columns carried through untouched
        bad = []
        for (table, ticker), raw in raw_frames.items():
            if table not in ("fundamentals_annual", "fundamentals_quarterly"):
                continue
            key = ["fyear"] if table == "fundamentals_annual" else ["fyearq", "fqtr"]
            cols = ["known_from", "source"] + (["pulled_at"] if table == "fundamentals_annual" else [])
            db = pd.read_sql(f"SELECT {', '.join(key + cols)} FROM {table} WHERE ticker = ?", conn, params=(ticker,))
            r = raw[key + cols].copy()
            for kc in key:
                r[kc] = r[kc].astype(int)
            merged = r.merge(db, on=key, suffixes=("_raw", "_db"))
            for c in cols:
                diff = ~((merged[c + "_raw"] == merged[c + "_db"]) |
                         (merged[c + "_raw"].isna() & merged[c + "_db"].isna()))
                if diff.any():
                    bad.append(f"{table}/{ticker}.{c}: {int(diff.sum())} rows differ")
        for table in ("analyst_growth", "dividends"):
            n = q(f"SELECT COUNT(*) FROM {table} WHERE known_from IS NULL")[0][0]
            if n:
                bad.append(f"{table}: {n} NULL known_from")
        check(not bad, "known_from / source / pulled_at identical to the raw files", "; ".join(bad))

        # 8. Every company has the inputs the current-value calculations need
        missing = []
        for (t,) in q("SELECT ticker FROM companies ORDER BY ticker"):
            for label, sql in (
                ("latest price", "SELECT COUNT(*) FROM prices_daily WHERE ticker = ?"),
                ("TTM EPS", "SELECT COUNT(*) FROM fundamentals_quarterly WHERE ticker = ? AND eps_ttm IS NOT NULL"),
                ("analyst growth", "SELECT COUNT(*) FROM analyst_growth WHERE ticker = ? AND meanest IS NOT NULL"),
                ("11 FYs of revt/eps/ceq/csho/oancf/capx/dltt/ebit",
                 "SELECT COUNT(*) FROM (SELECT * FROM fundamentals_annual WHERE ticker = ? ORDER BY fyear DESC LIMIT 11) "
                 "WHERE revt IS NOT NULL AND eps_diluted IS NOT NULL AND ceq IS NOT NULL AND csho IS NOT NULL "
                 "AND oancf IS NOT NULL AND capx IS NOT NULL AND dltt IS NOT NULL AND ebit IS NOT NULL"),
            ):
                n = q(sql, t)[0][0]
                if n == 0 or (label.startswith("11") and n < 11):
                    missing.append(f"{t}: {label} ({n})")
        check(not missing, "all 12 companies have current-value and 10-year inputs", "; ".join(missing))

        # Informational: OHLC consistency in the source data (not altered by the loader)
        n = q("SELECT COUNT(*) FROM prices_daily WHERE high < low OR close > high OR close < low")[0][0]
        print(f"  [INFO] {n} price rows where close falls outside [low, high] in the source data (left as-is)")
        for row in q("SELECT eps_diluted_source, COUNT(*) FROM fundamentals_annual GROUP BY 1 ORDER BY 1"):
            print(f"  [INFO] fundamentals_annual.eps_diluted from {row[0]}: {row[1]} rows")
        for row in q("SELECT eps_ttm_source, COUNT(*) FROM fundamentals_quarterly GROUP BY 1 ORDER BY 1"):
            print(f"  [INFO] fundamentals_quarterly.eps_ttm from {row[0]}: {row[1]} rows")
        n = q("SELECT COUNT(*) FROM dividends WHERE seq > 0")[0][0]
        print(f"  [INFO] {n} extra same-ex-date distributions kept (seq > 0)")
        print(f"  [INFO] latest price date: {q('SELECT MAX(date) FROM prices_daily')[0][0]}; "
              f"latest quarter end known_from: {q('SELECT MAX(known_from) FROM fundamentals_quarterly')[0][0]}")
    finally:
        conn.close()
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump_dir", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)

    try:
        tables, raw_counts, notes = build(args.dump_dir)
    except LoadError as e:
        print(f"Load failed: {e}", file=sys.stderr)
        return 1
    write(args.db, tables)
    print(f"Wrote {args.db}:")
    for name, df in tables.items():
        print(f"  {name:24s} {len(df):>8,} rows")
    print("Sanity checks:")
    failures = sanity_checks(args.db, raw_counts, notes)
    if failures:
        print(f"{len(failures)} sanity check(s) FAILED", file=sys.stderr)
        return 1
    print("All sanity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
