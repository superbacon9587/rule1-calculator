"""
Builds the same CompanyData that data.py builds from yfinance, but from the
local rule1.db tables (fundamentals_annual, fundamentals_quarterly,
analyst_growth, prices_daily, companies) that scripts/load_town_dump.py
loads from a Town dump (Compustat / CRSP / I/B/E/S).

Only the data source changes: every series here is built the same way
data.py builds it, and analysis.py runs the same rule1.metrics math on it.

Field mapping (yfinance line item -> rule1.db column):

    Total Revenue                  -> fundamentals_annual.revt
    Diluted EPS                    -> fundamentals_annual.epsfx / ajex
      (fallback: Net Income / Diluted Average Shares -> ni / cshfd, / ajex)
    Stockholders Equity            -> fundamentals_annual.seq
    Ordinary Shares Number         -> fundamentals_annual.csho * ajex
    Free Cash Flow                 -> fundamentals_annual.oancf - capx
    Long Term Debt                 -> fundamentals_annual.dltt   [see FLAG 1]
    EBIT                           -> fundamentals_annual.pi + xint   [see FLAG 2]
    Pretax Income                  -> fundamentals_annual.pi
    Tax Provision                  -> fundamentals_annual.txt
    monthly Close history          -> prices_daily.close / cfacpr, last close of each month
    info.trailingEps               -> fundamentals_quarterly.epsf12 / ajexq, latest quarter
    info.currentPrice              -> prices_daily.close / cfacpr, latest day
    info.trailingPE                -> derived: current price / current EPS
    growth_estimates "+5y"         -> analyst_growth.meanest / 100, latest statpers
    info.longName                  -> companies.name
    info.currency                  -> companies.currency
    info.sector / info.industry    -> NO MATCH (left empty)
    info.companyOfficers           -> NO MATCH (left empty)
    info.earningsGrowth            -> NOT USED (only the yfinance path's fallback
                                      when growth_estimates is missing)

FLAG 1: Compustat dltt includes capitalized lease obligations; Yahoo's
"Long Term Debt" does not. For MSFT FY2026 that is $109.9B vs $31.1B, so
the debt payback ratio and ROIC's invested capital run higher from here.
The dump has no column that separates leases back out.

FLAG 2: Yahoo's "EBIT" is Pretax Income + Interest Expense (not operating
income), so it maps to pi + xint, not Compustat's own `ebit` (= oiadp).
When xint is blank (AAPL stopped reporting it in FY2024) it counts as 0.

Per-share figures are put on today's share basis the way Yahoo reports
them: Compustat's EPS is as reported, so it is divided by `ajex`, and
closes by `cfacpr` (AAPL's 2020 and WMT's 2024 splits fall inside the
10-year window). Years are the calendar year of the fiscal year end
(`datadate`), which is how yfinance labels its statement columns.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .backtest import DEFAULT_DB_PATH
from .data import CompanyData
from .metrics import compute_roic

# A 10-year growth rate needs a starting year ten years before the latest
# one, so this loads 11 fiscal year ends (e.g. FY2016..FY2026).
HISTORY_YEARS = 10

REQUIRED_TABLES = ("companies", "fundamentals_annual", "fundamentals_quarterly",
                    "analyst_growth", "prices_daily")

ANNUAL_COLUMNS = ("datadate", "revt", "epsfx", "ni", "cshfd", "seq", "csho", "ajex",
                  "oancf", "capx", "dltt", "pi", "xint", "txt")


def _connect(db_path: Path) -> sqlite3.Connection:
    # mode=ro so a read never creates or modifies the file.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def db_covers(ticker: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """True when rule1.db exists, has every table this module reads, and has
    a companies row for `ticker`."""
    db_path = Path(db_path)
    if not db_path.exists():
        return False
    try:
        conn = _connect(db_path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not set(REQUIRED_TABLES) <= tables:
                return False
            row = conn.execute("SELECT 1 FROM companies WHERE ticker = ?", (ticker.strip().upper(),)).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # filter NaN


def fetch_company_data_from_db(ticker: str, db_path: Path = DEFAULT_DB_PATH,
                                history_years: int = HISTORY_YEARS) -> CompanyData:
    ticker = ticker.strip().upper()
    db_path = Path(db_path)
    conn = _connect(db_path)
    try:
        company = conn.execute("SELECT name, currency FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        if company is None:
            raise LookupError(f"{ticker} isn't in {db_path.name}")
        annual_rows = conn.execute(
            f"SELECT {', '.join(ANNUAL_COLUMNS)} FROM fundamentals_annual "
            "WHERE ticker = ? ORDER BY datadate", (ticker,)
        ).fetchall()
        quarter = conn.execute(
            "SELECT datadate, epsf12, ajexq FROM fundamentals_quarterly "
            "WHERE ticker = ? AND epsf12 IS NOT NULL ORDER BY datadate DESC LIMIT 1", (ticker,)
        ).fetchone()
        growth = conn.execute(
            "SELECT statpers, meanest FROM analyst_growth "
            "WHERE ticker = ? AND meanest IS NOT NULL ORDER BY statpers DESC LIMIT 1", (ticker,)
        ).fetchone()
        latest_price = conn.execute(
            "SELECT date, close, cfacpr FROM prices_daily "
            "WHERE ticker = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        first_year = int(annual_rows[-1]["datadate"][:4]) - history_years if annual_rows else 0
        daily_closes = conn.execute(
            "SELECT date, close, cfacpr FROM prices_daily "
            "WHERE ticker = ? AND close IS NOT NULL AND date >= ? ORDER BY date",
            (ticker, f"{first_year:04d}-01-01"),
        ).fetchall()
    finally:
        conn.close()

    # Last close of each calendar month -- what yfinance's interval="1mo" Close is.
    month_closes = {r["date"][:7]: r for r in daily_closes}.values()

    warnings: "list[str]" = []
    data = CompanyData(ticker=ticker, name=company["name"] or ticker, currency=company["currency"] or "USD",
                        warnings=warnings, data_source="rule1.db")
    data.source_urls = [
        f"{db_path.name}: fundamentals_annual (Compustat comp.funda)",
        f"{db_path.name}: fundamentals_quarterly (Compustat comp.fundq)",
        f"{db_path.name}: analyst_growth (I/B/E/S long-term growth)",
        f"{db_path.name}: prices_daily (CRSP daily)",
    ]
    warnings.append("Sector, industry and company officers aren't in rule1.db, so they're left blank for this ticker.")

    # ---- keep the latest HISTORY_YEARS + 1 fiscal year ends -------------------
    by_year = {}
    for r in annual_rows:
        by_year[int(r["datadate"][:4])] = r  # later datadate in the same year wins
    if by_year:
        last_year = max(by_year)
        by_year = {y: r for y, r in by_year.items() if y >= last_year - history_years}

    equity_total: "dict[int, float]" = {}
    debt_by_year: "dict[int, float]" = {}
    xint_blank = []
    for y, r in sorted(by_year.items()):
        ajex = _num(r["ajex"]) or 1.0

        # ---- Sales -----------------------------------------------------------
        revt = _num(r["revt"])
        if revt is not None:
            data.sales_by_year[y] = revt * 1e6

        # ---- EPS (diluted, falls back to net income / diluted shares) ---------
        eps = _num(r["epsfx"])
        if eps is None:
            ni, cshfd = _num(r["ni"]), _num(r["cshfd"])
            if ni is not None and cshfd:
                eps = ni / cshfd
        if eps is not None:
            data.eps_by_year[y] = eps / ajex

        # ---- Equity / book value per share ------------------------------------
        seq, csho = _num(r["seq"]), _num(r["csho"])
        if seq is not None:
            equity_total[y] = seq * 1e6
            if csho:
                data.equity_by_year[y] = seq / (csho * ajex)

        # ---- Free cash flow ---------------------------------------------------
        oancf = _num(r["oancf"])
        if oancf is not None:
            data.fcf_by_year[y] = (oancf - (_num(r["capx"]) or 0.0)) * 1e6  # capx is positive in Compustat

        # ---- Long-term debt ----------------------------------------------------
        dltt = _num(r["dltt"])
        if dltt is not None:
            debt_by_year[y] = dltt * 1e6

        # ---- ROIC: NOPAT / (equity + debt) --------------------------------------
        pi, xint, txt = _num(r["pi"]), _num(r["xint"]), _num(r["txt"])
        if pi is not None and y in equity_total:
            if xint is None:
                xint_blank.append(y)
            ebit = (pi + (xint or 0.0)) * 1e6
            tax_rate = (txt or 0.0) / pi if pi else None
            roic = compute_roic(ebit, tax_rate, equity_total[y], debt_by_year.get(y, 0.0))
            if roic is not None:
                data.roic_by_year[y] = roic

    if xint_blank:
        warnings.append(f"Interest expense (xint) is blank for FY{', FY'.join(map(str, xint_blank))}; "
                        "ROIC's EBIT uses pretax income alone for those years.")

    if data.fcf_by_year:
        data.free_cash_flow_ttm = data.fcf_by_year[max(data.fcf_by_year)]
    if debt_by_year:
        data.long_term_debt = debt_by_year[max(debt_by_year)]
    else:
        data.long_term_debt = 0.0
        warnings.append("No long-term debt (dltt) in rule1.db; assuming $0 (verify manually).")

    # ---- Historical PE per year (average month-end close / that year's EPS) -----
    closes_by_year: "dict[int, list[float]]" = {}
    for r in month_closes:
        close, cfacpr = _num(r["close"]), _num(r["cfacpr"]) or 1.0
        if close is not None:
            closes_by_year.setdefault(int(r["date"][:4]), []).append(close / cfacpr)
    for y, eps_val in data.eps_by_year.items():
        if not eps_val or eps_val <= 0:
            continue
        if closes_by_year.get(y):
            data.pe_by_year[y] = (sum(closes_by_year[y]) / len(closes_by_year[y])) / eps_val

    # ---- Current price, TTM EPS, PE -----------------------------------------------
    if latest_price is not None:
        data.current_price = _num(latest_price["close"]) / (_num(latest_price["cfacpr"]) or 1.0)
    if quarter is not None:
        data.current_eps = _num(quarter["epsf12"]) / (_num(quarter["ajexq"]) or 1.0)
    if data.current_price and data.current_eps and data.current_eps > 0:
        data.current_pe = data.current_price / data.current_eps
    if latest_price is not None and quarter is not None:
        warnings.append(f"Current price is the last close in rule1.db ({latest_price['date']}); "
                        f"TTM EPS is from the quarter ending {quarter['datadate']}.")

    # ---- Analyst growth estimate (long-term, annualized) ----------------------------
    if growth is not None:
        data.analyst_growth_estimate = _num(growth["meanest"]) / 100.0

    return data
