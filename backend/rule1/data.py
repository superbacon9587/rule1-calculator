"""
Fetches public financial statements (income statement, balance sheet, cash
flow statement) plus price history and analyst estimates for a ticker, and
reshapes them into the plain {year: value} series that rule1.metrics wants.

Data source: Yahoo Finance, via the `yfinance` library -- the same kind of
free source (MSN Money / Yahoo! Finance) the book itself uses in its
examples ("Free sites like MSN Money and Yahoo! Finance..."). yfinance pulls
Yahoo's public financial-statement pages, so this is doing the same job the
book describes: scraping a free site's balance sheet, income statement and
cash-flow statement rather than typing numbers in by hand.

NOTE ON DATA HISTORY: Yahoo's free statements typically go back 4-6 fiscal
years (not the full 10-15 the book prefers). The calculator reports
whatever span the data actually supports -- which is exactly what the book
itself recommends doing when a free source doesn't have ten years
("we either learn how to derive growth rates from raw numbers ourselves or
settle for five-year and one-year growth rates").

NOTE ON API DRIFT: Yahoo Finance regularly renames statement line items, and
yfinance follows along. `_find_row` below matches several likely label
spellings for each line item rather than one exact string, and everything
degrades to "not available" instead of crashing when a field truly can't be
found. Run `python -m rule1.cli TICKER --debug` to print the raw row labels
yfinance returned, which is the fastest way to fix a lookup if Yahoo renames
something again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import datetime as dt

try:
    import yfinance as yf
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "yfinance is required. Install with: pip install -r requirements.txt"
    ) from e


# ---------------------------------------------------------------------------

@dataclass
class CompanyData:
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: Optional[str] = None
    current_price: Optional[float] = None
    current_eps: Optional[float] = None
    current_pe: Optional[float] = None

    sales_by_year: "dict[int, float]" = field(default_factory=dict)
    eps_by_year: "dict[int, float]" = field(default_factory=dict)
    equity_by_year: "dict[int, float]" = field(default_factory=dict)   # book value / share
    fcf_by_year: "dict[int, float]" = field(default_factory=dict)
    roic_by_year: "dict[int, float]" = field(default_factory=dict)
    pe_by_year: "dict[int, float]" = field(default_factory=dict)

    long_term_debt: Optional[float] = None       # most recent
    free_cash_flow_ttm: Optional[float] = None    # most recent

    analyst_growth_estimate: Optional[float] = None  # 5-year, fraction (0.12 = 12%)

    officers: "list[dict]" = field(default_factory=list)  # [{"name": ..., "title": ...}, ...]

    data_source: str = "yahoo"  # "yahoo" (live yfinance) or "rule1.db" (see db_data.py)
    source_urls: "list[str]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _find_row(df, candidates: "list[str]"):
    """Case-insensitive, substring-based row lookup across possible labels."""
    if df is None or df.empty:
        return None
    index_lower = {str(i).lower(): i for i in df.index}
    for cand in candidates:
        cand_l = cand.lower()
        # exact match first
        if cand_l in index_lower:
            return df.loc[index_lower[cand_l]]
        # substring match
        for lower_label, orig_label in index_lower.items():
            if cand_l in lower_label:
                return df.loc[orig_label]
    return None


def _year_of(col) -> Optional[int]:
    try:
        return int(str(col)[:4])
    except (ValueError, TypeError):
        return None


def _series_to_year_dict(row) -> "dict[int, float]":
    out = {}
    if row is None:
        return out
    for col, val in row.items():
        yr = _year_of(col)
        if yr is None or val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if fval == fval:  # filter NaN
            out[yr] = fval
    return out


def _safe(fn, default=None, warnings: "list[str]" = None, label: str = ""):
    try:
        return fn()
    except Exception as e:  # yfinance surfaces all sorts of exceptions
        if warnings is not None:
            warnings.append(f"Couldn't fetch {label}: {e}")
        return default


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_ticker(query: str) -> str:
    """Best-effort: pass through obvious tickers, otherwise try Yahoo's search."""
    q = query.strip()
    if q.isupper() and " " not in q and len(q) <= 6:
        return q
    try:
        results = yf.Search(q, max_results=1).quotes
        if results:
            return results[0]["symbol"]
    except Exception:
        pass
    return q.upper()


def fetch_company_data(ticker: str, years: int = 10) -> CompanyData:
    ticker = ticker.strip().upper()
    t = yf.Ticker(ticker)
    warnings: "list[str]" = []

    info = _safe(lambda: t.get_info(), default={}, warnings=warnings, label="company info")
    if not info:
        info = _safe(lambda: t.info, default={}, warnings=warnings, label="company info (legacy)")

    name = info.get("longName") or info.get("shortName") or ticker
    data = CompanyData(
        ticker=ticker,
        name=name,
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency", "USD"),
        current_price=info.get("currentPrice") or info.get("regularMarketPrice"),
        current_eps=info.get("trailingEps"),
        current_pe=info.get("trailingPE"),
        warnings=warnings,
    )

    income = _safe(lambda: t.income_stmt, warnings=warnings, label="income statement")
    if income is None or income.empty:
        income = _safe(lambda: t.financials, warnings=warnings, label="income statement (legacy)")
    balance = _safe(lambda: t.balance_sheet, warnings=warnings, label="balance sheet")
    cashflow = _safe(lambda: t.cashflow, warnings=warnings, label="cash flow statement")
    if cashflow is None or (hasattr(cashflow, "empty") and cashflow.empty):
        cashflow = _safe(lambda: t.cash_flow, warnings=warnings, label="cash flow statement (legacy)")

    data.source_urls = [
        f"https://finance.yahoo.com/quote/{ticker}/financials/",
        f"https://finance.yahoo.com/quote/{ticker}/balance-sheet/",
        f"https://finance.yahoo.com/quote/{ticker}/cash-flow/",
        f"https://finance.yahoo.com/quote/{ticker}/analysis/",
    ]

    # ---- Sales (Total Revenue) --------------------------------------------------
    revenue_row = _find_row(income, ["Total Revenue", "Revenue", "Operating Revenue"])
    data.sales_by_year = _series_to_year_dict(revenue_row)

    # ---- EPS (Diluted, falls back to Net Income / Diluted Shares) --------------
    eps_row = _find_row(income, ["Diluted EPS", "Basic EPS"])
    eps_by_year = _series_to_year_dict(eps_row)
    if not eps_by_year:
        net_income_row = _find_row(income, ["Net Income Common Stockholders", "Net Income"])
        shares_row = _find_row(income, ["Diluted Average Shares", "Basic Average Shares"])
        ni = _series_to_year_dict(net_income_row)
        sh = _series_to_year_dict(shares_row)
        eps_by_year = {y: ni[y] / sh[y] for y in ni if y in sh and sh[y]}
    data.eps_by_year = eps_by_year

    # ---- Equity / book value per share ------------------------------------------
    equity_row = _find_row(balance, ["Stockholders Equity", "Common Stock Equity", "Total Equity"])
    shares_bs_row = _find_row(balance, ["Ordinary Shares Number", "Share Issued"])
    equity_total = _series_to_year_dict(equity_row)
    shares_bs = _series_to_year_dict(shares_bs_row)
    if shares_bs:
        data.equity_by_year = {y: equity_total[y] / shares_bs[y] for y in equity_total if y in shares_bs and shares_bs[y]}
    else:
        # fall back to per-share via current shares outstanding (less accurate
        # historically, since share count changes over time, but better than nothing)
        so = info.get("sharesOutstanding")
        if so:
            data.equity_by_year = {y: v / so for y, v in equity_total.items()}
        data.warnings.append(
            "Historical share counts weren't available; equity/share uses the "
            "current share count for all years, so equity growth may be a bit off "
            "if buybacks/issuance were significant."
        )

    # ---- Free cash flow ----------------------------------------------------------
    fcf_row = _find_row(cashflow, ["Free Cash Flow"])
    fcf_by_year = _series_to_year_dict(fcf_row)
    if not fcf_by_year:
        ocf_row = _find_row(cashflow, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                                        "Total Cash From Operating Activities"])
        capex_row = _find_row(cashflow, ["Capital Expenditure"])
        ocf = _series_to_year_dict(ocf_row)
        capex = _series_to_year_dict(capex_row)
        fcf_by_year = {y: ocf[y] + capex.get(y, 0.0) for y in ocf}  # capex is usually negative already
    data.fcf_by_year = fcf_by_year
    if fcf_by_year:
        latest_year = max(fcf_by_year)
        data.free_cash_flow_ttm = fcf_by_year[latest_year]

    # ---- Long-term debt (most recent) --------------------------------------------
    debt_row = _find_row(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"])
    debt_by_year = _series_to_year_dict(debt_row)
    if debt_by_year:
        data.long_term_debt = debt_by_year[max(debt_by_year)]
    else:
        data.long_term_debt = 0.0
        data.warnings.append("No long-term debt line item found; assuming $0 (verify manually).")

    # ---- ROIC per year: NOPAT / (equity + debt) -----------------------------------
    ebit_row = _find_row(income, ["EBIT", "Operating Income"])
    pretax_row = _find_row(income, ["Pretax Income"])
    tax_row = _find_row(income, ["Tax Provision"])
    ebit_by_year = _series_to_year_dict(ebit_row)
    pretax_by_year = _series_to_year_dict(pretax_row)
    tax_by_year = _series_to_year_dict(tax_row)
    for y, ebit in ebit_by_year.items():
        equity_y = equity_total.get(y)
        debt_y = debt_by_year.get(y, 0.0)
        if equity_y is None:
            continue
        tax_rate = None
        if y in pretax_by_year and pretax_by_year[y]:
            tax_rate = tax_by_year.get(y, 0.0) / pretax_by_year[y]
        from .metrics import compute_roic
        roic = compute_roic(ebit, tax_rate, equity_y, debt_y)
        if roic is not None:
            data.roic_by_year[y] = roic

    # ---- Historical PE per year (price near fiscal year end / that year's EPS) ---
    try:
        hist = t.history(period=f"{max(years, 10)}y", interval="1mo")
        for y, eps_val in data.eps_by_year.items():
            if not eps_val or eps_val <= 0:
                continue
            year_prices = hist[hist.index.year == y]["Close"] if not hist.empty else None
            if year_prices is not None and len(year_prices):
                avg_price = float(year_prices.mean())
                data.pe_by_year[y] = avg_price / eps_val
    except Exception as e:
        warnings.append(f"Couldn't compute historical PE: {e}")

    # ---- Analyst growth estimate (5-year, annualized) ------------------------------
    growth_estimate = None
    ge = _safe(lambda: t.growth_estimates, warnings=warnings, label="analyst growth estimates")
    if ge is not None and hasattr(ge, "index"):
        try:
            for idx in ge.index:
                if "5y" in str(idx).lower():
                    col = "stock" if "stock" in ge.columns else ge.columns[0]
                    growth_estimate = float(ge.loc[idx, col])
                    break
        except Exception as e:
            warnings.append(f"Couldn't parse growth estimates table: {e}")
    if growth_estimate is None:
        growth_estimate = info.get("earningsGrowth")
    data.analyst_growth_estimate = growth_estimate

    # ---- Company officers (the "Management" M) -------------------------------------
    raw_officers = info.get("companyOfficers") or []
    officers = []
    for o in raw_officers:
        nm = (o or {}).get("name")
        if not nm:
            continue
        officers.append({"name": nm.strip(), "title": (o.get("title") or "").strip()})
    data.officers = officers

    return data
