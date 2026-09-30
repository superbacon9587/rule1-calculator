"""
Turns one or more AnalysisResult objects into:
  1. A colored terminal report (green/red Big Five, debt gauge, Sticker
     Price / MOS verdict, source links).
  2. Saved chart images (growth-rate multi-line chart, debt gauge) using
     matplotlib, in the same style as the book's example charts.
  3. The matching castle/moat graphic, copied from assets/ into the output
     folder.
  4. A side-by-side comparison table/chart when more than one ticker is
     analyzed.

No web server, no published page -- everything here writes to files on disk
and to the terminal, per the requested "script only" scope.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

from .analysis import AnalysisResult, WINDOWS
from .metrics import MARR, PROJECTION_YEARS

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# ---------------------------------------------------------------------------
# ANSI colors (work in any modern terminal; on Windows, colorama --if
# installed-- is initialized automatically so colors render in cmd.exe too)
# ---------------------------------------------------------------------------

try:
    import colorama
    colorama.init()
except ImportError:
    pass

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
COLORS = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "orange": "\033[38;5;208m",
    "gray": "\033[90m",
    "cyan": "\033[36m",
    "white": "\033[97m",
}


def c(text: str, color: str, bold: bool = False) -> str:
    prefix = COLORS.get(color, "")
    b = BOLD if bold else ""
    return f"{b}{prefix}{text}{RESET}"


def _fmt_pct(x):
    return f"{x*100:.1f}%" if x is not None else "  n/a "


def _fmt_money(x, currency="USD"):
    if x is None:
        return "n/a"
    sym = "$" if currency in (None, "USD") else f"{currency} "
    return f"{sym}{x:,.2f}"


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------

def print_report(result: AnalysisResult) -> None:
    company = result.company
    print()
    print(c(f"═══ {company.name} ({company.ticker}) ═══", "white", bold=True))
    subtitle = " / ".join(x for x in [company.sector, company.industry] if x)
    if subtitle:
        print(c(subtitle, "gray"))
    if result.data_years_available < 10:
        print(c(f"Note: only {result.data_years_available} years of statement history were "
                 f"available from the free data source, so some 10-year figures below are "
                 f"shown as the longest window the data supports (this is exactly what the "
                 f"book itself recommends doing).", "yellow"))
    print()

    # ---- Big Five table -----------------------------------------------------
    header = f"{'Metric':<24}" + "".join(f"{w}yr".rjust(9) for w in WINDOWS)
    print(c(header, "white", bold=True))
    for key, metric in result.big_five.items():
        row = f"{metric.label:<24}"
        for w in WINDOWS:
            val = metric.windows.get(w)
            if val is None:
                cell = "n/a".rjust(9)
                row += c(cell, "gray")
            else:
                is_longest = (w == metric.longest_window)
                color = "green" if val >= 0.10 else "red"
                cell = _fmt_pct(val).rjust(9)
                row += c(cell, color, bold=is_longest)
        print(row)
    green_n = result.moat.green_count
    print(c(f"\n{green_n} of 5 Big Five numbers are >= 10% (green).", "cyan"))
    print()

    # ---- Debt -----------------------------------------------------------------
    debt_label = "n/a" if result.debt_years is None else (
        "no debt" if result.debt_years == 0 else
        ("unpayable (negative FCF)" if result.debt_years == float("inf") else
         f"{result.debt_years:.1f} years of free cash flow to pay off")
    )
    print(c("Debt:", "white", bold=True) + " " + c(debt_label, result.debt_color))
    print(c("  (Rule #1 wants long-term debt payable in <= 3 years of free cash flow)", "gray"))
    print()

    # ---- Moat / castle ----------------------------------------------------------
    print(c(f"Moat assessment: {result.moat.label}", "white", bold=True))
    print(c(f"  ({result.moat.green_count}/5 Big Five numbers green -> castle level {result.moat.level} "
             f"[1 = narrowest moat, 5 = widest])", "gray"))
    print()

    # ---- Sticker Price / MOS -----------------------------------------------------
    s = result.sticker
    print(c("Sticker Price & Margin of Safety (Chapter 9 method):", "white", bold=True))
    print(f"  Current EPS:            {_fmt_money(s.current_eps, company.currency)}")
    print(f"  Rule #1 growth rate:    {_fmt_pct(s.growth_rate)}  ({s.growth_rate_source})")
    print(f"  Default PE (2x growth): {s.default_pe:.1f}" if s.default_pe is not None else "  Default PE:             n/a")
    print(f"  Historical avg PE:      {s.historical_pe:.1f}" if s.historical_pe is not None else "  Historical avg PE:      n/a")
    print(f"  Rule #1 PE used:        {s.rule1_pe:.1f}" if s.rule1_pe is not None else "  Rule #1 PE used:        n/a")
    print(f"  Future EPS ({PROJECTION_YEARS}y):        {_fmt_money(s.future_eps, company.currency)}")
    print(f"  Future price ({PROJECTION_YEARS}y):      {_fmt_money(s.future_price, company.currency)}")
    print(c(f"  Sticker Price:          {_fmt_money(s.sticker_price, company.currency)}", "cyan", bold=True))
    print(c(f"  Margin-of-Safety price: {_fmt_money(s.mos_price, company.currency)}", "cyan", bold=True))
    print(f"  Current market price:   {_fmt_money(s.current_price, company.currency)}")
    verdict_color = "gray"
    if s.current_price is not None and s.mos_price is not None:
        if s.current_price <= s.mos_price:
            verdict_color = "green"
        elif s.sticker_price is not None and s.current_price <= s.sticker_price:
            verdict_color = "yellow"
        else:
            verdict_color = "red"
    print(c(f"  Verdict: {s.verdict}", verdict_color, bold=True))
    print()

    # ---- Sources ----------------------------------------------------------------
    print(c("Data sources:", "white", bold=True))
    for url in company.source_urls:
        print(c(f"  - {url}", "gray"))

    if company.warnings:
        print()
        print(c("Notes on this data pull:", "yellow", bold=True))
        for w in company.warnings:
            print(c(f"  - {w}", "yellow"))
    print()


def print_comparison_table(results: Iterable[AnalysisResult]) -> None:
    results = list(results)
    print()
    print(c("═══ Comparison ═══", "white", bold=True))
    header = f"{'Company':<22}" + "".join(f"{k.upper()}".rjust(9) for k in ["ROIC", "SALES", "EPS", "EQUITY", "FCF"])
    print(c(header, "white", bold=True))
    for r in results:
        label = f"{r.company.ticker}"
        row = f"{label:<22}"
        for key in ["roic", "sales", "eps", "equity", "fcf"]:
            m = r.big_five[key]
            val = m.longest_value
            if val is None:
                row += "n/a".rjust(9)
            else:
                color = "green" if val >= 0.10 else "red"
                row += c(_fmt_pct(val).rjust(9), color)
        print(row)
    print()
    print(f"{'Company':<22}{'Debt (yrs)':>12}{'Moat lvl':>10}{'Sticker':>12}{'MOS':>12}{'Price':>12}")
    for r in results:
        s = r.sticker
        debt_txt = "n/a" if r.debt_years is None else (
            "-" if r.debt_years == float("inf") else f"{r.debt_years:.1f}")
        print(f"{r.company.ticker:<22}{c(debt_txt, r.debt_color):>21}{r.moat.level:>10}"
              f"{_fmt_money(s.sticker_price):>12}{_fmt_money(s.mos_price):>12}"
              f"{_fmt_money(s.current_price):>12}")
    print()


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _lazy_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


# Validated categorical palette (dataviz skill, fixed adjacency-safe order)
# and the fixed status palette, used consistently across every chart.
SERIES_COLORS = {
    "Sales": "#2a78d6",           # slot 1: blue
    "EPS": "#eb6834",             # slot 2: orange
    "Equity (BVPS)": "#1baf7a",   # slot 3: aqua
    "Free Cash Flow": "#c98500",  # slot 4: yellow (darkened for legibility on white)
    "ROIC": "#4a3aa7",            # slot 7: violet -- ROIC isn't in the growth-trend
                                   # line chart, but gets its own bar-chart color
}
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#ffffff"  # matches the dashboard's white page/card background exactly


def _index_to_100(series: "dict[int, float]"):
    """Rebase a {year: value} series to its own first available year = 100.
    This is what keeps Sales/FCF (raw dollars, often in the billions) from
    reading as a separate outlier band from EPS/Equity (dollars per share)
    on a shared log-scale axis -- every line starts at the same 100 and the
    log scale then shows genuine relative growth, not unit mismatch."""
    pts = sorted((y, v) for y, v in series.items() if v is not None and v > 0)
    if len(pts) < 2:
        return []
    base = pts[0][1]
    if base <= 0:
        return []
    return [(y, v / base * 100.0) for y, v in pts]


def _build_growth_chart_fig(result: AnalysisResult):
    plt = _lazy_matplotlib()
    company = result.company
    fig, ax = plt.subplots(figsize=(11, 4.4))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    series_map = {
        "Sales": company.sales_by_year,
        "EPS": company.eps_by_year,
        "Equity (BVPS)": company.equity_by_year,
        "Free Cash Flow": company.fcf_by_year,
    }
    styles = {"Sales": "-", "EPS": "-", "Equity (BVPS)": "-", "Free Cash Flow": "--"}

    any_plotted = False
    for label, series in series_map.items():
        pts = _index_to_100(series)
        if len(pts) < 2:
            continue
        years, values = zip(*pts)
        ax.plot(years, values, label=label, color=SERIES_COLORS[label], linestyle=styles[label],
                linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE, markeredgewidth=1)
        any_plotted = True

    ax.set_yscale("log")
    if any_plotted:
        ax.axhline(100, color=GRID, linestyle="-", linewidth=1, zorder=0)

    ax.set_title(f"{company.ticker} — Growth Trends (indexed to first year = 100)",
                 fontsize=14, fontweight="bold", color=INK, loc="left")
    ax.set_xlabel("Fiscal Year", color=MUTED, fontsize=10)
    ax.set_ylabel("Indexed value (log scale)", color=MUTED, fontsize=10)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, which="major", linestyle="-", linewidth=0.8, color=GRID, alpha=0.9)
    ax.grid(True, which="minor", linestyle=":", linewidth=0.5, color=GRID, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    if any_plotted:
        legend = ax.legend(loc="upper left", fontsize=9, frameon=False)
        for text in legend.get_texts():
            text.set_color(INK)
    else:
        ax.text(0.5, 0.5, "Not enough data to chart", ha="center", va="center",
                 transform=ax.transAxes, color=MUTED)
    fig.tight_layout()
    return fig


def save_growth_chart(result: AnalysisResult, out_dir: Path) -> Path:
    plt = _lazy_matplotlib()
    fig = _build_growth_chart_fig(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.company.ticker}_growth_chart.png"
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def growth_chart_png_bytes(result: AnalysisResult) -> bytes:
    import io
    plt = _lazy_matplotlib()
    fig = _build_growth_chart_fig(result)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _build_debt_gauge_fig(result: AnalysisResult):
    plt = _lazy_matplotlib()
    company = result.company
    fig, ax = plt.subplots(figsize=(9, 3.1))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    bands = [(0, 1, STATUS_GOOD), (1, 2, STATUS_WARNING), (2, 3, STATUS_SERIOUS), (3, 4.5, STATUS_CRITICAL)]
    band_labels = {0: "Great (<1yr)", 1: "Good (1-2yr)", 2: "Watch (2-3yr)", 3: "Risky (3yr+)"}
    for lo, hi, color in bands:
        ax.barh(0, hi - lo, left=lo, height=0.85, color=color, edgecolor=SURFACE, linewidth=3)
        ax.text((lo + min(hi, 4.5)) / 2, -0.62, band_labels[lo], ha="center", va="top",
                fontsize=9.5, color=MUTED)

    years = result.debt_years
    if years is not None and years != float("inf"):
        marker_x = min(max(years, 0.06), 4.4)
        ax.plot([marker_x], [0], marker="v", color=INK, markersize=22, clip_on=False, zorder=5)
        label_x = min(max(marker_x, 0.4), 4.05)
        ax.text(label_x, 0.85, f"{years:.1f} yrs", ha="center", fontsize=16, fontweight="bold", color=INK)
    elif years == float("inf"):
        ax.text(4.45, 0.85, "FCF negative", ha="right", fontsize=15, fontweight="bold", color=STATUS_CRITICAL)

    ax.set_xlim(-0.05, 4.5)
    ax.set_ylim(-1.0, 1.25)
    ax.set_yticks([])
    ax.set_xticks([0, 1, 2, 3, 4])
    ax.tick_params(colors=MUTED, labelsize=11)
    ax.set_xlabel("Years of free cash flow to pay off long-term debt", color=MUTED, fontsize=12)
    ax.set_title(f"{company.ticker} — Debt Payback", fontsize=16, fontweight="bold", color=INK, loc="left")
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    return fig


def save_debt_gauge(result: AnalysisResult, out_dir: Path) -> Path:
    plt = _lazy_matplotlib()
    fig = _build_debt_gauge_fig(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.company.ticker}_debt_gauge.png"
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def debt_gauge_png_bytes(result: AnalysisResult) -> bytes:
    import io
    plt = _lazy_matplotlib()
    fig = _build_debt_gauge_fig(result)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Per-metric "click a Big Five tile" bar charts -- one series' raw values by
# year, in the style of the book's own bar-chart examples (e.g. Garmin's
# EPS 1995-2004 chart), rather than a growth rate.
# ---------------------------------------------------------------------------

BIGFIVE_BAR_LABEL = {
    "sales": "Sales",
    "eps": "EPS",
    "equity": "Equity (BVPS)",
    "fcf": "Free Cash Flow",
    "roic": "ROIC",
}
BIGFIVE_BAR_KIND = {
    "sales": "currency_large",
    "eps": "currency_per_share",
    "equity": "currency_per_share",
    "fcf": "currency_large",
    "roic": "percent",
}


def _scale_values(kind: str, raw_values):
    """Pick a sensible unit/scale for a metric's raw values and a formatter
    for the bar labels, so a $391B revenue series doesn't print 11-digit
    y-axis ticks."""
    if kind == "percent":
        return [v * 100 for v in raw_values], "%", (lambda v: f"{v:.1f}%")
    if kind == "currency_per_share":
        return list(raw_values), "$/share", (lambda v: f"${v:.2f}")
    maxabs = max((abs(v) for v in raw_values), default=0)
    if maxabs >= 1e9:
        return [v / 1e9 for v in raw_values], "$B", (lambda v: f"${v:.1f}B")
    if maxabs >= 1e6:
        return [v / 1e6 for v in raw_values], "$M", (lambda v: f"${v:.1f}M")
    if maxabs >= 1e3:
        return [v / 1e3 for v in raw_values], "$K", (lambda v: f"${v:.1f}K")
    return list(raw_values), "$", (lambda v: f"${v:,.0f}")


def _build_bigfive_bar_fig(result: AnalysisResult, metric_key: str):
    plt = _lazy_matplotlib()
    company = result.company
    label = BIGFIVE_BAR_LABEL.get(metric_key, metric_key)
    kind = BIGFIVE_BAR_KIND.get(metric_key, "currency_large")
    color = SERIES_COLORS.get(label, INK)
    series = getattr(company, f"{metric_key}_by_year", {}) or {}

    fig, ax = plt.subplots(figsize=(9, 4.4))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    pts = sorted((y, v) for y, v in series.items() if v is not None)
    if not pts:
        ax.text(0.5, 0.5, "Not enough data to chart", ha="center", va="center",
                 transform=ax.transAxes, color=MUTED)
    else:
        years, raw_values = zip(*pts)
        values, unit, fmt = _scale_values(kind, raw_values)
        bars = ax.bar([str(y) for y in years], values, color=color,
                       edgecolor=SURFACE, linewidth=1.2, width=0.62)
        for rect, v in zip(bars, values):
            va = "bottom" if v >= 0 else "top"
            offset = (max(values) - min(values) or 1) * 0.02
            ax.text(rect.get_x() + rect.get_width() / 2, v + (offset if v >= 0 else -offset),
                    fmt(v), ha="center", va=va, fontsize=9, fontweight="600", color=INK)
        ylabel = label if kind == "percent" else f"{label} ({unit})"
        ax.set_ylabel(ylabel, color=MUTED, fontsize=10.5)
        ax.axhline(0, color=GRID, linewidth=1)

    ax.set_title(f"{company.ticker} — {label} by year", fontsize=14, fontweight="bold", color=INK, loc="left")
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(True, axis="y", linestyle="-", linewidth=0.8, color=GRID, alpha=0.9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    return fig


def bigfive_bar_chart_png_bytes(result: AnalysisResult, metric_key: str) -> bytes:
    import io
    plt = _lazy_matplotlib()
    fig = _build_bigfive_bar_fig(result, metric_key)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def save_comparison_chart(results: Iterable[AnalysisResult], out_dir: Path) -> Path:
    plt = _lazy_matplotlib()
    results = list(results)
    n = len(results)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5), squeeze=False)
    axes = axes[0]

    series_defs = [
        ("Sales", "#1f77b4", "-"),
        ("EPS", "#111111", "-"),
        ("Equity (BVPS)", "#7f7f7f", "-"),
        ("Free Cash Flow", "#bbbbbb", "--"),
    ]
    for ax, r in zip(axes, results):
        company = r.company
        series_map = {
            "Sales": company.sales_by_year,
            "EPS": company.eps_by_year,
            "Equity (BVPS)": company.equity_by_year,
            "Free Cash Flow": company.fcf_by_year,
        }
        for label, color, style in series_defs:
            pts = sorted((y, v) for y, v in series_map[label].items() if v is not None and v > 0)
            if len(pts) < 2:
                continue
            years, values = zip(*pts)
            ax.plot(years, values, label=label, color=color, linestyle=style, marker="o", markersize=3)
        ax.set_yscale("log")
        ax.set_title(company.ticker, fontsize=12, fontweight="bold")
        ax.grid(True, which="both", linestyle=":", alpha=0.4)
        ax.legend(loc="upper left", fontsize=8)

    fig.suptitle("Growth Rate Comparison", fontsize=14, fontweight="bold")
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    tickers = "_vs_".join(r.company.ticker for r in results)
    path = out_dir / f"comparison_{tickers}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def copy_castle_image(result: AnalysisResult, out_dir: Path) -> Path:
    src = ASSETS_DIR / f"level{result.moat.level}.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{result.company.ticker}_moat_level{result.moat.level}.png"
    if src.exists():
        shutil.copyfile(src, dest)
    return dest
