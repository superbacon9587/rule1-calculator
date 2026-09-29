"""
Seed rule1.db with a small, realistic-shaped MSFT backtest fixture.

The real `backtest_signals` / `backtest_outcomes` tables come from a
teammate's R pipeline that isn't finished yet. This script creates both
tables (only if they don't already exist -- the R pipeline owns the real
schema) and inserts one ticker's worth of rows so the dashboard's Backtest
card has something to render against.

Run with: python scripts/seed_backtest_fixture.py [--db PATH] [--replace]

What's in the fixture:
  - 28 semiannual MSFT signals (2011-06-30 .. 2024-12-31), with prices that
    approximate real closes. A row is a buy window when price <= MOS price.
  - 4 buy windows, two of them spanning several consecutive signals:
      2011-06-30 (+2011-12-30), 2015-06-30, 2018-12-31,
      2021-12-31 (+2022-06-30, +2022-12-30)
  - Outcomes at 1/3/5-year horizons for each window's first signal, wherever
    the target date is inside the fixture's date range (11 rows).

Fixture conventions (confirm against the R pipeline once it lands):
  - dates are ISO 'YYYY-MM-DD' TEXT; booleans are INTEGER 0/1
  - returns, drawdown, and volatility are fractions (0.12 = 12%)
  - realized_return / benchmark_return are cumulative, price-only, over the
    horizon; benchmark is SPY; benchmark_delta = realized - benchmark
  - max_drawdown is negative (peak-to-trough during the holding period)
  - volatility is annualized stdev of daily returns
  - price_target_hit: realized_price >= the sticker price at signal time
  - moat_held_up: moat_level at target date >= moat_level at signal date
  - projected_return: (1 + equity growth at signal) ** horizon - 1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "rule1.db"
TICKER = "MSFT"

DDL = """
CREATE TABLE IF NOT EXISTS backtest_signals (
    ticker            TEXT    NOT NULL,
    as_of_date        TEXT    NOT NULL,
    big_five_json     TEXT,
    moat_level        INTEGER,
    sticker_price     REAL,
    mos_price         REAL,
    price_at_signal   REAL,
    tools_status_json TEXT,
    is_buy_window     INTEGER,
    PRIMARY KEY (ticker, as_of_date)
);
CREATE TABLE IF NOT EXISTS backtest_outcomes (
    ticker            TEXT    NOT NULL,
    signal_date       TEXT    NOT NULL,
    horizon_years     INTEGER NOT NULL,
    target_date       TEXT,
    realized_price    REAL,
    realized_return   REAL,
    projected_return  REAL,
    moat_held_up      INTEGER,
    price_target_hit  INTEGER,
    is_profitable     INTEGER,
    max_drawdown      REAL,
    volatility        REAL,
    benchmark_return  REAL,
    benchmark_delta   REAL,
    PRIMARY KEY (ticker, signal_date, horizon_years)
);
"""

# (as_of_date, MSFT close, SPY close, sticker price, moat level)
SIGNALS = [
    ("2011-06-30",  26.00, 132.04,  54.80, 5),
    ("2011-12-30",  25.96, 125.50,  53.10, 5),
    ("2012-06-29",  30.59, 136.10,  55.00, 5),
    ("2012-12-31",  26.71, 142.41,  50.40, 5),
    ("2013-06-28",  34.55, 160.42,  56.20, 4),
    ("2013-12-31",  37.41, 184.69,  60.80, 4),
    ("2014-06-30",  41.70, 195.72,  66.00, 5),
    ("2014-12-31",  46.45, 205.54,  71.40, 4),
    ("2015-06-30",  44.15, 204.45,  90.60, 3),
    ("2015-12-31",  55.48, 203.87,  96.00, 3),
    ("2016-06-30",  51.17, 209.48,  92.40, 3),
    ("2016-12-30",  62.14, 223.53, 104.00, 3),
    ("2017-06-30",  68.93, 241.80, 118.00, 4),
    ("2017-12-29",  85.54, 266.86, 142.00, 4),
    ("2018-06-29",  98.61, 271.28, 176.00, 5),
    ("2018-12-31", 101.57, 249.92, 210.00, 5),
    ("2019-06-28", 133.96, 293.00, 232.00, 5),
    ("2019-12-31", 157.70, 321.86, 262.00, 5),
    ("2020-06-30", 203.51, 308.36, 318.00, 5),
    ("2020-12-31", 222.42, 373.88, 380.00, 5),
    ("2021-06-30", 270.90, 428.06, 470.00, 5),
    ("2021-12-31", 336.32, 474.96, 690.00, 5),
    ("2022-06-30", 256.83, 377.25, 640.00, 5),
    ("2022-12-30", 239.82, 382.43, 560.00, 4),
    ("2023-06-30", 340.54, 443.28, 600.00, 4),
    ("2023-12-29", 376.04, 475.31, 640.00, 4),
    ("2024-06-28", 446.95, 544.22, 700.00, 5),
    ("2024-12-31", 421.50, 586.08, 720.00, 5),
]

# Path-dependent stats that can't be derived from two endpoint prices:
# (window start, horizon years) -> (max_drawdown, annualized volatility)
PATH_STATS = {
    ("2011-06-30", 1): (-0.121, 0.214),
    ("2011-06-30", 3): (-0.196, 0.203),
    ("2011-06-30", 5): (-0.196, 0.214),
    ("2015-06-30", 1): (-0.183, 0.262),
    ("2015-06-30", 3): (-0.183, 0.221),
    ("2015-06-30", 5): (-0.280, 0.268),
    ("2018-12-31", 1): (-0.068, 0.201),
    ("2018-12-31", 3): (-0.280, 0.284),
    ("2018-12-31", 5): (-0.376, 0.287),
    ("2021-12-31", 1): (-0.376, 0.352),
    ("2021-12-31", 3): (-0.376, 0.286),
}

HORIZONS = (1, 3, 5)
ROWS_PER_YEAR = 2  # semiannual signals


def big_five_for(level: int, year: int) -> dict:
    """Big Five growth rates whose green count (>= 10%) equals `level`."""
    wobble = (year % 3) * 0.01
    b5 = {
        "roic": 0.27 + wobble,
        "sales": 0.11 + wobble,
        "eps": 0.13 + wobble,
        "equity": 0.12 + wobble,
        "fcf": 0.14 + wobble,
    }
    # Knock metrics below the 10% bar, weakest first, until the count matches.
    for key, weak in (("fcf", 0.07), ("eps", 0.04), ("sales", 0.06), ("equity", 0.08)):
        if sum(v >= 0.10 for v in b5.values()) <= level:
            break
        b5[key] = weak
    return {k: round(v, 4) for k, v in b5.items()}


def build_rows():
    signals, outcomes = [], []
    buy_flags = []
    for date, price, _spy, sticker, level in SIGNALS:
        mos = round(sticker / 2, 2)
        is_buy = price <= mos
        buy_flags.append(is_buy)
        tools = ({"ma": "buy", "macd": "buy", "stochastic": "buy"} if is_buy
                 else {"ma": "sell", "macd": "buy" if level >= 4 else "sell", "stochastic": "sell"})
        signals.append((
            TICKER, date, json.dumps(big_five_for(level, int(date[:4]))), level,
            sticker, mos, price, json.dumps(tools), int(is_buy),
        ))

    for i, is_buy in enumerate(buy_flags):
        if not is_buy or (i > 0 and buy_flags[i - 1]):
            continue  # outcomes are recorded for each window's first signal only
        date, price, spy, sticker, level = SIGNALS[i]
        equity_growth = big_five_for(level, int(date[:4]))["equity"]
        for h in HORIZONS:
            j = i + h * ROWS_PER_YEAR
            if j >= len(SIGNALS):
                continue  # target date is past the end of the fixture
            t_date, t_price, t_spy, _t_sticker, t_level = SIGNALS[j]
            realized = t_price / price - 1
            bench = t_spy / spy - 1
            max_dd, vol = PATH_STATS[(date, h)]
            outcomes.append((
                TICKER, date, h, t_date, t_price,
                round(realized, 4),
                round((1 + equity_growth) ** h - 1, 4),
                int(t_level >= level),
                int(t_price >= sticker),
                int(realized > 0),
                max_dd, vol,
                round(bench, 4),
                round(realized - bench, 4),
            ))
    return signals, outcomes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file (default: {DEFAULT_DB.name})")
    parser.add_argument("--replace", action="store_true",
                        help=f"delete existing {TICKER} backtest rows first (otherwise refuse to touch them)")
    args = parser.parse_args(argv)

    signals, outcomes = build_rows()

    conn = sqlite3.connect(args.db)
    try:
        conn.executescript(DDL)
        existing = sum(
            conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker = ?", (TICKER,)).fetchone()[0]
            for table in ("backtest_signals", "backtest_outcomes")
        )
        if existing and not args.replace:
            print(f"{args.db} already has {existing} {TICKER} backtest rows -- leaving them alone. "
                  f"Re-run with --replace to overwrite them with the fixture.", file=sys.stderr)
            return 1
        with conn:
            conn.execute("DELETE FROM backtest_signals WHERE ticker = ?", (TICKER,))
            conn.execute("DELETE FROM backtest_outcomes WHERE ticker = ?", (TICKER,))
            conn.executemany("INSERT INTO backtest_signals VALUES (?,?,?,?,?,?,?,?,?)", signals)
            conn.executemany("INSERT INTO backtest_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", outcomes)
    finally:
        conn.close()

    n_buy = sum(row[-1] for row in signals)
    print(f"Seeded {args.db}: {len(signals)} {TICKER} signals ({n_buy} flagged is_buy_window), "
          f"{len(outcomes)} outcome rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
