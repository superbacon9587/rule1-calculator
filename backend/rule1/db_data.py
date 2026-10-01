"""
Builds the same CompanyData that data.py builds from yfinance, but from the
local rule1.db tables (fundamentals_annual, fundamentals_quarterly,
analyst_growth, prices_daily, companies) that build_rule1_db.py loads from a
Town dump (Compustat / CRSP / I/B/E/S).

Only the data source changes: every series here is built the same way
data.py builds it, and analysis.py runs the same rule1.metrics math on it.

Field mapping (yfinance line item -> rule1.db column):

    Total Revenue                  -> fundamentals_annual.revt
    Diluted EPS (else Basic EPS)   -> fundamentals_annual.eps_diluted / ajex
                                      (epsfx, else epspx; see eps_diluted_source)
    Stockholders Equity            -> fundamentals_annual.ceq   [see FLAG 1]
    Ordinary Shares Number         -> fundamentals_annual.csho * ajex
    Free Cash Flow                 -> fundamentals_annual.oancf - capx
    Long Term Debt                 -> fundamentals_annual.dltt   [see FLAG 2]
    EBIT                           -> fundamentals_annual.ebit   [see FLAG 3]
    Pretax Income                  -> NO MATCH                   [see FLAG 4]
    Tax Provision                  -> fundamentals_annual.txt (unused without Pretax Income)
    statement column year          -> fundamentals_annual.fyear  [see FLAG 5]
    monthly Close history          -> prices_daily.adj_close, last close of each month
    info.trailingEps               -> fundamentals_quarterly.eps_ttm, latest quarter  [see FLAG 6]
    info.currentPrice              -> prices_daily.adj_close, latest day
    info.trailingPE                -> derived: current price / current EPS
    growth_estimates "+5y"         -> analyst_growth.meanest / 100, latest statpers
    info.longName                  -> NO MATCH (the ticker is shown instead)
    info.currency                  -> NO MATCH (assumed USD; all 12 are USD in the dump)
    info.sector / info.industry    -> NO MATCH (left empty)
    info.companyOfficers           -> NO MATCH (left empty)
    Net Income / Diluted Avg Shares-> NOT NEEDED (data.py's EPS fallback; the db's
                                      eps_diluted already falls back to basic EPS)
    info.sharesOutstanding         -> NOT NEEDED (data.py's fallback when the balance
                                      sheet has no share count; csho is always there)
    info.earningsGrowth            -> NOT USED (only the yfinance path's fallback
                                      when growth_estimates is missing)

FLAG 1: ceq is common equity; Yahoo's "Stockholders Equity" also counts
preferred stock (Compustat seq, not in rule1.db). Over the last 11 fiscal
years the two differ only for INTC (up to 1.4%) and PG (up to 0.5%).

FLAG 2: Compustat dltt includes capitalized lease obligations; Yahoo's
"Long Term Debt" does not. For MSFT FY2026 that is $109.9B vs $31.1B, so
the debt payback ratio and ROIC's invested capital run higher from here.
rule1.db has no column that separates leases back out.

FLAG 3: Yahoo's "EBIT" is Pretax Income + Interest Expense. Compustat's
ebit is operating income after depreciation (= oiadp), which leaves out
non-operating income. For MSFT FY2026: $155.2B here vs $169.0B on Yahoo.

FLAG 4: rule1.db has no pretax income (Compustat pi is in the raw dump but
build_rule1_db.py doesn't load it), so the tax rate txt / pretax can't be
computed. compute_roic then uses its existing 21% fallback for every year,
exactly as the yfinance path does when Yahoo has no Pretax Income row.
ni + txt is not a usable stand-in: it is off by up to 145% (JNJ), 39% (PG)
and 19% (INTC) in the last 11 fiscal years.

FLAG 5: fyear is Compustat's fiscal year, which for year ends in January
to May is one less than the calendar year yfinance labels the column with
(HD and WMT: fiscal year ending 2026-01-31 is fyear 2025 here, 2026 on
Yahoo). The historical PE pairs each year's EPS with that calendar year's
closes, so for those two it pairs a different twelve months of prices.

FLAG 6: eps_ttm is as reported and rule1.db has no ajexq to put it on
today's share basis. The latest quarter's ajexq is 1.0 for all 12 tickers
in the dump, so nothing is off today; a split after the latest quarter in
a future dump would be.

Per-share figures are put on today's share basis the way Yahoo reports
them: Compustat's EPS is as reported, so it is divided by `ajex`, and
closes by `cfacpr` (= adj_close; AAPL's 2020 and WMT's 2024 splits fall
inside the 10-year window).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .backtest import DEFAULT_DB_PATH
from .data import CompanyData
from .metrics import compute_roic

# A 10-year growth rate needs a starting year ten years before the latest
# one, so this loads 11 fiscal years (e.g. FY2016..FY2026).
HISTORY_YEARS = 10

REQUIRED_TABLES = ("companies", "fundamentals_annual", "fundamentals_quarterly",
                    "analyst_growth", "prices_daily")

ANNUAL_COLUMNS = ("fyear", "revt", "eps_diluted", "ceq", "csho", "ajex",
                  "oancf", "capx", "dltt", "ebit")


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
        company = conn.execute("SELECT 1 FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        if company is None:
            raise LookupError(f"{ticker} isn't in {db_path.name}")
        # The latest history_years + 1 fiscal years.
        annual_rows = conn.execute(
            f"SELECT {', '.join(ANNUAL_COLUMNS)} FROM fundamentals_annual "
            "WHERE ticker = ? ORDER BY fyear DESC LIMIT ?", (ticker, history_years + 1)
        ).fetchall()[::-1]
        quarter = conn.execute(
            "SELECT fyearq, fqtr, eps_ttm FROM fundamentals_quarterly "
            "WHERE ticker = ? AND eps_ttm IS NOT NULL ORDER BY fyearq DESC, fqtr DESC LIMIT 1", (ticker,)
        ).fetchone()
        growth = conn.execute(
            "SELECT statpers, meanest FROM analyst_growth "
            "WHERE ticker = ? AND meanest IS NOT NULL ORDER BY statpers DESC LIMIT 1", (ticker,)
        ).fetchone()
        latest_price = conn.execute(
            "SELECT date, adj_close FROM prices_daily "
            "WHERE ticker = ? AND adj_close IS NOT NULL ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        first_year = annual_rows[0]["fyear"] if annual_rows else 0
        daily_closes = conn.execute(
            "SELECT date, adj_close FROM prices_daily "
            "WHERE ticker = ? AND adj_close IS NOT NULL AND date >= ? ORDER BY date",
            (ticker, f"{first_year:04d}-01-01"),
        ).fetchall()
    finally:
        conn.close()

    # Last close of each calendar month -- what yfinance's interval="1mo" Close is.
    month_closes = {r["date"][:7]: r for r in daily_closes}.values()

    warnings: "list[str]" = []
    data = CompanyData(ticker=ticker, name=ticker, currency="USD",
                        warnings=warnings, data_source="rule1.db")
    data.source_urls = [
        f"{db_path.name}: fundamentals_annual (Compustat comp.funda)",
        f"{db_path.name}: fundamentals_quarterly (Compustat comp.fundq)",
        f"{db_path.name}: analyst_growth (I/B/E/S long-term growth)",
        f"{db_path.name}: prices_daily (CRSP daily)",
    ]
    warnings.append("Company name, sector, industry and officers aren't in rule1.db, so they're left blank "
                    "for this ticker; currency is assumed to be USD.")
    warnings.append("rule1.db has no pretax income, so ROIC uses the 21% fallback tax rate for every year, "
                    "and its EBIT is Compustat operating income rather than Yahoo's pretax income + interest.")

    equity_total: "dict[int, float]" = {}
    debt_by_year: "dict[int, float]" = {}
    for r in annual_rows:
        y = int(r["fyear"])
        ajex = _num(r["ajex"]) or 1.0

        # ---- Sales -----------------------------------------------------------
        revt = _num(r["revt"])
        if revt is not None:
            data.sales_by_year[y] = revt * 1e6

        # ---- EPS (diluted, falls back to basic inside the db) ------------------
        eps = _num(r["eps_diluted"])
        if eps is not None:
            data.eps_by_year[y] = eps / ajex

        # ---- Equity / book value per share ------------------------------------
        ceq, csho = _num(r["ceq"]), _num(r["csho"])
        if ceq is not None:
            equity_total[y] = ceq * 1e6
            if csho:
                data.equity_by_year[y] = ceq / (csho * ajex)

        # ---- Free cash flow ---------------------------------------------------
        oancf = _num(r["oancf"])
        if oancf is not None:
            data.fcf_by_year[y] = (oancf - (_num(r["capx"]) or 0.0)) * 1e6  # capx is positive in Compustat

        # ---- Long-term debt ----------------------------------------------------
        dltt = _num(r["dltt"])
        if dltt is not None:
            debt_by_year[y] = dltt * 1e6

        # ---- ROIC: NOPAT / (equity + debt) --------------------------------------
        ebit = _num(r["ebit"])
        if ebit is not None and y in equity_total:
            # tax_rate None: no pretax income in rule1.db (FLAG 4)
            roic = compute_roic(ebit * 1e6, None, equity_total[y], debt_by_year.get(y, 0.0))
            if roic is not None:
                data.roic_by_year[y] = roic

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
        closes_by_year.setdefault(int(r["date"][:4]), []).append(r["adj_close"])
    for y, eps_val in data.eps_by_year.items():
        if not eps_val or eps_val <= 0:
            continue
        if closes_by_year.get(y):
            data.pe_by_year[y] = (sum(closes_by_year[y]) / len(closes_by_year[y])) / eps_val

    # ---- Current price, TTM EPS, PE -----------------------------------------------
    if latest_price is not None:
        data.current_price = latest_price["adj_close"]
    if quarter is not None:
        data.current_eps = _num(quarter["eps_ttm"])
    if data.current_price and data.current_eps and data.current_eps > 0:
        data.current_pe = data.current_price / data.current_eps
    if latest_price is not None and quarter is not None:
        warnings.append(f"Current price is the last close in rule1.db ({latest_price['date']}); "
                        f"TTM EPS is from fiscal {quarter['fyearq']} Q{quarter['fqtr']}.")

    # ---- Analyst growth estimate (long-term, annualized) ----------------------------
    if growth is not None:
        data.analyst_growth_estimate = _num(growth["meanest"]) / 100.0

    return data
