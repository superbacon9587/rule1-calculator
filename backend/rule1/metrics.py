"""
Core Rule #1 math: CAGR growth rates, ROIC, the debt payback ratio, and the
Sticker Price / Margin-of-Safety price.

Every function here is a pure function (numbers in, numbers out) so it can be
unit-tested without touching the network. See tests/test_metrics.py, which
checks these functions against the worked examples in the book (Apollo
Group, Garmin, Harley-Davidson).

Book reference: Phil Town, *Rule #1*, chapters 5 ("The Big Five Numbers"),
6 ("Calculate the Big Five"), 8 ("Demand a Margin of Safety"), and 9
("Calculate the Sticker Price").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Growth rates (Chapter 6: "Calculate the Big Five")
# ---------------------------------------------------------------------------

def cagr(start: Optional[float], end: Optional[float], years: float) -> Optional[float]:
    """
    Compound annual growth rate between two values `years` apart.

    Returns None (rather than a misleading number) whenever a rate can't be
    meaningfully computed -- e.g. the starting value is zero/negative, which
    the book itself says to treat as a disqualifying red flag rather than a
    number ("Negative numbers? Forget it. Just put a zero.").
    """
    if start is None or end is None or years <= 0:
        return None
    if start <= 0:
        # Can't take a fractional root of a non-positive base -> not a
        # meaningful compounding rate. Chapter 6 treats this as a "0" (fail).
        return 0.0
    ratio = end / start
    if ratio <= 0:
        return 0.0
    return ratio ** (1.0 / years) - 1.0


def growth_rate_windows(series_by_year: "dict[int, float]", windows=(10, 5, 3, 1)) -> "dict[int, Optional[float]]":
    """
    Given a {year: value} series (most years present, sorted ascending),
    compute the trailing CAGR for each requested window (in years) that the
    data actually supports. Mirrors the book's practice of reporting
    whatever the source has -- ten years when available, otherwise five and
    one ("Nobody does ten-year growth rates for free -- yet.").
    """
    years = sorted(y for y, v in series_by_year.items() if v is not None)
    out: "dict[int, Optional[float]]" = {}
    if not years:
        return {w: None for w in windows}
    last_year = years[-1]
    last_val = series_by_year[last_year]
    max_span = years[-1] - years[0]
    for w in windows:
        # Allow a window whose span is close to what's on hand (e.g. ten
        # annual data points span nine intervals -- the book still calls
        # that a "ten-year" rate). Only drop windows the data falls well
        # short of, so a short window isn't mislabeled with a long one.
        if w > max_span + 1:
            out[w] = None
            continue
        target_year = last_year - w
        if target_year not in series_by_year or series_by_year[target_year] is None:
            # fall back to the closest earlier year we do have; if the
            # window reaches further back than any data we have, just use
            # the earliest year on hand (e.g. a "10yr" window against ten
            # data points spanning nine years -- close enough to report).
            candidates = [y for y in years if y <= target_year]
            if not candidates:
                if years[0] >= last_year:
                    out[w] = None
                    continue
                target_year = years[0]
            else:
                target_year = max(candidates)
            w_actual = last_year - target_year
        else:
            w_actual = w
        start_val = series_by_year[target_year]
        out[w] = cagr(start_val, last_val, w_actual)
    return out


def average_windows(series_by_year: "dict[int, float]", windows=(10, 5, 3, 1)) -> "dict[int, Optional[float]]":
    """
    Like growth_rate_windows, but for numbers that are already a rate/ratio
    (ROIC) rather than a dollar amount to compound. The book reports ROIC
    over a period as the *average* of that period's yearly values (e.g.
    Apollo Group: "last ten years: 32%, last five years: 30%, last year:
    36%"), not a CAGR between the first and last year.
    """
    years = sorted(y for y, v in series_by_year.items() if v is not None)
    out: "dict[int, Optional[float]]" = {}
    if not years:
        return {w: None for w in windows}
    last_year = years[-1]
    for w in windows:
        window_years = [y for y in years if y >= last_year - w + 1]
        if not window_years:
            out[w] = None
            continue
        vals = [series_by_year[y] for y in window_years]
        out[w] = sum(vals) / len(vals)
    return out


# ---------------------------------------------------------------------------
# ROIC (Chapter 5 sidebar: NOPAT / (equity + debt))
# ---------------------------------------------------------------------------

def compute_roic(ebit: Optional[float], tax_rate: Optional[float],
                  equity: Optional[float], debt: Optional[float]) -> Optional[float]:
    """Return on Invested Capital = NOPAT / (Equity + Debt)."""
    if ebit is None or equity is None or debt is None:
        return None
    if tax_rate is None:
        tax_rate = 0.21  # reasonable statutory-rate fallback
    tax_rate = min(max(tax_rate, 0.0), 0.60)
    nopat = ebit * (1 - tax_rate)
    invested_capital = equity + debt
    if invested_capital <= 0:
        return None
    return nopat / invested_capital


# ---------------------------------------------------------------------------
# Debt payback (Chapter 5: "The Rule on Debt")
# ---------------------------------------------------------------------------

def debt_payback_years(long_term_debt: Optional[float], free_cash_flow: Optional[float]) -> Optional[float]:
    """Years of free cash flow needed to pay off all long-term debt."""
    if long_term_debt is None or free_cash_flow is None:
        return None
    if long_term_debt <= 0:
        return 0.0
    if free_cash_flow <= 0:
        return float("inf")
    return long_term_debt / free_cash_flow


# ---------------------------------------------------------------------------
# Sticker Price & Margin of Safety (Chapter 9)
# ---------------------------------------------------------------------------

MARR = 0.15          # Rule #1 minimum acceptable rate of return
PROJECTION_YEARS = 10  # Rule #1's "10-10 rule"


@dataclass
class StickerPriceResult:
    current_eps: Optional[float]
    growth_rate: Optional[float]           # the "Rule #1 growth rate" actually used
    growth_rate_source: str                # which number won and why
    default_pe: Optional[float]
    historical_pe: Optional[float]
    rule1_pe: Optional[float]
    future_eps: Optional[float]
    future_price: Optional[float]
    sticker_price: Optional[float]
    mos_price: Optional[float]
    current_price: Optional[float]
    verdict: str = ""


# Tickers whose book value per share is shrunk by heavy share buybacks, so
# historical equity growth understates how fast the business is growing
# (near zero or negative). For these the analyst estimate is used on its
# own instead of the lower-of rule; every other ticker keeps the lower-of rule.
BUYBACK_DISTORTED_TICKERS = frozenset({"AAPL", "KO", "WMT", "JNJ", "PG", "XOM", "CSCO"})
BUYBACK_DISTORTED_SOURCE = "analyst 5-year estimate (equity growth distorted by buybacks)"


def pick_rule1_growth_rate(historical_equity_growth: Optional[float],
                            analyst_growth_estimate: Optional[float],
                            fallback_eps_growth: Optional[float] = None,
                            ticker: Optional[str] = None) -> "tuple[Optional[float], str]":
    """
    Chapter 9: "the single most important number for choosing a business's
    estimated future EPS growth rate is its past equity growth rate."  We
    cross-check it against the analysts' estimate and conservatively take
    the lower of the two (the book's rule of thumb).

    Exception: for a ticker in BUYBACK_DISTORTED_TICKERS the analyst
    estimate is used directly. Without one, the usual rule applies.
    """
    if (ticker is not None and ticker.strip().upper() in BUYBACK_DISTORTED_TICKERS
            and analyst_growth_estimate is not None):
        return analyst_growth_estimate, BUYBACK_DISTORTED_SOURCE

    candidates = []
    if historical_equity_growth is not None:
        candidates.append(("historical equity growth", historical_equity_growth))
    if analyst_growth_estimate is not None:
        candidates.append(("analyst 5-year estimate", analyst_growth_estimate))
    if not candidates and fallback_eps_growth is not None:
        candidates.append(("historical EPS growth (fallback)", fallback_eps_growth))
    if not candidates:
        return None, "no data available"
    if len(candidates) == 1:
        label, val = candidates[0]
        return val, label
    label, val = min(candidates, key=lambda kv: kv[1])
    return val, f"lower of historical equity growth & analyst estimate ({label})"


def compute_sticker_price(current_eps: Optional[float],
                           historical_equity_growth: Optional[float],
                           analyst_growth_estimate: Optional[float],
                           historical_avg_pe: Optional[float],
                           current_price: Optional[float] = None,
                           fallback_eps_growth: Optional[float] = None,
                           ticker: Optional[str] = None) -> StickerPriceResult:
    growth_rate, source = pick_rule1_growth_rate(
        historical_equity_growth, analyst_growth_estimate, fallback_eps_growth, ticker)

    # Growth rate has to be sane and positive to project a Sticker Price at all.
    if growth_rate is not None:
        growth_rate = max(growth_rate, 0.0)
        growth_rate = min(growth_rate, 0.60)  # book: cap unrealistic assumptions

    if current_eps is None or current_eps <= 0 or growth_rate is None:
        return StickerPriceResult(
            current_eps=current_eps, growth_rate=growth_rate, growth_rate_source=source,
            default_pe=None, historical_pe=historical_avg_pe, rule1_pe=None,
            future_eps=None, future_price=None, sticker_price=None, mos_price=None,
            current_price=current_price,
            verdict="Not enough data (need a positive current EPS and a usable growth rate).",
        )

    default_pe = growth_rate * 100 * 2
    if historical_avg_pe is not None and historical_avg_pe > 0:
        rule1_pe = min(default_pe, historical_avg_pe)
    else:
        rule1_pe = default_pe
    rule1_pe = max(rule1_pe, 5.0)  # book: businesses almost never sell below ~5x

    future_eps = current_eps * (1 + growth_rate) ** PROJECTION_YEARS
    future_price = future_eps * rule1_pe
    sticker_price = future_price / (1 + MARR) ** PROJECTION_YEARS
    mos_price = sticker_price / 2.0

    verdict = _verdict(current_price, sticker_price, mos_price)

    return StickerPriceResult(
        current_eps=current_eps, growth_rate=growth_rate, growth_rate_source=source,
        default_pe=default_pe, historical_pe=historical_avg_pe, rule1_pe=rule1_pe,
        future_eps=future_eps, future_price=future_price, sticker_price=sticker_price,
        mos_price=mos_price, current_price=current_price, verdict=verdict,
    )


def implied_annual_return(current_price: Optional[float], future_price: Optional[float],
                          years: float = PROJECTION_YEARS) -> Optional[float]:
    """
    Annualized return from buying at `current_price` today if the stock
    reaches the Sticker Price walk-through's `future_price` in `years`.
    Ignores dividends. Buying exactly at the Sticker Price returns MARR,
    since the Sticker Price is `future_price` discounted at MARR.
    """
    if current_price is None or current_price <= 0 or future_price is None:
        return None
    return cagr(current_price, future_price, years)


def _verdict(current_price, sticker_price, mos_price) -> str:
    if current_price is None or sticker_price is None or mos_price is None:
        return "Can't compare to the current price (missing data)."
    if current_price <= mos_price:
        return "At or below the Margin-of-Safety price -- classic Rule #1 buy zone."
    if current_price <= sticker_price:
        return "Below Sticker Price but above the MOS price -- fairly priced, not on sale."
    return "Above Sticker Price -- Mr. Market is charging a premium; wait for a better price."


# ---------------------------------------------------------------------------
# Color / moat grading (per the calculator spec)
# ---------------------------------------------------------------------------

def is_green(rate: Optional[float], threshold: float = 0.10) -> Optional[bool]:
    if rate is None:
        return None
    return rate >= threshold


def debt_color(years: Optional[float]) -> str:
    """green (0-1), yellow (1-2), orange (2-3), red (>3 or unknown-bad)."""
    if years is None:
        return "gray"
    if years == float("inf"):
        return "red"
    if years < 1:
        return "green"
    if years < 2:
        return "yellow"
    if years < 3:
        return "orange"
    return "red"


@dataclass
class MoatAssessment:
    green_count: int
    total: int
    level: int          # 1 (narrowest/weakest moat) .. 5 (widest/healthiest)
    label: str


def assess_moat(big_five_flags: "list[Optional[bool]]") -> MoatAssessment:
    """
    Count how many of the Big Five (ROIC + 4 growth rates, using the
    longest-available period for each) are green, and translate that into
    one of the five castle/moat levels the spec asks for:
        0-1 green -> Level 1 (Very Narrow Moat / Strongly Damaged)
        2 green -> Level 2 (Narrow Moat / Deteriorating)
        3 green -> Level 3 (Moderate Moat / Showing Wear)
        4 green -> Level 4 (Wider Moat / Still Healthy)
        5 green -> Level 5 (Very Wide Moat / Healthy)
    """
    total = len(big_five_flags)
    green = sum(1 for f in big_five_flags if f)
    level_by_green = {5: 5, 4: 4, 3: 3, 2: 2, 1: 1, 0: 1}
    level = level_by_green.get(green, 1)
    labels = {
        1: "Very Narrow Moat (Strongly Damaged)",
        2: "Narrow Moat (Deteriorating)",
        3: "Moderate Moat (Showing Wear)",
        4: "Wider Moat (Still Healthy)",
        5: "Very Wide Moat (Healthy)",
    }
    return MoatAssessment(green_count=green, total=total, level=level, label=labels[level])
