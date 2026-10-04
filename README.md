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

Pick one of the supported tickers in the search bar and it computes
everything from the local `rule1.db` database. The app supports exactly
these 12 tickers: **AAPL, MSFT, KO, JNJ, WMT, PG, XOM, HD, INTC, CSCO, HOG,
TSM**. Any other ticker gets a friendly "not supported" message. The app
never contacts Yahoo Finance or any other data site, so it keeps working
without live scraping. There's also a plain command-line version if you'd
rather not open a browser.

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

## Data source

All figures come from `backend/rule1.db`, a local SQLite database that
`backend/build_rule1_db.py` builds from a WRDS dump (Compustat fundamentals,
CRSP daily prices, I/B/E/S long-term growth estimates) for the 12 supported
tickers, with 10 fiscal years of history. Nothing is fetched at run time.

"Current price" is the last close recorded in `rule1.db`, not a live quote;
the dashboard's warning banner shows that date for each ticker. The
docstring at the top of `backend/rule1/db_data.py` documents which database
column feeds each number.

`rule1.db` is not committed to git (see `.gitignore`), so a fresh clone
needs it built or copied into `backend/` before the app can show anything.

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
search bar (or click one of the 12 ticker chips) and it computes
and displays everything: the Big Five stat tiles (click any one to chart
that metric's raw values by year, bar-chart style), a **Leadership** card
(the "Management" M — current officers and directors from Wikidata, corrected by the
hand-maintained `backend/data/officer_overrides.csv`, loaded into
`rule1.db` by `python scripts/build_officers.py`; `--from-csv` reloads them from
`backend/data/company_officers.csv` without the network), the growth trends chart
(sales/EPS/equity/free cash flow indexed to their first year so very
different units compare cleanly on one log-scale axis), the debt-payback
gauge, the full Sticker Price walk-through as a table, and — last — the
moat castle with the Big Five ratio shown above it.

This is a plain local Flask app — `python app.py` starts a server only your
own browser can reach, and closing the terminal (or hitting Ctrl+C) stops
it. Nothing is deployed, hosted, or made reachable from outside your
machine.

## Usage — the command line (optional)

The same calculation engine is also available as a terminal tool, if you'd
rather not open a browser:

Analyze one company:

```bash
python analyze.py AAPL
```

Analyze and compare several:

```bash
python analyze.py AAPL MSFT KO
```

Useful flags:

```bash
python analyze.py AAPL --years 5           # read fewer years of history (default 10)
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
├── requirements.txt
├── Procfile
├── backend/
│   ├── app.py                 # `python app.py` — the local dashboard (Flask)
│   ├── analyze.py             # `python analyze.py TICKER [TICKER...]` — CLI
│   ├── build_rule1_db.py      # builds rule1.db from the WRDS dump
│   ├── rule1.db               # local database (not in git)
│   ├── rule1/
│   │   ├── data.py            # the CompanyData container
│   │   ├── db_data.py         # loads CompanyData from rule1.db; SUPPORTED_TICKERS
│   │   ├── metrics.py         # pure-math: CAGR, ROIC, debt ratio, sticker price
│   │   ├── analysis.py        # wires db_data.py + metrics.py into one result
│   │   ├── backtest.py        # reads the backtest tables from rule1.db
│   │   ├── report.py          # colored terminal output + matplotlib charts (also
│   │   │                       #   used by app.py to serve chart PNGs)
│   │   └── cli.py             # argument parsing / entry point for analyze.py
│   ├── scripts/
│   └── tests/
└── frontend/
    ├── templates/index.html   # dashboard page (ticker picker + results panel)
    ├── static/                # app.css, app.js
    └── assets/                # the five moat/castle graphics
```

## Running the tests

No network needed — the tests use numbers straight from the book (Apollo
Group, Harley-Davidson, H&R Block) as fixtures, so you can verify the math
is right without the real database (run from `backend/`):

```bash
python tests/test_metrics.py
python tests/test_report_smoke.py
python tests/test_db_data.py
python tests/test_backtest.py
```

or, if you have pytest installed:

```bash
pytest tests/ -v
```

## Extending it

- **More tickers or more history**: add them to the WRDS dump, rebuild with
  `build_rule1_db.py`, and add the ticker to `SUPPORTED_TICKERS` in
  `rule1/db_data.py`.
- **Excel/CSV export**: `analysis.py`'s `AnalysisResult` has everything
  already computed — add a `csv.writer` call in `report.py` to dump it.
- **Screening many tickers at once**: loop over a watchlist and call
  `rule1.analysis.analyze()` for each; the terminal comparison table
  already handles an arbitrary number of results.
