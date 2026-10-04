"""
Unit tests for rule1.metrics, checked against the worked examples printed in
the book so we know the math matches Phil Town's own numbers, not just our
own logic. Run with:

    python -m pytest tests/ -v

or, with no test runner installed:

    python tests/test_metrics.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rule1.metrics import (
    cagr, growth_rate_windows, average_windows, compute_roic, debt_payback_years,
    compute_sticker_price, pick_rule1_growth_rate, is_green, debt_color,
    assess_moat, implied_annual_return, MARR, BUYBACK_DISTORTED_TICKERS,
)


def approx(a, b, tol):
    return a is not None and abs(a - b) <= tol


def test_cagr_apollo_group_10yr_matches_book():
    # Apollo Group, 1995 -> 2004 (book, Chapter 5), values in millions/$.
    sales = cagr(163, 1798, 9)
    eps = cagr(0.08, 0.77, 9)
    equity = cagr(55, 957, 9)
    cash = cagr(12, 400, 9)

    assert approx(sales, 0.31, 0.03), f"sales CAGR {sales}"
    assert approx(eps, 0.29, 0.03), f"eps CAGR {eps}"
    assert approx(equity, 0.37, 0.03), f"equity CAGR {equity}"
    assert approx(cash, 0.48, 0.03), f"cash CAGR {cash}"
    print("Apollo 10-year Big Five growth rates match the book within 3pp: OK")


def test_cagr_apollo_group_5yr_sales_matches_book():
    # Book: 5-year sales growth rate = 29%
    sales_5y = cagr(498, 1798, 5)
    assert approx(sales_5y, 0.29, 0.02), f"sales 5y CAGR {sales_5y}"
    print("Apollo 5-year sales growth matches the book: OK")


def test_cagr_handles_negative_or_zero_start():
    assert cagr(-5, 10, 3) == 0.0
    assert cagr(0, 10, 3) == 0.0
    assert cagr(10, 10, 0) is None


def test_growth_rate_windows_falls_back_gracefully():
    # Only 4 years of data -- shouldn't be able to report a 10-year rate.
    series = {2021: 100.0, 2022: 110.0, 2023: 121.0, 2024: 133.1}
    out = growth_rate_windows(series, windows=(10, 5, 3, 1))
    assert out[10] is None
    assert out[3] is not None
    assert approx(out[3], 0.10, 0.01)
    assert approx(out[1], 0.10, 0.01)


def test_average_windows_is_a_simple_average_not_a_cagr():
    # ROIC is a ratio, not a dollar figure, so windows should average the
    # yearly values -- not compound-grow from the first year to the last.
    roic_by_year = {2015: 0.20, 2016: 0.22, 2017: 0.18, 2018: 0.25, 2019: 0.30}
    out = average_windows(roic_by_year, windows=(5, 3, 1))
    assert approx(out[1], 0.30, 1e-9)
    assert approx(out[3], (0.18 + 0.25 + 0.30) / 3, 1e-9)
    assert approx(out[5], sum(roic_by_year.values()) / 5, 1e-9)


def test_debt_payback_hr_block_matches_book():
    # Book, Chapter 5: H&R Block, $923M long-term debt, $304M free cash flow -> ~3 years.
    years = debt_payback_years(923, 304)
    assert approx(years, 3.0, 0.05), f"debt payback {years}"
    print("H&R Block debt payback matches the book (~3 years): OK")


def test_debt_color_thresholds():
    assert debt_color(0.5) == "green"
    assert debt_color(1.5) == "yellow"
    assert debt_color(2.5) == "orange"
    assert debt_color(4.0) == "red"
    assert debt_color(float("inf")) == "red"
    assert debt_color(None) == "gray"


def test_roic_formula_sane():
    # NOPAT = EBIT*(1-tax); ROIC = NOPAT / (equity+debt)
    roic = compute_roic(ebit=1000, tax_rate=0.25, equity=3000, debt=1000)
    nopat = 1000 * 0.75
    expected = nopat / 4000
    assert approx(roic, expected, 1e-9)


def test_sticker_price_harley_2000_matches_book_almost_exactly():
    """
    Book, Chapter 9, "Harley's Sticker Price, MOS Price, and ROI":
      current EPS = $0.89
      growth rate  = 24%
      historical PE = 46 (lower than the 48 default, so 46 is used)
      -> future EPS $7.65, future price $351.86,
         Sticker Price $86.97, MOS Price $43.49.
    This is the single strongest end-to-end check that the Chapter 9 math
    (the part most likely to have an off-by-one error) is implemented
    exactly as the book describes it.
    """
    result = compute_sticker_price(
        current_eps=0.89,
        historical_equity_growth=0.24,
        analyst_growth_estimate=0.24,
        historical_avg_pe=46,
    )
    assert approx(result.growth_rate, 0.24, 1e-6)
    assert approx(result.rule1_pe, 46, 1e-6)
    assert approx(result.future_eps, 7.65, 0.02), f"future eps {result.future_eps}"
    assert approx(result.future_price, 351.86, 1.0), f"future price {result.future_price}"
    assert approx(result.sticker_price, 86.97, 0.5), f"sticker price {result.sticker_price}"
    assert approx(result.mos_price, 43.49, 0.3), f"mos price {result.mos_price}"
    print(f"Harley sticker price ${result.sticker_price:.2f} / MOS ${result.mos_price:.2f} "
          f"matches the book's $86.97 / $43.49: OK")


def test_implied_annual_return_equals_marr_at_sticker_price():
    # Harley 2000 again: buying at the Sticker Price should earn exactly the
    # 15% MARR the Sticker Price was discounted at; paying the MOS price
    # (half) should earn more, paying double should earn less.
    result = compute_sticker_price(
        current_eps=0.89,
        historical_equity_growth=0.24,
        analyst_growth_estimate=0.24,
        historical_avg_pe=46,
    )
    at_sticker = implied_annual_return(result.sticker_price, result.future_price)
    assert approx(at_sticker, MARR, 1e-9), f"implied return at sticker {at_sticker}"
    assert implied_annual_return(result.mos_price, result.future_price) > MARR
    assert implied_annual_return(result.sticker_price * 2, result.future_price) < MARR
    assert implied_annual_return(None, result.future_price) is None
    assert implied_annual_return(0, result.future_price) is None
    assert implied_annual_return(50.0, None) is None


def test_pick_rule1_growth_rate_takes_the_lower_number():
    rate, source = pick_rule1_growth_rate(historical_equity_growth=0.30, analyst_growth_estimate=0.18)
    assert approx(rate, 0.18, 1e-9)
    assert "lower of" in source


def test_buyback_distorted_ticker_uses_analyst_estimate():
    # AAPL: buybacks shrink book value, so equity growth is ~0 or negative
    # and the lower-of rule would floor growth at 0%. The analyst estimate
    # is used instead, and the source says why.
    assert "AAPL" in BUYBACK_DISTORTED_TICKERS
    rate, source = pick_rule1_growth_rate(
        historical_equity_growth=-0.0069, analyst_growth_estimate=0.1175, ticker="AAPL")
    assert approx(rate, 0.1175, 1e-9)
    assert source == "analyst 5-year estimate (equity growth distorted by buybacks)"

    result = compute_sticker_price(
        current_eps=8.73, historical_equity_growth=-0.0069, analyst_growth_estimate=0.1175,
        historical_avg_pe=22.77, ticker="AAPL")
    assert approx(result.growth_rate, 0.1175, 1e-9)
    assert result.growth_rate_source == source
    assert approx(result.rule1_pe, 22.77, 1e-9)  # default PE 23.5, historical is lower


def test_other_tickers_keep_the_lower_of_rule():
    # MSFT is not on the list: lower of 20.5% equity growth and 15.7% analyst.
    assert "MSFT" not in BUYBACK_DISTORTED_TICKERS
    rate, source = pick_rule1_growth_rate(
        historical_equity_growth=0.2051, analyst_growth_estimate=0.1569, ticker="MSFT")
    assert approx(rate, 0.1569, 1e-9)
    assert "lower of" in source

    # ...and it still picks equity growth when that is the lower number.
    rate, source = pick_rule1_growth_rate(
        historical_equity_growth=0.05, analyst_growth_estimate=0.1569, ticker="MSFT")
    assert approx(rate, 0.05, 1e-9)
    assert "lower of" in source and "historical equity growth" in source


def test_buyback_distorted_ticker_keeps_cap_and_pe_floor():
    # The 60% cap and the PE floor of 5 are untouched by the override.
    capped = compute_sticker_price(
        current_eps=1.0, historical_equity_growth=0.0, analyst_growth_estimate=0.90,
        historical_avg_pe=30, ticker="AAPL")
    assert approx(capped.growth_rate, 0.60, 1e-9)
    floored = compute_sticker_price(
        current_eps=1.0, historical_equity_growth=0.0, analyst_growth_estimate=0.01,
        historical_avg_pe=30, ticker="AAPL")
    assert approx(floored.rule1_pe, 5.0, 1e-9)


def test_is_green_threshold():
    assert is_green(0.15) is True
    assert is_green(0.05) is False
    assert is_green(None) is None


def test_assess_moat_levels():
    assert assess_moat([True, True, True, True, True]).level == 5
    assert assess_moat([True, True, True, True, False]).level == 4
    assert assess_moat([True, True, True, False, False]).level == 3
    assert assess_moat([True, True, False, False, False]).level == 2
    assert assess_moat([True, False, False, False, False]).level == 1
    assert assess_moat([False, False, False, False, False]).level == 1


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
