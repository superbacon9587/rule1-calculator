"""
CompanyData: one company's financials as the plain {year: value} series that
rule1.metrics wants (sales, EPS, book value per share, free cash flow, ROIC,
PE), plus the latest price, TTM EPS, long-term debt and analyst growth
estimate.

The only loader is rule1.db_data.fetch_company_data_from_db, which fills this
from the local rule1.db. Nothing in this package fetches data over the
network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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

    data_source: str = "rule1.db"  # where the series came from (see db_data.py)
    source_urls: "list[str]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)
