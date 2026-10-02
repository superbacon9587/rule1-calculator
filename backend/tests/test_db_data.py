"""
Tests for rule1/db_data.py, run against a tiny throwaway rule1.db built
here (never the real one), checking the rule1.db field mapping, plus the
rule that the app serves only SUPPORTED_TICKERS and never imports yfinance.

Run with: python tests/test_db_data.py
"""

import math
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rule1.db_data import SUPPORTED_TICKERS, UnsupportedTickerError, db_covers, fetch_company_data_from_db
from rule1.analysis import analyze_company, load_company

# FY2012..FY2024 (13 fiscal years), a 2:1 split between FY2019 and FY2020 (ajex 2 -> 1).
YEARS = list(range(2012, 2025))


def _build_db(tmp: str) -> Path:
    db = Path(tmp) / "rule1.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE companies (ticker, permno, gvkey)")
    conn.execute("INSERT INTO companies VALUES ('TST', 1, '000001')")
    conn.execute("CREATE TABLE fundamentals_annual (ticker, fyear, revt, eps_diluted, ceq, csho, ajex, "
                 "oancf, capx, dltt, ebit)")
    for i, y in enumerate(YEARS):
        ajex = 2.0 if y < 2020 else 1.0
        conn.execute("INSERT INTO fundamentals_annual VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            "TST", y,
            100.0 * 1.1 ** i,            # revt ($M)
            2.0 * 1.1 ** i * ajex,       # eps_diluted, as reported (pre-split years are 2x)
            500.0 * 1.1 ** i,            # ceq ($M)
            100.0 / ajex,                # csho (M), as reported
            ajex,
            80.0, 30.0,                  # oancf, capx (capx positive, Compustat style)
            200.0,                       # dltt
            130.0,                       # ebit
        ))
    conn.execute("CREATE TABLE fundamentals_quarterly (ticker, fyearq, fqtr, eps_ttm)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', 2023, 4, 4.0)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', 2024, 2, 6.0)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', 2024, 1, 5.0)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', 2024, 3, NULL)")
    conn.execute("CREATE TABLE analyst_growth (ticker, statpers, meanest, medest)")
    conn.execute("INSERT INTO analyst_growth VALUES ('TST', '2024-05-16', 9.0, 9.5)")
    conn.execute("INSERT INTO analyst_growth VALUES ('TST', '2024-06-20', 12.0, 12.5)")
    conn.execute("CREATE TABLE prices_daily (ticker, date, adj_close)")
    for y in YEARS:
        for m in range(1, 13):
            conn.execute("INSERT INTO prices_daily VALUES ('TST', ?, ?)", (f"{y}-{m:02d}-15", 999.0))
            conn.execute("INSERT INTO prices_daily VALUES ('TST', ?, ?)", (f"{y}-{m:02d}-28", 40.0))
    conn.commit()
    conn.close()
    return db


def _load():
    tmp = tempfile.mkdtemp()
    return fetch_company_data_from_db("TST", db_path=_build_db(tmp))


def test_db_covers_only_loaded_tickers():
    with tempfile.TemporaryDirectory() as tmp:
        db = _build_db(tmp)
        assert db_covers("tst", db)
        assert not db_covers("MSFT", db)
        assert not db_covers("TST", Path(tmp) / "missing.db")


def test_window_is_ten_years_of_fiscal_years():
    c = _load()
    assert sorted(c.sales_by_year) == list(range(2014, 2025)), sorted(c.sales_by_year)


def test_ten_year_cagr_uses_full_window():
    r = analyze_company(_load())
    assert math.isclose(r.big_five["sales"].windows[10], 0.10, rel_tol=1e-9)
    assert r.big_five["sales"].longest_window == 10


def test_per_share_figures_are_split_adjusted():
    c = _load()
    # EPS grows a smooth 10%/yr once divided by ajex -- no jump at the split.
    assert math.isclose(c.eps_by_year[2020] / c.eps_by_year[2019], 1.1, rel_tol=1e-9)
    assert math.isclose(c.equity_by_year[2020] / c.equity_by_year[2019], 1.1, rel_tol=1e-9)


def test_fcf_debt_and_units():
    c = _load()
    assert c.fcf_by_year[2024] == 50.0e6       # oancf - capx, $M -> $
    assert c.free_cash_flow_ttm == 50.0e6
    assert c.long_term_debt == 200.0e6


def test_roic_uses_db_ebit_and_fallback_tax_rate():
    c = _load()
    # rule1.db has no pretax income -> compute_roic's 21% fallback, with a warning
    equity = 500.0 * 1.1 ** (2023 - 2012)
    assert math.isclose(c.roic_by_year[2023], 130.0 * (1 - 0.21) / (equity + 200.0), rel_tol=1e-9)
    assert any("pretax" in w for w in c.warnings)


def test_pe_uses_month_end_adj_closes():
    c = _load()
    # month-end adj_close is 40 every year; mid-month 999 is ignored
    assert math.isclose(c.pe_by_year[2024], 40.0 / c.eps_by_year[2024], rel_tol=1e-9)
    assert math.isclose(c.pe_by_year[2015], 40.0 / c.eps_by_year[2015], rel_tol=1e-9)


def test_current_values_use_latest_rows():
    c = _load()
    assert c.current_price == 40.0
    assert c.current_eps == 6.0
    assert math.isclose(c.analyst_growth_estimate, 0.12)
    assert c.data_source == "rule1.db"


def test_unsupported_ticker_raises_friendly_error_listing_supported():
    try:
        load_company("tsla")
    except UnsupportedTickerError as e:
        msg = str(e)
    else:
        raise AssertionError("load_company accepted a ticker outside SUPPORTED_TICKERS")
    assert "TSLA" in msg and "only supports these 12 tickers" in msg, msg
    assert all(t in msg for t in SUPPORTED_TICKERS), msg


def test_api_returns_friendly_error_for_unsupported_ticker():
    import app as app_module
    client = app_module.app.test_client()
    resp = client.get("/api/analyze?ticker=TSLA")
    assert resp.status_code == 400, resp.status_code
    body = resp.get_json()
    assert "only supports these 12 tickers" in body["error"], body
    assert body["supported_tickers"] == list(SUPPORTED_TICKERS)


def test_page_offers_exactly_the_supported_tickers():
    import app as app_module
    page = app_module.app.test_client().get("/").data.decode()
    for t in SUPPORTED_TICKERS:
        assert f'<option value="{t}">' in page and f'data-ticker="{t}"' in page, t
    assert page.count("<option value=") == len(SUPPORTED_TICKERS)
    assert 'data-ticker="COST"' not in page


def test_app_never_imports_yfinance():
    import app as app_module  # noqa: F401 -- pulls in every rule1 module the app uses
    import rule1.cli  # noqa: F401
    assert "yfinance" not in sys.modules, "something in the app still imports yfinance"


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
