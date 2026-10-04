"""
End-to-end check of rule1.db: compute one company's Big Five growth rates,
ROIC, debt payback ratio, Sticker Price and Margin of Safety using only what is
in rule1.db, two ways, and check the results are sane and agree:

  1. the app's own path (rule1.db_data -> rule1.analysis), with yfinance
     replaced by a stub that raises if anything tries to scrape Yahoo;
  2. an independent recomputation straight from SQL.

Usage (from backend/):
    python scripts/validate_rule1_db.py            # MSFT
    python scripts/validate_rule1_db.py --ticker KO --db path/to/rule1.db

The sanity ranges (growth 0-40%, ROIC 10-60%, Sticker Price within 0.5x-2x
of the 10-year price range) are tuned for a steady compounder like MSFT; a
slower grower (KO, PG, HOG) legitimately falls outside them.

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _NoLiveData(types.ModuleType):
    def __getattr__(self, name):
        raise RuntimeError(f"yfinance.{name} was used -- the rule1.db path must not scrape Yahoo")


sys.modules["yfinance"] = _NoLiveData("yfinance")

from rule1.analysis import analyze_company  # noqa: E402
from rule1.db_data import fetch_company_data_from_db  # noqa: E402
from rule1.metrics import BUYBACK_DISTORTED_TICKERS, MARR, PROJECTION_YEARS  # noqa: E402

DEFAULT_DB = ROOT / "rule1.db"
FALLBACK_TAX_RATE = 0.21  # compute_roic's fallback; rule1.db has no pretax income


def pct(v) -> str:
    return "  n/a " if v is None else f"{v * 100:6.1f}%"


def cagr(start, end, years):
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def independent(conn: sqlite3.Connection, ticker: str) -> dict:
    """Recompute everything from SQL without going through rule1.*."""
    rows = conn.execute(
        "SELECT fyear, revt, eps_diluted, ajex, ceq, csho, oancf, capx, dltt, dlc, che, ebit "
        "FROM fundamentals_annual WHERE ticker = ? ORDER BY fyear DESC LIMIT 11", (ticker,)).fetchall()[::-1]
    by = {r[0]: r for r in rows}
    last, first = rows[-1][0], rows[0][0]
    series = {
        "sales": {y: r[1] for y, r in by.items()},
        "eps": {y: r[2] / r[3] for y, r in by.items()},
        "equity": {y: r[4] / (r[5] * r[3]) for y, r in by.items()},
        "fcf": {y: r[6] - r[7] for y, r in by.items()},
    }
    growth = {k: {w: cagr(s[last - w], s[last], w) for w in (10, 5, 1) if last - w >= first}
              for k, s in series.items()}
    # ROIC is averaged over the last 10 fiscal years (not compounded), as the book reports it
    roic = {y: r[11] * (1 - FALLBACK_TAX_RATE) / (r[4] + r[8]) for y, r in by.items() if y > last - 10}
    roic_net = {y: r[11] * (1 - FALLBACK_TAX_RATE) / (r[4] + r[8] + (r[9] or 0) - (r[10] or 0))
                for y, r in by.items() if y > last - 10}
    lr = by[last]
    debt_years = lr[8] / (lr[6] - lr[7])

    eps_ttm, fyq, fq, eps_known = conn.execute(
        "SELECT eps_ttm, fyearq, fqtr, known_from FROM fundamentals_quarterly WHERE ticker = ? AND eps_ttm IS NOT NULL "
        "ORDER BY fyearq DESC, fqtr DESC LIMIT 1", (ticker,)).fetchone()
    meanest, statpers = conn.execute(
        "SELECT meanest, statpers FROM analyst_growth WHERE ticker = ? AND meanest IS NOT NULL "
        "ORDER BY statpers DESC LIMIT 1", (ticker,)).fetchone()
    price_date, price = conn.execute(
        "SELECT date, adj_close FROM prices_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    # historical PE: mean of month-end adj closes in each calendar year / that fiscal year's split-adjusted EPS
    month_end = conn.execute(
        "SELECT substr(p.date, 1, 4), p.adj_close FROM prices_daily p JOIN "
        "(SELECT MAX(date) AS d FROM prices_daily WHERE ticker = ? GROUP BY substr(date, 1, 7)) m ON p.date = m.d "
        "WHERE p.ticker = ?", (ticker, ticker)).fetchall()
    closes = {}
    for y, c in month_end:
        closes.setdefault(int(y), []).append(c)
    pes = [sum(closes[y]) / len(closes[y]) / e for y, e in series["eps"].items() if e > 0 and y in closes]
    hist_pe = sum(pes) / len(pes)

    # buyback-distorted tickers use the analyst estimate alone; the rest take the lower of the two
    if ticker in BUYBACK_DISTORTED_TICKERS:
        raw_g = meanest / 100
    else:
        raw_g = min(growth["equity"][10] or 0.0, meanest / 100)
    g = min(max(raw_g, 0.0), 0.60)  # same 0-60% clamp as the app
    rule1_pe = max(min(g * 200, hist_pe), 5.0)
    sticker = (eps_ttm * (1 + g) ** PROJECTION_YEARS * rule1_pe / (1 + MARR) ** PROJECTION_YEARS
               if eps_ttm > 0 else None)
    return {
        "years": (first, last), "growth": growth, "series": series,
        "roic_10": sum(roic.values()) / len(roic), "roic_last": roic[last],
        "roic_net_10": sum(roic_net.values()) / len(roic_net),
        "debt_years": debt_years, "dltt": lr[8], "fcf": lr[6] - lr[7],
        "eps_ttm": eps_ttm, "eps_q": f"FY{fyq} Q{fq}", "eps_known": eps_known,
        "meanest": meanest, "statpers": statpers, "price": price, "price_date": price_date,
        "hist_pe": hist_pe, "growth_used": g, "rule1_pe": rule1_pe,
        "sticker": sticker, "mos": sticker / 2 if sticker else None,
        "price_range_10y": conn.execute(
            "SELECT MIN(adj_close), MAX(adj_close) FROM prices_daily WHERE ticker = ? AND date >= ?",
            (ticker, f"{last - 10}-01-01")).fetchone(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", default="MSFT")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args(argv)
    t = args.ticker.upper()

    app = analyze_company(fetch_company_data_from_db(t, db_path=args.db))
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        ind = independent(conn, t)
    finally:
        conn.close()

    first, last = ind["years"]
    print(f"{t} from {args.db.name} only (fiscal years {first}-{last}; yfinance stubbed out)\n")
    print("Big Five                     10yr     5yr     1yr   | independent 10yr")
    labels = {"roic": "ROIC (avg)", "sales": "Sales growth", "eps": "EPS growth",
              "equity": "Equity (BVPS) growth", "fcf": "Free cash flow growth"}
    for key, label in labels.items():
        w = app.big_five[key].windows
        ind10 = ind["roic_10"] if key == "roic" else ind["growth"][key].get(10)
        print(f"  {label:24s} {pct(w[10])}  {pct(w[5])}  {pct(w[1])}   | {pct(ind10)}")
    print(f"  ROIC, invested capital net of cash (ceq + dltt + dlc - che), 10yr avg: {pct(ind['roic_net_10'])}")
    print(f"\nDebt payback: {app.debt_years:.2f} years  "
          f"(dltt ${ind['dltt']:,.0f}M / FCF ${ind['fcf']:,.0f}M, FY{last})")
    s = app.sticker
    print(f"\nSticker Price inputs:")
    if s.sticker_price is None:
        print(f"  TTM EPS ${s.current_eps:.2f} ({ind['eps_q']}): {s.verdict}")
        print("\nNo Sticker Price to check; stopping here.")
        return 1
    print(f"  TTM EPS           ${s.current_eps:.2f}  ({ind['eps_q']}, known from {ind['eps_known']})")
    print(f"  growth used       {pct(s.growth_rate)}  ({s.growth_rate_source}; analyst mean "
          f"{ind['meanest']:.1f}% as of {ind['statpers']})")
    print(f"  PE used           {s.rule1_pe:.1f}  (default {s.default_pe:.1f}, historical avg {s.historical_pe:.1f})")
    print(f"  future EPS / price ${s.future_eps:.2f} / ${s.future_price:,.2f}")
    print(f"  Sticker Price     ${s.sticker_price:,.2f}   (independent: ${ind['sticker']:,.2f})")
    print(f"  Margin of Safety  ${s.mos_price:,.2f}   (independent: ${ind['mos']:,.2f})")
    print(f"  current price     ${s.current_price:,.2f} on {ind['price_date']}  -> {s.verdict}")

    failures = []

    def check(ok, label):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    print("\nChecks:")
    for key in ("sales", "eps", "equity", "fcf"):
        g = app.big_five[key].windows[10]
        check(g is not None and 0 < g < 0.40, f"{labels[key]} 10yr is positive and under 40% ({pct(g)})")
        check(g is not None and math.isclose(g, ind["growth"][key][10], rel_tol=1e-9),
              f"{labels[key]} 10yr matches the independent SQL recompute")
    roic = app.big_five["roic"].windows[10]
    check(roic is not None and 0.10 <= roic < 0.60, f"ROIC 10yr avg between 10% and 60% ({pct(roic)})")
    check(roic is not None and math.isclose(roic, ind["roic_10"], rel_tol=1e-9), "ROIC matches the independent recompute")
    d = app.debt_years
    check(d is not None and 0 <= d < 10 and math.isclose(d, ind["debt_years"], rel_tol=1e-9),
          f"debt payback is a finite number of years under 10 and matches ({d:.2f})")
    lo, hi = ind["price_range_10y"]
    check(s.sticker_price is not None and lo * 0.5 <= s.sticker_price <= hi * 2,
          f"Sticker Price ${s.sticker_price:,.2f} within 0.5x-2x of {t}'s 10-year price range "
          f"(${lo:,.2f}-${hi:,.2f})")
    check(math.isclose(s.sticker_price, ind["sticker"], rel_tol=1e-9), "Sticker Price matches the independent recompute")
    check(math.isclose(s.mos_price, s.sticker_price / 2), "Margin of Safety is half the Sticker Price")
    check(s.current_eps == ind["eps_ttm"] and s.current_eps > 0, "TTM EPS comes from fundamentals_quarterly.eps_ttm")

    print("\nCaveats found in the data (not failures):")
    if ind["price_date"] < ind["eps_known"]:
        print(f"  - latest price ({ind['price_date']}) is older than the latest TTM EPS "
              f"(known from {ind['eps_known']}): the dump has no prices after {ind['price_date']}, "
              f"so 'current price' and the verdict are stale relative to the fundamentals.")
    for w in app.company.warnings:
        print(f"  - app warning: {w}")

    print(f"\n{'All checks passed.' if not failures else f'{len(failures)} check(s) FAILED.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
