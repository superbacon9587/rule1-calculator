"""
Ties data.py (scraping) / db_data.py (local rule1.db) and metrics.py (math) together into one
per-company AnalysisResult, which report.py then prints / charts / compares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .data import CompanyData, fetch_company_data
from .db_data import db_covers, fetch_company_data_from_db
from .metrics import (
    growth_rate_windows, average_windows, debt_payback_years, compute_sticker_price,
    is_green, debt_color, assess_moat, StickerPriceResult, MoatAssessment,
)

WINDOWS = (10, 5, 3, 1)


@dataclass
class BigFiveMetric:
    label: str
    windows: "dict[int, Optional[float]]"
    longest_window: Optional[int]
    longest_value: Optional[float]
    green: Optional[bool]


@dataclass
class AnalysisResult:
    company: CompanyData
    big_five: "dict[str, BigFiveMetric]"
    debt_years: Optional[float]
    debt_color: str
    moat: MoatAssessment
    sticker: StickerPriceResult
    data_years_available: int


def _metric(label: str, series: "dict[int, float]", use_average: bool = False) -> BigFiveMetric:
    windows = (average_windows if use_average else growth_rate_windows)(series, WINDOWS)
    longest = next((w for w in WINDOWS if windows.get(w) is not None), None)
    value = windows.get(longest) if longest else None
    return BigFiveMetric(label=label, windows=windows, longest_window=longest,
                          longest_value=value, green=is_green(value))


def load_company(ticker: str, years: int = 10) -> CompanyData:
    """rule1.db for the tickers it covers, live yfinance for everything else."""
    if db_covers(ticker):
        return fetch_company_data_from_db(ticker)
    return fetch_company_data(ticker, years=years)


def analyze(ticker: str, years: int = 10) -> AnalysisResult:
    return analyze_company(load_company(ticker, years=years))


def analyze_company(company: CompanyData) -> AnalysisResult:
    sales = _metric("Sales growth", company.sales_by_year)
    eps = _metric("EPS growth", company.eps_by_year)
    equity = _metric("Equity (BVPS) growth", company.equity_by_year)
    fcf = _metric("Free cash flow growth", company.fcf_by_year)
    roic = _metric("ROIC", company.roic_by_year, use_average=True)

    big_five = {
        "roic": roic,
        "sales": sales,
        "eps": eps,
        "equity": equity,
        "fcf": fcf,
    }

    debt_years = debt_payback_years(company.long_term_debt, company.free_cash_flow_ttm)

    moat = assess_moat([m.green for m in big_five.values()])

    historical_avg_pe = None
    if company.pe_by_year:
        vals = [v for v in company.pe_by_year.values() if v and v > 0]
        if vals:
            historical_avg_pe = sum(vals) / len(vals)

    sticker = compute_sticker_price(
        current_eps=company.current_eps,
        historical_equity_growth=equity.longest_value,
        analyst_growth_estimate=company.analyst_growth_estimate,
        historical_avg_pe=historical_avg_pe or company.current_pe,
        current_price=company.current_price,
        fallback_eps_growth=eps.longest_value,
    )

    data_years = len(set(company.sales_by_year) | set(company.eps_by_year) |
                      set(company.equity_by_year) | set(company.fcf_by_year))

    return AnalysisResult(
        company=company, big_five=big_five, debt_years=debt_years,
        debt_color=debt_color(debt_years), moat=moat, sticker=sticker,
        data_years_available=data_years,
    )
