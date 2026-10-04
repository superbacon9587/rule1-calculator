"""
Read-only summaries of the backtest tables in rule1.db.

`backtest_signals` and `backtest_outcomes` are produced by a separate R
pipeline (see scripts/seed_backtest_fixture.py for a stand-in fixture and the
column conventions assumed here). This module only reads them and turns one
ticker's rows into the numbers the dashboard's Backtest card shows, each with
a plain-English note naming the table and columns it came from.

Nothing here writes to the database, and a missing file or table is reported
back as "not available" rather than raised.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "rule1.db"

HIT_RATE_COLUMNS = {
    "moat_held_up": "Moat held up",
    "price_target_hit": "Price target hit",
    "is_profitable": "Win rate (profitable)",
}
PERFORMANCE_COLUMNS = ("max_drawdown", "volatility", "benchmark_return", "benchmark_delta")
STICKER_HORIZON_YEARS = 10

_R_EPOCH = date(1970, 1, 1)


def _to_date(value) -> Optional[date]:
    """ISO text, or a number of days since 1970-01-01 (how RSQLite writes an
    R Date column unless extended types are switched on)."""
    if value is None:
        return None
    try:
        # Numeric, or numeric text (a REAL stored into a TEXT-affinity column).
        return _R_EPOCH + timedelta(days=int(float(value)))
    except ValueError:
        return date.fromisoformat(str(value)[:10])


def _to_bool(value) -> Optional[bool]:
    """INTEGER 0/1 (SQLite / RSQLite logicals), or TRUE/FALSE-style text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes"):
        return True
    if text in ("0", "false", "f", "no"):
        return False
    return None


def _unavailable(ticker: str, reason: str) -> dict:
    return {"ticker": ticker, "available": False, "reason": reason}


def buy_window_starts(signals: "list[tuple[date, bool]]") -> "list[date]":
    """
    The first signal of each buy window, from (as_of_date, is_buy_window)
    pairs sorted by date. Consecutive flagged signals are one window, so a
    window starts at a flagged row whose preceding row isn't flagged.
    """
    starts = []
    prev_buy = False
    for as_of, is_buy in signals:
        if is_buy and not prev_buy:
            starts.append(as_of)
        prev_buy = bool(is_buy)
    return starts


def gap_stats(starts: "list[date]") -> Optional[dict]:
    gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
    if not gaps:
        return None
    return {
        "n": len(gaps),
        "gaps_days": gaps,
        "min_days": min(gaps),
        "median_days": statistics.median(gaps),
        "max_days": max(gaps),
    }


def next_window_marker(starts: "list[date]", gaps: Optional[dict], today: date) -> Optional[dict]:
    """
    Illustrative only: the last window start plus the median historical gap,
    alongside the dates the min and max gaps would give. None when there are
    fewer than two window starts (no gap to measure).
    """
    if not starts or not gaps:
        return None
    last = starts[-1]
    marker = last + timedelta(days=round(gaps["median_days"]))
    return {
        "date": marker.isoformat(),
        "earliest": (last + timedelta(days=gaps["min_days"])).isoformat(),
        "latest": (last + timedelta(days=gaps["max_days"])).isoformat(),
        "already_passed": marker < today,
        "today": today.isoformat(),
    }


def hit_rate(values: "list[Optional[bool]]") -> dict:
    """Fraction True among rows where the flag is known (NULLs excluded)."""
    known = [v for v in values if v is not None]
    hits = sum(known)
    return {
        "hits": hits,
        "n": len(known),
        "missing": len(values) - len(known),
        "rate": hits / len(known) if known else None,
    }


def load_backtest(ticker: str, db_path: Path = DEFAULT_DB_PATH, today: Optional[date] = None) -> dict:
    ticker = ticker.strip().upper()
    today = today or date.today()
    db_path = Path(db_path)
    if not db_path.exists():
        return _unavailable(ticker, f"{db_path.name} doesn't exist yet -- the backtest pipeline hasn't written it.")

    # mode=ro so a read never creates or modifies the file.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in ("backtest_signals", "backtest_outcomes") if t not in tables]
        if missing:
            return _unavailable(ticker, f"{db_path.name} has no {' or '.join(missing)} table yet.")

        signal_rows = conn.execute(
            "SELECT as_of_date, is_buy_window, price_at_signal, sticker_price, mos_price, moat_level "
            "FROM backtest_signals WHERE ticker = ?", (ticker,)
        ).fetchall()
        outcome_rows = conn.execute(
            "SELECT signal_date, horizon_years, target_date, realized_price, realized_return, "
            "projected_return, moat_held_up, price_target_hit, is_profitable, max_drawdown, "
            "volatility, benchmark_return, benchmark_delta "
            "FROM backtest_outcomes WHERE ticker = ?", (ticker,)
        ).fetchall()
    finally:
        conn.close()

    if not signal_rows and not outcome_rows:
        return _unavailable(ticker, f"No rows for {ticker} in backtest_signals or backtest_outcomes.")

    # Sorted in Python, not SQL, so ISO-text and numeric (R epoch) dates
    # both order correctly.
    signals = sorted(
        ({"as_of_date": _to_date(r[0]), "is_buy_window": _to_bool(r[1]) is True,
          "price_at_signal": r[2], "sticker_price": r[3], "mos_price": r[4], "moat_level": r[5]}
         for r in signal_rows),
        key=lambda s: s["as_of_date"],
    )
    signals_by_date = {s["as_of_date"]: s for s in signals}

    # ---- buy windows ------------------------------------------------------
    starts = buy_window_starts([(s["as_of_date"], s["is_buy_window"]) for s in signals])
    n_flagged = sum(s["is_buy_window"] for s in signals)
    latest_signal = signals[-1] if signals else None
    gaps = gap_stats(starts)
    buy_windows = {
        "signal_rows": len(signals),
        "flagged_rows": n_flagged,
        "starts": [d.isoformat() for d in starts],
        "last_start": starts[-1].isoformat() if starts else None,
        "latest_signal_date": latest_signal["as_of_date"].isoformat() if latest_signal else None,
        "still_open": bool(latest_signal and latest_signal["is_buy_window"]),
        "gaps": gaps,
        "next_window_marker": next_window_marker(starts, gaps, today),
        "provenance": {
            "last_start": (
                f"backtest_signals.as_of_date for {ticker} rows with is_buy_window = 1 "
                f"({n_flagged} of {len(signals)} rows); a window starts at a flagged row whose "
                f"previous as_of_date isn't flagged."
            ),
            "gaps": (
                f"Days between consecutive buy-window starts above, from backtest_signals.as_of_date "
                f"where is_buy_window = 1 for {ticker}."
            ),
            "next_window_marker": (
                f"Last buy-window start plus the median / min / max gap above, all from "
                f"backtest_signals.as_of_date where is_buy_window = 1 for {ticker}. "
                f"Nothing else goes into it."
            ),
        },
    }

    # ---- outcomes, grouped by horizon ------------------------------------
    outcomes = []
    for r in outcome_rows:
        signal_date = _to_date(r[0])
        sig = signals_by_date.get(signal_date, {})
        outcomes.append({
            "signal_date": signal_date.isoformat(),
            "horizon_years": r[1],
            "target_date": _to_date(r[2]).isoformat() if r[2] is not None else None,
            "price_at_signal": sig.get("price_at_signal"),
            "sticker_price": sig.get("sticker_price"),
            "realized_price": r[3],
            "realized_return": r[4],
            "projected_return": r[5],
            "moat_held_up": _to_bool(r[6]),
            "price_target_hit": _to_bool(r[7]),
            "is_profitable": _to_bool(r[8]),
            "max_drawdown": r[9],
            "volatility": r[10],
            "benchmark_return": r[11],
            "benchmark_delta": r[12],
        })
    outcomes.sort(key=lambda o: (o["horizon_years"], o["signal_date"]), reverse=True)

    horizons = sorted({o["horizon_years"] for o in outcomes})
    by_horizon = {}
    for h in horizons:
        rows = [o for o in outcomes if o["horizon_years"] == h]
        latest = rows[0]  # sorted newest signal first
        by_horizon[str(h)] = {
            "n_rows": len(rows),
            "hit_rates": {col: hit_rate([o[col] for o in rows]) for col in HIT_RATE_COLUMNS},
            "latest": latest,
            "rows": rows,
            "provenance": {
                "hit_rates": {
                    col: (f"backtest_outcomes.{col} across {len(rows)} {ticker} rows with "
                          f"horizon_years = {h} (NULLs excluded).")
                    for col in HIT_RATE_COLUMNS
                },
                "latest": (
                    f"backtest_outcomes.{', '.join(PERFORMANCE_COLUMNS)} for {ticker}, "
                    f"signal_date = {latest['signal_date']}, horizon_years = {h} "
                    f"(the most recent signal_date with a {h}-year outcome)."
                ),
                "rows": (
                    f"All {len(rows)} backtest_outcomes rows for {ticker} with horizon_years = {h}; "
                    f"price at signal joined from backtest_signals.price_at_signal on "
                    f"signal_date = as_of_date."
                ),
            },
        }

    # Default to the 10-year horizon the Sticker Price is built around; without
    # one, the horizon with the most completed outcomes (ties -> longer).
    if STICKER_HORIZON_YEARS in horizons:
        default_horizon = STICKER_HORIZON_YEARS
    else:
        default_horizon = max(horizons, key=lambda h: (by_horizon[str(h)]["n_rows"], h)) if horizons else None

    return {
        "ticker": ticker,
        "available": True,
        "db_file": db_path.name,
        "buy_windows": buy_windows,
        "horizons": horizons,
        "default_horizon": default_horizon,
        "by_horizon": by_horizon,
    }
