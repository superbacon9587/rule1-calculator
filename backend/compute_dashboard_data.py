"""
Feeds raw_scraped_data.json through the validated rule1.metrics engine and
writes dashboard_data.json: one fully-computed record per ticker, ready to
seed into the Artifact's db.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rule1.metrics import (
    growth_rate_windows, average_windows, debt_payback_years,
    compute_sticker_price, is_green, debt_color, assess_moat,
)

WINDOWS = (10, 5, 3, 1)


def to_int_keys(d):
    return {int(k): v for k, v in d.items() if v is not None}


def metric_block(series, use_average=False):
    windows = (average_windows if use_average else growth_rate_windows)(series, WINDOWS)
    longest = next((w for w in WINDOWS if windows.get(w) is not None), None)
    value = windows.get(longest) if longest else None
    return {
        "windows": {str(w): windows.get(w) for w in WINDOWS},
        "longest_window": longest,
        "longest_value": value,
        "green": is_green(value),
    }


def main():
    raw = json.loads(Path("raw_scraped_data.json").read_text())
    out = {}

    for ticker, d in raw.items():
        revenue = to_int_keys(d["revenue_by_year"])
        eps = to_int_keys(d["eps_by_year"])
        equity_total = to_int_keys(d["equity_by_year"])
        shares = to_int_keys(d["shares_by_year"])
        fcf = to_int_keys(d["fcf_by_year"])
        roic = to_int_keys(d.get("roic_by_year", {}))
        pe = to_int_keys(d.get("pe_by_year", {}))
        debt = to_int_keys(d.get("long_term_debt_by_year", {}))

        bvps = {y: equity_total[y] / shares[y] for y in equity_total if y in shares and shares[y]}

        sales_m = metric_block(revenue)
        eps_m = metric_block(eps)
        equity_m = metric_block(bvps)
        fcf_m = metric_block(fcf)
        roic_m = metric_block(roic, use_average=True)

        big_five = {"roic": roic_m, "sales": sales_m, "eps": eps_m, "equity": equity_m, "fcf": fcf_m}
        green_flags = [big_five[k]["green"] for k in ["roic", "sales", "eps", "equity", "fcf"]]
        moat = assess_moat(green_flags)

        latest_year = max(debt) if debt else None
        latest_debt = debt.get(latest_year) if latest_year else None
        latest_fcf_year = max(fcf) if fcf else None
        latest_fcf = fcf.get(latest_fcf_year) if latest_fcf_year else None
        d_years = debt_payback_years(latest_debt, latest_fcf)

        pe_vals = [v for v in pe.values() if v and v > 0]
        hist_avg_pe = sum(pe_vals) / len(pe_vals) if pe_vals else None

        sticker = compute_sticker_price(
            current_eps=d.get("current_eps_ttm"),
            historical_equity_growth=equity_m["longest_value"],
            analyst_growth_estimate=d.get("analyst_growth_estimate"),
            historical_avg_pe=hist_avg_pe,
            current_price=d.get("current_price"),
            fallback_eps_growth=eps_m["longest_value"],
        )

        out[ticker] = {
            "ticker": ticker,
            "name": d["name"],
            "sector": d.get("sector"),
            "industry": d.get("industry"),
            "current_price": d.get("current_price"),
            "current_eps": d.get("current_eps_ttm"),
            "raw": {
                "sales_by_year": revenue,
                "eps_by_year": eps,
                "equity_by_year": bvps,
                "fcf_by_year": fcf,
                "roic_by_year": roic,
                "pe_by_year": pe,
            },
            "big_five": big_five,
            "moat": {"green_count": moat.green_count, "total": moat.total, "level": moat.level, "label": moat.label},
            "debt": {
                "years": (None if d_years is None else (None if d_years == float("inf") else d_years)),
                "unpayable": d_years == float("inf"),
                "color": debt_color(d_years),
                "long_term_debt": latest_debt,
                "free_cash_flow": latest_fcf,
            },
            "sticker": {
                "current_eps": sticker.current_eps,
                "growth_rate": sticker.growth_rate,
                "growth_rate_source": sticker.growth_rate_source,
                "default_pe": sticker.default_pe,
                "historical_pe": sticker.historical_pe,
                "rule1_pe": sticker.rule1_pe,
                "future_eps": sticker.future_eps,
                "future_price": sticker.future_price,
                "sticker_price": sticker.sticker_price,
                "mos_price": sticker.mos_price,
                "current_price": sticker.current_price,
                "verdict": sticker.verdict,
            },
            "sources": [
                f"https://stockanalysis.com/stocks/{ticker}/financials/",
                f"https://stockanalysis.com/stocks/{ticker}/financials/balance-sheet/",
                f"https://stockanalysis.com/stocks/{ticker}/financials/cash-flow-statement/",
                f"https://stockanalysis.com/stocks/{ticker}/financials/ratios/",
                f"https://stockanalysis.com/stocks/{ticker}/forecast/",
            ],
        }
        sp_str = f"${sticker.sticker_price:.2f}" if sticker.sticker_price else "n/a"
        print(f"{ticker}: moat level {moat.level} ({moat.green_count}/5 green), "
              f"sticker {sp_str}, debt {d_years}")

    Path("dashboard_data.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote dashboard_data.json with {len(out)} tickers")


if __name__ == "__main__":
    main()
