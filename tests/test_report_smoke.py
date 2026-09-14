"""
End-to-end smoke test that does NOT touch the network: builds a CompanyData
by hand (Apollo Group's numbers from the book) and runs it through the full
analysis -> terminal report -> chart pipeline, to catch crashes/typos that
the pure-math unit tests wouldn't.

Run with: python tests/test_report_smoke.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rule1.data import CompanyData
from rule1.analysis import (
    _metric, WINDOWS, AnalysisResult,
)
from rule1.metrics import debt_payback_years, compute_sticker_price, debt_color, assess_moat
from rule1.report import (
    print_report, print_comparison_table, save_growth_chart, save_debt_gauge,
    save_comparison_chart, copy_castle_image,
)

YEARS = list(range(1995, 2005))
SALES = [163, 214, 283, 391, 498, 610, 769, 1009, 1310, 1798]
EPS = [.08, .12, .19, .26, .33, .41, .60, .87, 1.30, .77]
EQUITY = [55, 88, 124, 200, 231, 261, 481, 700, 1027, 957]
CASH = [12, 15, 36, 26, 31, 83, 120, 224, 287, 400]


def build_apollo() -> CompanyData:
    company = CompanyData(
        ticker="APOL",
        name="Apollo Group (book example)",
        sector="Consumer Defensive",
        industry="Education & Training Services",
        currency="USD",
        current_price=79.0,
        current_eps=EPS[-1],
        current_pe=45.0,
    )
    company.sales_by_year = dict(zip(YEARS, SALES))
    company.eps_by_year = dict(zip(YEARS, EPS))
    company.equity_by_year = dict(zip(YEARS, EQUITY))
    company.fcf_by_year = dict(zip(YEARS, CASH))
    company.roic_by_year = {2002: 0.30, 2003: 0.32, 2004: 0.36}
    company.long_term_debt = 0.0
    company.free_cash_flow_ttm = CASH[-1]
    company.analyst_growth_estimate = 0.25
    company.source_urls = ["https://finance.yahoo.com/quote/APOL/financials/ (book example, not live data)"]
    return company


def build_result(company: CompanyData) -> AnalysisResult:
    sales = _metric("Sales growth", company.sales_by_year)
    eps = _metric("EPS growth", company.eps_by_year)
    equity = _metric("Equity (BVPS) growth", company.equity_by_year)
    fcf = _metric("Free cash flow growth", company.fcf_by_year)
    roic = _metric("ROIC", company.roic_by_year, use_average=True)
    big_five = {"roic": roic, "sales": sales, "eps": eps, "equity": equity, "fcf": fcf}

    debt_years = debt_payback_years(company.long_term_debt, company.free_cash_flow_ttm)
    moat = assess_moat([m.green for m in big_five.values()])
    sticker = compute_sticker_price(
        current_eps=company.current_eps,
        historical_equity_growth=equity.longest_value,
        analyst_growth_estimate=company.analyst_growth_estimate,
        historical_avg_pe=company.current_pe,
        current_price=company.current_price,
    )
    return AnalysisResult(
        company=company, big_five=big_five, debt_years=debt_years,
        debt_color=debt_color(debt_years), moat=moat, sticker=sticker,
        data_years_available=10,
    )


def test_full_pipeline_apollo():
    company = build_apollo()
    result = build_result(company)

    assert result.big_five["sales"].longest_value > 0.25
    assert result.moat.green_count >= 3
    assert result.sticker.sticker_price is not None and result.sticker.sticker_price > 0
    print(f"Apollo sticker price: ${result.sticker.sticker_price:.2f}, "
          f"MOS: ${result.sticker.mos_price:.2f}, moat level {result.moat.level}")

    # Should not raise.
    print_report(result)

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        gpath = save_growth_chart(result, out_dir)
        dpath = save_debt_gauge(result, out_dir)
        cpath = copy_castle_image(result, out_dir)
        assert gpath.exists() and gpath.stat().st_size > 0
        assert dpath.exists() and dpath.stat().st_size > 0
        assert cpath.exists() and cpath.stat().st_size > 0
        print(f"Charts written OK: {gpath.name}, {dpath.name}, {cpath.name}")


def test_comparison_pipeline_two_companies():
    apollo = build_apollo()
    weak = CompanyData(
        ticker="WEAK", name="Weak Co (synthetic, bad numbers)",
        current_price=40.0, current_eps=1.0, current_pe=20.0,
    )
    weak.sales_by_year = {2015: 100, 2016: 101, 2017: 99, 2018: 100, 2019: 98,
                           2020: 97, 2021: 96, 2022: 95, 2023: 94, 2024: 93}
    weak.eps_by_year = {2015: 2.0, 2016: 1.8, 2017: 1.5, 2018: 1.2, 2019: 1.0,
                         2020: 0.9, 2021: 0.8, 2022: 0.7, 2023: 0.6, 2024: 0.5}
    weak.equity_by_year = {2015: 50, 2016: 49, 2017: 48, 2018: 47, 2019: 46,
                            2020: 45, 2021: 44, 2022: 43, 2023: 42, 2024: 41}
    weak.fcf_by_year = {2015: 10, 2016: 9, 2017: 8, 2018: 7, 2019: 6,
                         2020: 5, 2021: 4, 2022: 3, 2023: 2, 2024: 1}
    weak.long_term_debt = 50.0
    weak.free_cash_flow_ttm = 1.0
    weak.source_urls = ["synthetic test data"]

    r_apollo = build_result(apollo)
    r_weak = build_result(weak)

    assert r_apollo.moat.level > r_weak.moat.level, "Apollo should have a wider moat than the synthetic weak company"

    print_comparison_table([r_apollo, r_weak])

    with tempfile.TemporaryDirectory() as td:
        path = save_comparison_chart([r_apollo, r_weak], Path(td))
        assert path.exists() and path.stat().st_size > 0
        print(f"Comparison chart written OK: {path.name}")


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}\n")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}\n")
    print(f"{len(tests)-failed}/{len(tests)} tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
