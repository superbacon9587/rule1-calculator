# Rule #1 Investment Calculator

A small local dashboard that pulls a company's public financial statements
(income statement, balance sheet, cash-flow statement) and computes the
numbers Phil Town's *Rule #1* uses to judge whether a business is a
"wonderful company at an attractive price":

- The **Big Five** growth rates (sales, EPS, equity/book-value-per-share,
  free cash flow) and **ROIC**, over the 10-, 5-, 3-, and 1-year windows the
  data supports
- The **debt payback ratio** (long-term debt ÷ free cash flow)
- A **moat rating** (how many of the Big Five are green) shown as one of the
  five castle graphics
- The **Sticker Price** and **Margin-of-Safety price**, following the exact
  method in Chapter 9

Type a ticker into the search bar and it fetches and computes everything
live. Everything runs locally on your own machine — nothing here is hosted,
published, or shared with anyone; the only network traffic is your own
browser talking to a Flask server on `127.0.0.1`, and that server's own
request to Yahoo Finance for whatever ticker you type in. There's also a
plain command-line version if you'd rather not open a browser.

## Why these numbers, and where they come from

This tool follows *Rule #1*, chapters 5–9, as closely as the math allows:

- **Chapter 5 ("The Big Five Numbers")** — what ROIC, sales, EPS, equity,
  and cash growth mean, and why 10% a year is the bar, and the debt rule
  (payable within 3 years of free cash flow).
- **Chapter 6 ("Calculate the Big Five")** — how to turn raw statement
  numbers into growth rates. The code uses the exact CAGR formula
  (`(end/start)^(1/years) - 1`) rather than the book's in-your-head "Rule of
  72" shortcut, since a computer doesn't need the shortcut — but both
  methods agree closely (see `tests/test_metrics.py`, which checks this
  code's output against the book's own worked Apollo Group example and
  matches within a percentage point or two).
- **Chapter 8 ("Demand a Margin of Safety")** — the MOS price is always half
  of the Sticker Price.
- **Chapter 9 ("Calculate the Sticker Price")** — current EPS, grown at the
  Rule #1 growth rate (the lower of historical equity growth and the
  analysts' estimate) for 10 years, multiplied by a future PE (the lower of
  2× the growth rate and the historical average PE), then discounted back
  at a 15% minimum acceptable rate of return. `tests/test_metrics.py`
  reproduces the book's own Harley-Davidson example (current EPS $0.89,
  24% growth, PE 46) and gets **Sticker Price $86.97 / MOS $43.49 — matching
  the book to the penny.**

## Data source (the "scraping" part)

Financial statements are pulled from **Yahoo Finance**, via the `yfinance`
library — the same kind of free source (MSN Money / Yahoo! Finance) the
book itself uses in its own examples. Every report prints the exact Yahoo
Finance URLs the numbers came from, at the bottom.

**A free source's honest limitation:** Yahoo's free statement pages
typically go back 4–6 fiscal years, not the full 10–15 the book prefers for
judging consistency. The book itself acknowledges this ("Nobody does
ten-year growth rates for free — yet") and says to use whatever span you
have (5-year and 1-year, at minimum) rather than skip the analysis. This
tool does the same: it reports the longest window the data actually
supports, and labels it accordingly.

**A note on API drift:** Yahoo occasionally renames statement line items,
and `yfinance` follows along, which can make a field briefly stop matching.
`rule1/data.py` matches several likely label spellings for each line item
and degrades to "not available" rather than crashing — but if a whole
statement comes back empty after a Yahoo change, that's the first place to
look.

## Setup

```bash
pip install -r requirements.txt
```

(Python 3.9+. On Windows, `colorama` makes the colored terminal output work
in `cmd.exe`/PowerShell too — only needed if you use the CLI below.)

## Usage — the dashboard (recommended)

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser. Type a ticker into the
search bar (or click one of the quick-pick chips) and it fetches, computes,
and displays everything: the Big Five stat tiles (click any one to chart
that metric's raw values by year, bar-chart style), a **Leadership** card
(the company's named officers — the "Management" M — each name linking out
to Wikipedia so you can read up on them), the growth trends chart
(sales/EPS/equity/free cash flow indexed to their first year so very
different units compare cleanly on one log-scale axis), the debt-payback
gauge, the full Sticker Price walk-through as a table, and — last — the
moat castle with the Big Five ratio shown above it.

This is a plain local Flask app — `python app.py` starts a server only your
own browser can reach, and closing the terminal (or hitting Ctrl+C) stops
it. Nothing is deployed, hosted, or made reachable from outside your
machine.

If Yahoo Finance can't be reached for a ticker (no internet, or a rate
limit), the app falls back to a small built-in cache of eight tickers
(AAPL, MSFT, COST, JNJ, PG, NKE, CAT, XOM) so the layout still has
something to show, and says clearly that the figures are cached rather than
live. Leadership names come from Yahoo's live company-profile data only, so
that card shows a plain note instead in cached/offline mode.

## Usage — the command line (optional)

The same calculation engine is also available as a terminal tool, if you'd
rather not open a browser:

Analyze one company:

```bash
python analyze.py AAPL
```

Analyze and compare several:

```bash
python analyze.py AAPL MSFT COST
```

You can also pass a company name instead of a ticker (best-effort lookup):

```bash
python analyze.py "Garmin"
```

Useful flags:

```bash
python analyze.py AAPL --years 15          # ask for more history (capped by what Yahoo has)
python analyze.py AAPL --out-dir my_charts # where to save PNGs (default: rule1_output/)
python analyze.py AAPL --no-charts         # terminal report only, skip the PNGs
```

### What the CLI gives you

- A colored terminal report: the Big Five table (green ≥10%, red <10%), the
  debt payback verdict, the moat/castle rating, the full Sticker
  Price/MOS breakdown, and the source links.
- `<TICKER>_growth_chart.png` — a log-scale, multi-line chart of sales,
  EPS, equity, and free cash flow over time (same style as the book's
  example charts).
- `<TICKER>_debt_gauge.png` — a green→yellow→orange→red gauge showing the
  debt payback ratio.
- `<TICKER>_moat_levelN.png` — the castle graphic matching how many of the
  Big Five are green (5/5 green = Level 5, widest moat; 0–1/5 green =
  Level 1, narrowest).
- When you pass more than one ticker: a comparison table plus
  `comparison_<TICKERS>.png`, a side-by-side version of the growth charts.

## Project layout

```
rule1_calculator/
├── app.py                 # `python app.py` — the local dashboard (Flask)
├── analyze.py             # `python analyze.py TICKER [TICKER...]` — CLI
├── requirements.txt
├── dashboard_data.json    # offline fallback cache (8 tickers) if live fetch fails
├── templates/
│   └── index.html         # dashboard page (search bar + results panel)
├── static/
│   ├── app.css
│   └── app.js
├── assets/                # the five moat/castle graphics
│   └── level1.png ... level5.png
├── rule1/
│   ├── data.py            # scrapes/fetches statements via yfinance
│   ├── metrics.py         # pure-math: CAGR, ROIC, debt ratio, sticker price
│   ├── analysis.py        # wires data.py + metrics.py into one result
│   ├── report.py          # colored terminal output + matplotlib charts (also
│   │                       #   used by app.py to serve chart PNGs)
│   └── cli.py              # argument parsing / entry point for analyze.py
└── tests/
    ├── test_metrics.py       # math checked against the book's own numbers
    └── test_report_smoke.py  # full pipeline, run offline on book fixture data
```

## Running the tests

No network needed — the tests use numbers straight from the book (Apollo
Group, Harley-Davidson, H&R Block) as fixtures, so you can verify the math
is right before you ever hit a live API:

```bash
python tests/test_metrics.py
python tests/test_report_smoke.py
```

or, if you have pytest installed:

```bash
pytest tests/ -v
```

## Extending it

- **More years of history**: swap `rule1/data.py`'s Yahoo Finance calls for
  a paid data provider (or scrape a site like stockanalysis.com directly)
  if you want the full 10–15 years the book prefers.
- **Excel/CSV export**: `analysis.py`'s `AnalysisResult` has everything
  already computed — add a `csv.writer` call in `report.py` to dump it.
- **Screening many tickers at once**: loop over a watchlist and call
  `rule1.analysis.analyze()` for each; the terminal comparison table
  already handles an arbitrary number of results.
- **Refreshing the offline fallback cache**: `dashboard_data.json` (used by
  `app.py` only when a live fetch fails) was built by running
  `compute_dashboard_data.py` over `raw_scraped_data.json`. Edit the raw
  file and re-run the script to add tickers or update the cached figures.
