"""
Command-line entry point.

Usage:
    python -m rule1.cli AAPL
    python -m rule1.cli AAPL MSFT KO --out-dir results
    python -m rule1.cli JNJ --no-charts

Only the tickers in the local rule1.db are supported (see
rule1.db_data.SUPPORTED_TICKERS); nothing is fetched over the network.

Each ticker gets:
  - a colored terminal report,
  - a growth-rate chart and a debt-payback gauge (PNG, in --out-dir),
  - the matching castle/moat graphic (PNG, in --out-dir).

Pass more than one ticker to also get a side-by-side comparison table and
chart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import analyze
from .db_data import SUPPORTED_TICKERS
from .report import (
    print_report, print_comparison_table, save_growth_chart,
    save_debt_gauge, save_comparison_chart, copy_castle_image, c,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rule1",
        description="Compute Phil Town's Rule #1 numbers (Big Five, ROIC, debt, "
                     "Sticker Price, MOS) from the local rule1.db.",
    )
    p.add_argument("tickers", nargs="+", help="Stock ticker(s). Supported: " + ", ".join(SUPPORTED_TICKERS))
    p.add_argument("--years", type=int, default=10, help="Years of history to read (default: 10; "
                    "actual span depends on what rule1.db holds)")
    p.add_argument("--out-dir", default="rule1_output", help="Folder to save charts/images into")
    p.add_argument("--no-charts", action="store_true", help="Skip generating chart images (faster, terminal-only)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)

    results = []
    for raw in args.tickers:
        ticker = raw.strip().upper()
        print(c(f"Loading {ticker} ...", "gray"))
        try:
            result = analyze(ticker, years=args.years)
        except Exception as e:
            print(c(f"Failed to analyze {ticker}: {e}", "red"), file=sys.stderr)
            continue
        results.append(result)
        print_report(result)
        if not args.no_charts:
            gpath = save_growth_chart(result, out_dir)
            dpath = save_debt_gauge(result, out_dir)
            cpath = copy_castle_image(result, out_dir)
            print(c(f"Saved: {gpath}", "gray"))
            print(c(f"Saved: {dpath}", "gray"))
            print(c(f"Saved: {cpath}", "gray"))
            print()

    if len(results) > 1:
        print_comparison_table(results)
        if not args.no_charts:
            cmp_path = save_comparison_chart(results, out_dir)
            print(c(f"Saved: {cmp_path}", "gray"))

    if not results:
        print(c("No tickers could be analyzed.", "red"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
