"""
Tests for rule1/db_data.py, run against a tiny throwaway rule1.db built
here (never the real one), checking the yfinance -> rule1.db field mapping.

Run with: python tests/test_db_data.py
"""

import math
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rule1.db_data import db_covers, fetch_company_data_from_db
from rule1.analysis import analyze_company

# FY2012..FY2024 (13 year ends), a 2:1 split between FY2019 and FY2020 (ajex 2 -> 1).
YEARS = list(range(2012, 2025))


def _build_db(tmp: str) -> Path:
    db = Path(tmp) / "rule1.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE companies (ticker, permno, gvkey, cusip, name, currency)")
    conn.execute("INSERT INTO companies VALUES ('TST', '1', '000001', '00000000', 'TEST CO', 'USD')")
    conn.execute("CREATE TABLE fundamentals_annual (ticker, datadate, revt, epsfx, ni, cshfd, seq, csho, ajex, "
                 "oancf, capx, dltt, pi, xint, txt)")
    for i, y in enumerate(YEARS):
        ajex = 2.0 if y < 2020 else 1.0
        conn.execute("INSERT INTO fundamentals_annual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "TST", f"{y}-06-30",
            100.0 * 1.1 ** i,            # revt ($M)
            2.0 * 1.1 ** i * ajex,       # epsfx, as reported (pre-split years are 2x)
            None, None,
            500.0 * 1.1 ** i,            # seq ($M)
            100.0 / ajex,                # csho (M), as reported
            ajex,
            80.0, 30.0,                  # oancf, capx (capx positive, Compustat style)
            200.0,                       # dltt
            120.0, None if y == 2024 else 10.0, 26.0,  # pi, xint (blank in FY2024), txt
        ))
    conn.execute("CREATE TABLE fundamentals_quarterly (ticker, datadate, epsf12, ajexq)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', '2024-03-31', 5.0, 1.0)")
    conn.execute("INSERT INTO fundamentals_quarterly VALUES ('TST', '2024-06-30', 6.0, 1.0)")
    conn.execute("CREATE TABLE analyst_growth (ticker, statpers, meanest, medest)")
    conn.execute("INSERT INTO analyst_growth VALUES ('TST', '2024-05-16', 9.0, 9.5)")
    conn.execute("INSERT INTO analyst_growth VALUES ('TST', '2024-06-20', 12.0, 12.5)")
    conn.execute("CREATE TABLE prices_daily (ticker, date, close, cfacpr)")
    for y in YEARS:
        cfacpr = 2.0 if y < 2020 else 1.0
        for m in range(1, 13):
            conn.execute("INSERT INTO prices_daily VALUES ('TST', ?, ?, ?)", (f"{y}-{m:02d}-15", 999.0, cfacpr))
            conn.execute("INSERT INTO prices_daily VALUES ('TST', ?, ?, ?)", (f"{y}-{m:02d}-28", 40.0 * cfacpr, cfacpr))
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


def test_window_is_ten_years_of_year_ends():
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


def test_roic_uses_pretax_plus_interest_as_ebit():
    c = _load()
    equity = 500.0 * 1.1 ** (2023 - 2012)
    expected = (120.0 + 10.0) * (1 - 26.0 / 120.0) / (equity + 200.0)
    assert math.isclose(c.roic_by_year[2023], expected, rel_tol=1e-9)
    # FY2024 xint is blank -> EBIT = pretax alone, with a warning
    equity = 500.0 * 1.1 ** (2024 - 2012)
    assert math.isclose(c.roic_by_year[2024], 120.0 * (1 - 26.0 / 120.0) / (equity + 200.0), rel_tol=1e-9)
    assert any("xint" in w for w in c.warnings)


def test_pe_uses_split_adjusted_month_end_closes():
    c = _load()
    # month-end close is 40 on today's basis every year; mid-month 999 is ignored
    assert math.isclose(c.pe_by_year[2024], 40.0 / c.eps_by_year[2024], rel_tol=1e-9)
    assert math.isclose(c.pe_by_year[2015], 40.0 / c.eps_by_year[2015], rel_tol=1e-9)


def test_current_values_use_latest_rows():
    c = _load()
    assert c.current_price == 40.0
    assert c.current_eps == 6.0
    assert math.isclose(c.analyst_growth_estimate, 0.12)
    assert c.data_source == "rule1.db"


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
