"""
Tests for rule1/backtest.py, run against a throwaway copy of the MSFT
fixture from scripts/seed_backtest_fixture.py (never the real rule1.db).

Run with: python tests/test_backtest.py
"""

import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_backtest_fixture as fixture
from rule1.backtest import buy_window_starts, gap_stats, hit_rate, load_backtest

TODAY = date(2026, 9, 28)


def _seeded_db(tmp: str) -> Path:
    db = Path(tmp) / "rule1.db"
    assert fixture.main(["--db", str(db)]) == 0
    return db


def test_consecutive_flagged_signals_are_one_window():
    d = date.fromisoformat
    signals = [
        (d("2020-01-01"), True), (d("2020-07-01"), True),   # one window
        (d("2021-01-01"), False),
        (d("2021-07-01"), True),                             # second window
    ]
    assert buy_window_starts(signals) == [d("2020-01-01"), d("2021-07-01")]


def test_gap_stats_reports_min_median_max():
    d = date.fromisoformat
    g = gap_stats([d("2020-01-01"), d("2020-01-11"), d("2020-02-10"), d("2020-02-15")])
    assert g["gaps_days"] == [10, 30, 5]
    assert (g["min_days"], g["median_days"], g["max_days"]) == (5, 10, 30)
    assert gap_stats([d("2020-01-01")]) is None


def test_hit_rate_excludes_nulls():
    r = hit_rate([True, False, None, True])
    assert (r["hits"], r["n"], r["missing"]) == (2, 3, 1)
    assert abs(r["rate"] - 2 / 3) < 1e-9
    assert hit_rate([None])["rate"] is None


def test_fixture_buy_windows():
    with tempfile.TemporaryDirectory() as tmp:
        bt = load_backtest("msft", _seeded_db(tmp))
    bw = bt["buy_windows"]
    assert bt["available"]
    assert bw["starts"] == ["2011-06-30", "2015-06-30", "2018-12-31", "2021-12-31"], bw["starts"]
    assert bw["last_start"] == "2021-12-31"
    assert bw["still_open"] is False
    assert bw["gaps"]["gaps_days"] == [1461, 1280, 1096]
    assert (bw["gaps"]["min_days"], bw["gaps"]["median_days"], bw["gaps"]["max_days"]) == (1096, 1280, 1461)


def test_fixture_hit_rates_are_three_separate_numbers():
    with tempfile.TemporaryDirectory() as tmp:
        bt = load_backtest("MSFT", _seeded_db(tmp))
    assert bt["horizons"] == [1, 3, 5]
    one_year = bt["by_horizon"]["1"]["hit_rates"]
    assert (one_year["moat_held_up"]["hits"], one_year["moat_held_up"]["n"]) == (3, 4)
    assert (one_year["price_target_hit"]["hits"], one_year["price_target_hit"]["n"]) == (0, 4)
    assert (one_year["is_profitable"]["hits"], one_year["is_profitable"]["n"]) == (3, 4)
    latest = bt["by_horizon"]["1"]["latest"]
    assert latest["signal_date"] == "2021-12-31"
    assert latest["is_profitable"] is False and latest["max_drawdown"] == -0.376
    assert latest["price_at_signal"] == 336.32  # joined from backtest_signals


def test_missing_db_or_tables_is_reported_not_raised():
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope.db"
        bt = load_backtest("MSFT", missing)
        assert bt["available"] is False and "doesn't exist" in bt["reason"]
        assert not missing.exists(), "a read must not create the database file"

        empty = Path(tmp) / "empty.db"
        sqlite3.connect(empty).close()
        bt = load_backtest("MSFT", empty)
        assert bt["available"] is False and "backtest_signals" in bt["reason"]


def test_unknown_ticker_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        bt = load_backtest("ZZZZ", _seeded_db(tmp))
    assert bt["available"] is False and "ZZZZ" in bt["reason"]


def test_r_style_dates_and_logicals_are_understood():
    """RSQLite writes R Dates as days since 1970-01-01 and may leave logicals
    as 'TRUE'/'FALSE' text -- both should read the same as ISO/0-1."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _seeded_db(tmp)
        conn = sqlite3.connect(db)
        with conn:
            conn.execute("UPDATE backtest_signals SET as_of_date = julianday(as_of_date) - 2440587.5")
            conn.execute("UPDATE backtest_signals SET is_buy_window = CASE is_buy_window WHEN 1 THEN 'TRUE' ELSE 'FALSE' END")
            conn.execute("UPDATE backtest_outcomes SET signal_date = julianday(signal_date) - 2440587.5, "
                         "target_date = julianday(target_date) - 2440587.5")
        conn.close()
        bt = load_backtest("MSFT", db)
    assert bt["buy_windows"]["starts"] == ["2011-06-30", "2015-06-30", "2018-12-31", "2021-12-31"]
    assert bt["by_horizon"]["1"]["latest"]["signal_date"] == "2021-12-31"
    assert bt["by_horizon"]["1"]["latest"]["price_at_signal"] == 336.32


def _seed_signals_only(db: Path, ticker: str, flags: "list[int]"):
    """Add `ticker` signal rows (semiannual from 2015) with the given
    is_buy_window flags and no outcome rows."""
    conn = sqlite3.connect(db)
    with conn:
        conn.executemany(
            "INSERT INTO backtest_signals VALUES (?,?,?,?,?,?,?,?,?)",
            [(ticker, f"{2015 + i // 2}-{'06-30' if i % 2 == 0 else '12-31'}", "{}", 3,
              200.0, 100.0, 150.0, "{}", flag) for i, flag in enumerate(flags)],
        )
    conn.close()


def test_fixture_next_window_marker_is_last_start_plus_median_gap():
    with tempfile.TemporaryDirectory() as tmp:
        bt = load_backtest("MSFT", _seeded_db(tmp), today=TODAY)
    m = bt["buy_windows"]["next_window_marker"]
    # 2021-12-31 + 1280 (median) / 1096 (min) / 1461 (max) days
    assert (m["date"], m["earliest"], m["latest"]) == ("2025-07-03", "2024-12-31", "2025-12-31"), m
    assert m["already_passed"] is True


def test_ticker_with_zero_buy_windows_has_empty_state_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        db = _seeded_db(tmp)
        _seed_signals_only(db, "COST", [0] * 8)
        bt = load_backtest("COST", db, today=TODAY)

        # The same payload through the Flask route the dashboard calls.
        import app as app_module
        original = app_module.BACKTEST_DB_PATH
        app_module.BACKTEST_DB_PATH = db
        try:
            client = app_module.app.test_client()
            resp = client.get("/api/backtest/cost")
            page = client.get("/")
        finally:
            app_module.BACKTEST_DB_PATH = original

    bw = bt["buy_windows"]
    assert bt["available"] is True
    assert (bw["signal_rows"], bw["flagged_rows"]) == (8, 0)
    assert bw["starts"] == [] and bw["last_start"] is None
    assert bw["gaps"] is None, "no gap distribution without buy windows"
    assert bw["next_window_marker"] is None, "no projected date without buy windows"
    assert bw["still_open"] is False
    assert bt["horizons"] == [] and bt["default_horizon"] is None and bt["by_horizon"] == {}

    assert resp.status_code == 200
    assert resp.get_json() == bt
    assert page.status_code == 200 and b'id="bt-next-date"' in page.data


def test_single_buy_window_has_start_but_no_gap_or_marker():
    with tempfile.TemporaryDirectory() as tmp:
        db = _seeded_db(tmp)
        _seed_signals_only(db, "COST", [0, 0, 1, 1, 0, 0])
        bt = load_backtest("COST", db, today=TODAY)
    bw = bt["buy_windows"]
    assert bw["starts"] == ["2016-06-30"]
    assert bw["gaps"] is None and bw["next_window_marker"] is None


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
