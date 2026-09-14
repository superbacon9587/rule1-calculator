"""
Local Rule #1 dashboard.

Run with `python app.py`, then open http://127.0.0.1:5000 in a browser.
This is a local-only Flask app: nothing here is published or hosted
anywhere, and no data leaves your machine except the live request to
Yahoo Finance for whatever ticker you type in.

The heavy lifting (fetching statements, computing the Big Five, the
Sticker Price, the moat rating) is entirely the already-tested `rule1`
package -- this file just exposes it over a few HTTP routes and serves
a small HTML/CSS/JS front end.
"""
from __future__ import annotations

import io
import json
import traceback
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, abort

from rule1.analysis import analyze
from rule1.metrics import assess_moat
from rule1 import report as report_mod

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

app = Flask(__name__)

# In-memory cache so re-typing a ticker (or switching between the growth
# chart and debt gauge) doesn't refetch/recompute every time.
_cache: "dict[str, object]" = {}

# Offline fallback: the 8 tickers scraped earlier in this project, used only
# if a live yfinance fetch fails (e.g. no network, or Yahoo rate-limits).
_FALLBACK_PATH = BASE_DIR / "dashboard_data.json"
_fallback_cache = None


def _load_fallback():
    global _fallback_cache
    if _fallback_cache is None:
        if _FALLBACK_PATH.exists():
            _fallback_cache = json.loads(_FALLBACK_PATH.read_text())
        else:
            _fallback_cache = {}
    return _fallback_cache


def _result_to_json(result) -> dict:
    company = result.company

    def series(d):
        return {str(y): v for y, v in sorted(d.items())}

    big_five = {}
    for key, m in result.big_five.items():
        big_five[key] = {
            "label": m.label,
            "windows": {str(w): v for w, v in m.windows.items()},
            "longest_window": m.longest_window,
            "longest_value": m.longest_value,
            "green": m.green,
        }

    debt_years = result.debt_years
    debt_unpayable = debt_years == float("inf")

    return {
        "ticker": company.ticker,
        "name": company.name,
        "sector": company.sector,
        "industry": company.industry,
        "currency": company.currency,
        "current_price": company.current_price,
        "current_eps": company.current_eps,
        "current_pe": company.current_pe,
        "data_years_available": result.data_years_available,
        "series": {
            "sales": series(company.sales_by_year),
            "eps": series(company.eps_by_year),
            "equity": series(company.equity_by_year),
            "fcf": series(company.fcf_by_year),
            "roic": series(company.roic_by_year),
            "pe": series(company.pe_by_year),
        },
        "big_five": big_five,
        "debt": {
            "years": None if debt_years is None or debt_unpayable else debt_years,
            "unpayable": debt_unpayable,
            "color": result.debt_color,
            "long_term_debt": company.long_term_debt,
            "free_cash_flow": company.free_cash_flow_ttm,
        },
        "moat": {
            "green_count": result.moat.green_count,
            "total": result.moat.total,
            "level": result.moat.level,
            "label": result.moat.label,
        },
        "sticker": {
            "current_eps": result.sticker.current_eps,
            "growth_rate": result.sticker.growth_rate,
            "growth_rate_source": result.sticker.growth_rate_source,
            "default_pe": result.sticker.default_pe,
            "historical_pe": result.sticker.historical_pe,
            "rule1_pe": result.sticker.rule1_pe,
            "future_eps": result.sticker.future_eps,
            "future_price": result.sticker.future_price,
            "sticker_price": result.sticker.sticker_price,
            "mos_price": result.sticker.mos_price,
            "current_price": result.sticker.current_price,
            "verdict": result.sticker.verdict,
        },
        "leadership": company.officers,
        "sources": company.source_urls,
        "warnings": company.warnings,
        "offline_fallback": False,
    }


def _fallback_to_json(ticker: str) -> "dict | None":
    """Reshape a cached dashboard_data.json record into the same shape
    _result_to_json produces, so the front end doesn't need two code paths."""
    data = _load_fallback().get(ticker)
    if not data:
        return None

    raw = data["raw"]
    labels = {
        "roic": "ROIC",
        "sales": "Sales growth",
        "eps": "EPS growth",
        "equity": "Equity (BVPS) growth",
        "fcf": "Free cash flow growth",
    }
    big_five = {
        key: {**block, "label": labels.get(key, key)}
        for key, block in data["big_five"].items()
    }

    return {
        "ticker": data["ticker"],
        "name": data["name"],
        "sector": data.get("sector"),
        "industry": data.get("industry"),
        "currency": "USD",
        "current_price": data.get("current_price"),
        "current_eps": data.get("current_eps"),
        "current_pe": None,
        "data_years_available": len(raw.get("sales_by_year", {})),
        "series": {
            "sales": raw.get("sales_by_year", {}),
            "eps": raw.get("eps_by_year", {}),
            "equity": raw.get("equity_by_year", {}),
            "fcf": raw.get("fcf_by_year", {}),
            "roic": raw.get("roic_by_year", {}),
            "pe": raw.get("pe_by_year", {}),
        },
        "big_five": big_five,
        "debt": data["debt"],
        "moat": data["moat"],
        "sticker": data["sticker"],
        "leadership": [],  # not part of the offline cache -- live fetch only
        "sources": data.get("sources", []),
        "warnings": ["Live data wasn't reachable -- showing cached figures from an earlier pull."],
        "offline_fallback": True,
    }


def _get_result(ticker: str):
    """Live-fetch + compute, cached in memory. Raises on failure."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Enter a ticker symbol.")
    if ticker not in _cache:
        _cache[ticker] = analyze(ticker)
    return _cache[ticker]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze")
def api_analyze():
    ticker = request.args.get("ticker", "")
    ticker_u = ticker.strip().upper()
    if not ticker_u:
        return jsonify({"error": "Enter a ticker symbol."}), 400

    try:
        result = _get_result(ticker_u)
        return jsonify(_result_to_json(result))
    except Exception as exc:  # noqa: BLE001 -- surface any fetch/compute failure to the UI
        fallback = _fallback_to_json(ticker_u)
        if fallback:
            return jsonify(fallback)
        return jsonify({
            "error": f"Couldn't fetch or compute data for \"{ticker_u}\": {exc}",
        }), 502


@app.route("/api/chart/growth/<ticker>.png")
def api_chart_growth(ticker: str):
    ticker_u = ticker.strip().upper()
    try:
        result = _get_result(ticker_u)
        png = report_mod.growth_chart_png_bytes(result)
        return send_file(io.BytesIO(png), mimetype="image/png")
    except Exception:
        abort(404)


@app.route("/api/chart/debt/<ticker>.png")
def api_chart_debt(ticker: str):
    ticker_u = ticker.strip().upper()
    try:
        result = _get_result(ticker_u)
        png = report_mod.debt_gauge_png_bytes(result)
        return send_file(io.BytesIO(png), mimetype="image/png")
    except Exception:
        abort(404)


@app.route("/api/chart/bigfive/<ticker>/<metric>.png")
def api_chart_bigfive(ticker: str, metric: str):
    ticker_u = ticker.strip().upper()
    metric_l = metric.strip().lower()
    if metric_l not in report_mod.BIGFIVE_BAR_LABEL:
        abort(404)
    try:
        result = _get_result(ticker_u)
        png = report_mod.bigfive_bar_chart_png_bytes(result, metric_l)
        return send_file(io.BytesIO(png), mimetype="image/png")
    except Exception:
        abort(404)


@app.route("/api/moat/<int:level>.png")
def api_moat_image(level: int):
    if level < 1 or level > 5:
        abort(404)
    path = ASSETS_DIR / f"level{level}.png"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")


if __name__ == "__main__":
    print("\nRule #1 Calculator -- open http://127.0.0.1:5000 in your browser\n")
    app.run(debug=True, port=5000)
