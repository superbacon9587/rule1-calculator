library(DBI)
library(RSQLite)
library(dplyr)
library(lubridate)
library(jsonlite)
library(TTR)

# ---------------------------------------------------------
# Connect to Rule #1 database
# ---------------------------------------------------------

db_path <- file.path("backend", "rule1.db")

con <- dbConnect(SQLite(), db_path)

print(dbListTables(con))

# ---------------------------------------------------------
# Check that the base tables exist
# ---------------------------------------------------------

required_tables <- c(
  "prices_daily",
  "fundamentals_annual",
  "fundamentals_quarterly",
  "analyst_growth",
  "dividends"
)

existing_tables <- dbListTables(con)

missing_tables <- setdiff(required_tables, existing_tables)

if (length(missing_tables) > 0) {
  stop(
    paste(
      "Missing required base tables:",
      paste(missing_tables, collapse = ", ")
    )
  )
}

cat("All required base tables are available.\n")

# ---------------------------------------------------------
# Load base tables
# ---------------------------------------------------------

prices_daily <- dbReadTable(con, "prices_daily")
fundamentals_annual <- dbReadTable(con, "fundamentals_annual")
fundamentals_quarterly <- dbReadTable(con, "fundamentals_quarterly")
analyst_growth <- dbReadTable(con, "analyst_growth")
dividends <- dbReadTable(con, "dividends")

cat("Base tables loaded successfully.\n")

cat("Prices:", nrow(prices_daily), "rows\n")
cat("Annual fundamentals:", nrow(fundamentals_annual), "rows\n")
cat("Quarterly fundamentals:", nrow(fundamentals_quarterly), "rows\n")
cat("Analyst growth:", nrow(analyst_growth), "rows\n")
cat("Dividends:", nrow(dividends), "rows\n")
# ---------------------------------------------------------
# Prepare daily price data
# ---------------------------------------------------------

prices_daily <- prices_daily %>%
  mutate(
    date = as.Date(date),
    ticker = as.character(ticker),
    close = as.numeric(close),
    high = as.numeric(high),
    low = as.numeric(low)
  ) %>%
  arrange(ticker, date)

cat("Price data prepared.\n")
cat("Tickers:", n_distinct(prices_daily$ticker), "\n")
cat("Date range:",
    format(min(prices_daily$date, na.rm = TRUE)),
    "to",
    format(max(prices_daily$date, na.rm = TRUE)),
    "\n")

    # ---------------------------------------------------------
# Rule #1 helper functions
# ---------------------------------------------------------

cagr <- function(start, end, years) {
  if (is.na(start) || is.na(end) || years <= 0) {
    return(NA_real_)
  }

  if (start <= 0) {
    return(0)
  }

  ratio <- end / start

  if (ratio <= 0) {
    return(0)
  }

  ratio^(1 / years) - 1
}
growth_rate_windows <- function(series, windows = c(10, 5, 3, 1)) {
  series <- series[!is.na(series)]
  
  if (length(series) == 0) {
    return(setNames(rep(NA_real_, length(windows)), windows))
  }
  
  years <- as.integer(names(series))
  years <- sort(years)
  last_year <- max(years)
  last_value <- series[as.character(last_year)]
  
  max_span <- last_year - min(years)
  
  results <- setNames(rep(NA_real_, length(windows)), windows)
  
  for (w in windows) {
    if (w > max_span + 1) {
      next
    }
    
    target_year <- last_year - w
    
    if (!(as.character(target_year) %in% names(series))) {
      available_years <- years[years <= target_year]
      
      if (length(available_years) == 0) {
        target_year <- min(years)
      } else {
        target_year <- max(available_years)
      }
    }
    
    start_value <- series[as.character(target_year)]
    actual_years <- last_year - target_year
    
    if (actual_years > 0) {
      results[as.character(w)] <- cagr(
        start_value,
        last_value,
        actual_years
      )
    }
  }
  
  results
}

compute_roic <- function(ebit, tax_rate, equity, debt) {
  if (is.na(ebit) || is.na(equity) || is.na(debt)) {
    return(NA_real_)
  }
  
  if (is.na(tax_rate)) {
    tax_rate <- 0.21
  }
  
  # Keep tax rate between 0% and 60%
  tax_rate <- min(max(tax_rate, 0), 0.60)
  
  nopat <- ebit * (1 - tax_rate)
  invested_capital <- equity + debt
  
  if (invested_capital <= 0) {
    return(NA_real_)
  }
  
  nopat / invested_capital
}

# Return the longest available growth rate
longest_growth <- function(growth_rates) {
  for (period in c("10", "5", "3", "1")) {
    value <- growth_rates[[period]]
    
    if (!is.null(value) && !is.na(value)) {
      return(value)
    }
  }
  
  NA_real_
}

# Big Five number is green at 10% or higher
is_green <- function(rate, threshold = 0.10) {
  if (is.na(rate)) {
    return(NA)
  }
  
  rate >= threshold
}

# Calculate the Big Five metrics for historical data

calculate_big_five <- function(annual_data) {
  
  # Create year-named series for each Big Five metric
  sales_series <- annual_data$sales
  names(sales_series) <- annual_data$fyear
  
  eps_series <- annual_data$eps
  names(eps_series) <- annual_data$fyear
  
  equity_series <- annual_data$equity
  names(equity_series) <- annual_data$fyear
  
  fcf_series <- annual_data$fcf
  names(fcf_series) <- annual_data$fyear
  
  # Calculate growth rates
  sales_growth <- growth_rate_windows(sales_series)
  eps_growth <- growth_rate_windows(eps_series)
  equity_growth <- growth_rate_windows(equity_series)
  fcf_growth <- growth_rate_windows(fcf_series)
  
  # Calculate average ROIC
  roic_values <- annual_data$roic[!is.na(annual_data$roic)]
  
  roic_average <- if (length(roic_values) > 0) {
    mean(roic_values)
  } else {
    NA_real_
  }
  
  # Determine which Big Five metrics are green
  green_flags <- c(
    sales = is_green(sales_growth["10"]),
    eps = is_green(eps_growth["10"]),
    equity = is_green(equity_growth["10"]),
    fcf = is_green(fcf_growth["10"]),
    roic = is_green(roic_average)
  )
  
  green_count <- sum(green_flags, na.rm = TRUE)
  
  list(
    sales_growth = sales_growth,
    eps_growth = eps_growth,
    equity_growth = equity_growth,
    fcf_growth = fcf_growth,
    roic = roic_average,
    green_flags = green_flags,
    green_count = green_count
  )
}

# Determine moat level from Big Five green count
assess_moat <- function(green_count) {
  
  if (green_count <= 1) {
    return(1)
  } else if (green_count == 2) {
    return(2)
  } else if (green_count == 3) {
    return(3)
  } else if (green_count == 4) {
    return(4)
  } else {
    return(5)
  }
}

# Tickers whose book value per share is shrunk by heavy share buybacks, so
# historical equity growth understates how fast the business is growing
# (near zero or negative). For these the analyst estimate is used on its
# own instead of the lower-of rule; every other ticker keeps the lower-of
# rule. Same list and source label as rule1/metrics.py.
BUYBACK_DISTORTED_TICKERS <- c("AAPL", "KO", "WMT", "JNJ", "PG", "XOM", "CSCO")
BUYBACK_DISTORTED_SOURCE <-
  "analyst 5-year estimate (equity growth distorted by buybacks)"

# Choose the Rule #1 growth rate
pick_rule1_growth_rate <- function(
    historical_equity_growth,
    analyst_growth_estimate,
    fallback_eps_growth = NA_real_,
    ticker = NA_character_
) 
{
  
  # Without an analyst estimate, the usual rule below applies
  if (!is.na(ticker) &&
      toupper(trimws(ticker)) %in% BUYBACK_DISTORTED_TICKERS &&
      !is.na(analyst_growth_estimate)) {
    return(list(
      growth_rate = analyst_growth_estimate,
      source = BUYBACK_DISTORTED_SOURCE
    ))
  }
  
  candidates <- c()
  labels <- c()
  
  if (!is.na(historical_equity_growth)) {
    candidates <- c(candidates, historical_equity_growth)
    labels <- c(labels, "historical equity growth")
  }
  
  if (!is.na(analyst_growth_estimate)) {
    candidates <- c(candidates, analyst_growth_estimate)
    labels <- c(labels, "analyst 5-year estimate")
  }
  
  if (length(candidates) == 0 && !is.na(fallback_eps_growth)) {
    candidates <- c(fallback_eps_growth)
    labels <- c(labels, "historical EPS growth (fallback)")
  }
  
  if (length(candidates) == 0) {
    return(list(
      growth_rate = NA_real_,
      source = "no data available"
    ))
  }
  
  if (length(candidates) == 1) {
    return(list(
      growth_rate = candidates[1],
      source = labels[1]
    ))
  }
  
  index <- which.min(candidates)
  
  list(
    growth_rate = candidates[index],
    source = paste(
      "lower of historical equity growth & analyst estimate",
      paste0("(", labels[index], ")")
    )
  )
}

# Hysteresis state machine for the Stochastic buy/sell regime (Ch. 12):
# starts out of regime, flips to "buy" on an upward cross through 20,
# stays "buy" until a downward cross through 80 flips it to "sell", and
# so on. Mirrors the book's own Starbucks example, where the three
# Tools cross on different days and the trade enters once all three are
# simultaneously in their buy regime, not on one shared crossing day.
compute_stoch_regime <- function(stoch_slow) {
  n <- length(stoch_slow)
  regime <- logical(n)
  current <- FALSE

  for (i in seq_len(n)) {
    if (i > 1 && !is.na(stoch_slow[i]) && !is.na(stoch_slow[i - 1])) {
      if (stoch_slow[i] > 20 && stoch_slow[i - 1] <= 20) current <- TRUE
      if (stoch_slow[i] < 80 && stoch_slow[i - 1] >= 80) current <- FALSE
    }
    regime[i] <- current
  }

  regime
}

# Calculate technical buy signals
calculate_technical_signals <- function(prices) {
  
  prices <- prices %>%
    arrange(date)
  
  close_prices <- prices$close
  
  # MACD: 8-day / 17-day EMA with 9-day signal
  macd_result <- TTR::MACD(
    close_prices,
    nFast = 8,
    nSlow = 17,
    nSig = 9,
    maType = "EMA"
  )
  
  prices$macd <- macd_result[, "macd"]
  prices$macd_signal <- macd_result[, "signal"]
  
  # Buy regime: MACD is currently above its signal line. This persists
  # for as long as the condition holds, not just the single day it
  # crosses up, since Town's rule is "all three Tools agree buy as of
  # that day," a state, not a same-day crossing event (Ch. 12).
  prices$macd_buy <- prices$macd > prices$macd_signal
  
  # Stochastics: 14-day %K with 5-day slow average
  # TTR::stoch stops with "non-leading NAs" when high/low are missing
  # mid-series, so those days borrow the close here. They still count as
  # 2-of-3 days below because high_low_available reads the original columns.
  stoch_input <- data.frame(
    high = dplyr::coalesce(prices$high, prices$close),
    low = dplyr::coalesce(prices$low, prices$close),
    close = prices$close
  )
  
  stoch_result <- TTR::stoch(
    stoch_input,
    nFastK = 14,
    nFastD = 5,
    nSlowD = 5
  )
  
  # TTR returns stochastics on a 0-1 scale; the rule below uses 0-100
  prices$stoch_k <- stoch_result[, "fastK"] * 100
  prices$stoch_slow <- stoch_result[, "slowD"] * 100
  
  # Buy regime: enters on an upward cross through 20 (coming out of
  # oversold) and persists until a downward cross through 80 (coming out
  # of overbought) flips it to a sell regime, per Town's own rule (Ch.
  # 12). This needs a running state, not a single-day comparison, since
  # the two thresholds are different (20 to enter, 80 to exit).
  prices$stoch_buy <- compute_stoch_regime(prices$stoch_slow)
  
  # 10-day moving average
  prices$moving_average <- zoo::rollmean(
    prices$close,
    k = 10,
    fill = NA,
    align = "right"
  )
  
  # Buy regime: price is currently above its moving average, persisting
  # until it crosses back below (same state-vs-event reasoning as MACD).
  prices$ma_buy <- prices$close > prices$moving_average
  
  # Determine which tools can be used
  prices <- prices %>%
    mutate(
      high_low_available = !is.na(high) & !is.na(low),
      
      tools_status = case_when(
        !high_low_available ~ "2-of-3 Tools",
        macd_buy & stoch_buy & ma_buy ~ "3-of-3 Tools",
        TRUE ~ "Not Buy"
      ),
      
      technical_buy = case_when(
        !high_low_available ~ macd_buy & ma_buy,
        high_low_available ~ macd_buy & stoch_buy & ma_buy,
        TRUE ~ FALSE
      )
    )
  
  prices
}

# Calculate Sticker Price and Margin of Safety
compute_sticker_price <- function(
    current_eps,
    historical_equity_growth,
    analyst_growth_estimate,
    historical_avg_pe,
    current_price = NA_real_,
    fallback_eps_growth = NA_real_,
    ticker = NA_character_
) {
  
  growth_result <- pick_rule1_growth_rate(
    historical_equity_growth,
    analyst_growth_estimate,
    fallback_eps_growth,
    ticker
  )
  
  growth_rate <- growth_result$growth_rate
  source <- growth_result$source
  
  # Need positive EPS and a usable growth rate
  if (is.na(current_eps) ||
      current_eps <= 0 ||
      is.na(growth_rate)) {
    
    return(list(
      growth_rate = growth_rate,
      growth_source = source,
      rule1_pe = NA_real_,
      future_eps = NA_real_,
      future_price = NA_real_,
      sticker_price = NA_real_,
      mos_price = NA_real_
    ))
  }
  
  # Cap growth between 0% and 60%
  growth_rate <- max(growth_rate, 0)
  growth_rate <- min(growth_rate, 0.60)
  
  # Rule #1 default P/E
  default_pe <- growth_rate * 100 * 2
  
  # Use the lower of default P/E and historical average P/E
  if (!is.na(historical_avg_pe) && historical_avg_pe > 0) {
    rule1_pe <- min(default_pe, historical_avg_pe)
  } else {
    rule1_pe <- default_pe
  }
  
  # Minimum P/E of 5
  rule1_pe <- max(rule1_pe, 5)
  
  # Project EPS 10 years into the future
  future_eps <- current_eps * (1 + growth_rate)^10
  
  # Project future stock price
  future_price <- future_eps * rule1_pe
  
  # Discount future price at 15% MARR
  sticker_price <- future_price / (1 + 0.15)^10
  
  # Margin of Safety = half of Sticker Price
  mos_price <- sticker_price / 2
  
  list(
    growth_rate = growth_rate,
    growth_source = source,
    rule1_pe = rule1_pe,
    future_eps = future_eps,
    future_price = future_price,
    sticker_price = sticker_price,
    mos_price = mos_price
  )
}

# Calculate historical average P/E up to a specific date
calculate_historical_pe <- function(
    prices,
    annual_eps,
    as_of_date
) 
{
  
  prices <- prices %>%
    filter(
      date <= as_of_date,
      !is.na(close)
    ) %>%
    mutate(
      year = year(date)
    )
  
  if (nrow(prices) == 0 || length(annual_eps) == 0) {
    return(NA_real_)
  }
  
  # Use the last available close of each month
  monthly_prices <- prices %>%
    group_by(year, month = month(date)) %>%
    slice_max(date, n = 1, with_ties = FALSE) %>%
    ungroup()
  
  monthly_prices <- monthly_prices %>%
    mutate(
      eps = annual_eps[as.character(year)],
      pe = ifelse(
        !is.na(eps) & eps > 0,
        close / eps,
        NA_real_
      )
    ) %>%
    filter(!is.na(pe))
  
  if (nrow(monthly_prices) == 0) {
    return(NA_real_)
  }
  
  mean(monthly_prices$pe)
}

# Filter annual fundamentals to information known by the signal date
get_historical_fundamentals <- function(
    fundamentals,
    ticker_value,
    as_of_date
) {
  
  fundamentals %>%
    filter(
      ticker == ticker_value,
      as.Date(known_from) <= as.Date(as_of_date)
    ) %>%
    arrange(fyear, known_from)
}

# Keep the latest known record for each fiscal year
latest_known_by_year <- function(data) {
  
  data %>%
    arrange(fyear, known_from) %>%
    group_by(fyear) %>%
    slice_tail(n = 1) %>%
    ungroup()
}

prepare_annual_fundamentals <- function(data) {
  
  data %>%
    mutate(
      fyear = as.integer(fyear),
      sales = as.numeric(sale),
      eps = as.numeric(eps_diluted),
      equity = as.numeric(ceq),
      fcf = as.numeric(oancf) - as.numeric(capx),
      debt = coalesce(as.numeric(dltt), 0) + coalesce(as.numeric(dlc), 0),
      ebit = as.numeric(ebit),
      tax_rate = ifelse(
        !is.na(txt) & !is.na(ni) & ni != 0,
        txt / ni,
        NA_real_
      )
    ) %>%
    mutate(
      roic = mapply(
        compute_roic,
        ebit,
        tax_rate,
        equity,
        debt
      )
    )
}

# Mean estimate (meanest), the same figure rule1/db_data.py reads
get_analyst_growth <- function(data, ticker_value, as_of_date) {
  
  data %>%
    filter(
      ticker == ticker_value,
      as.Date(known_from) <= as.Date(as_of_date),
      !is.na(meanest)
    ) %>%
    arrange(statpers, known_from) %>%
    slice_tail(n = 1) %>%
    pull(meanest) %>%
    { if (length(.) == 0) NA_real_ else as.numeric(.) }
}

get_current_eps <- function(data, ticker_value, as_of_date) {
  
  result <- data %>%
    filter(
      ticker == ticker_value,
      as.Date(known_from) <= as.Date(as_of_date),
      !is.na(eps_ttm)
    ) %>%
    arrange(fyearq, fqtr, known_from) %>%
    slice_tail(n = 1) %>%
    pull(eps_ttm)
  
  if (length(result) == 0) {
    return(NA_real_)
  }
  
  as.numeric(result)
}







calculate_rule1_signal <- function(ticker_value, annual_data, quarterly_data,
                                   prices, analyst_growth_rate, as_of_date) {
  
  big_five <- calculate_big_five(annual_data)
  moat_level <- assess_moat(big_five$green_count)
  
  current_eps <- get_current_eps(
    quarterly_data,
    ticker_value = ticker_value,
    as_of_date = as_of_date
  )
  
  if (is.na(current_eps) || current_eps <= 0) {
    return(NULL)
  }
  
  annual_eps <- annual_data$eps
  names(annual_eps) <- annual_data$fyear
  
  historical_pe <- calculate_historical_pe(
    prices = prices,
    annual_eps = annual_eps,
    as_of_date = as_of_date
  )
  
  sticker <- compute_sticker_price(
    current_eps = current_eps,
    historical_equity_growth = big_five$equity_growth["10"],
    analyst_growth_estimate = analyst_growth_rate,
    fallback_eps_growth = big_five$eps_growth["10"],
    historical_avg_pe = historical_pe,
    ticker = ticker_value
  )
  
  list(
    big_five = big_five,
    moat_level = moat_level,
    current_eps = current_eps,
    sticker_price = sticker$sticker_price,
    mos_price = sticker$mos_price,
    growth_rate = sticker$growth_rate,
    historical_pe = historical_pe
  )
}
dbExecute(con, "
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
")

# RSQLite runs only the first statement of a multi-statement string,
# so each table gets its own dbExecute()
dbExecute(con, "
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
")

is_buy_condition <- function(price, mos_price, technical_buy) {
  !is.na(price) &&
    !is.na(mos_price) &&
    price <= mos_price &&
    isTRUE(technical_buy)
}

build_buy_windows <- function(signals) {
  signals <- signals %>%
    arrange(date) %>%
    mutate(
      buy_condition = mapply(
        is_buy_condition,
        price,
        mos_price,
        technical_buy
      ),
      is_buy_window = as.integer(buy_condition)
    )

  signals
}

# ---------------------------------------------------------
# Backtest orchestration
# ---------------------------------------------------------

# Holding periods measured for every buy window, in years
HORIZONS <- c(1, 3, 5, 10)

# Fiscal years of history behind each Big Five calculation
# (11 rows = a 10-year growth window, same as rule1/db_data.py)
HISTORY_YEARS <- 10

# Everything per-share is put on today's share basis so prices and EPS
# from either side of a stock split can be compared. This only rescales
# units; it uses no information from after the as-of date.
#   prices: adj_close (= close / cfacpr), high / cfacpr, low / cfacpr
#   annual EPS: eps_diluted / ajex
#   TTM EPS: eps_ttm / cfacpr at the fiscal quarter end (no ajexq in the db)
adjusted_prices <- prices_daily %>%
  transmute(
    ticker,
    date,
    close = as.numeric(adj_close),
    high = high / as.numeric(cfacpr),
    low = low / as.numeric(cfacpr),
    cfacpr = as.numeric(cfacpr)
  )

annual_prepared <- fundamentals_annual %>%
  mutate(eps_diluted = as.numeric(eps_diluted) / as.numeric(ajex)) %>%
  prepare_annual_fundamentals()

# Latest row on or before each date, in a frame sorted by date
rows_on_or_before <- function(sorted_dates, dates) {
  findInterval(as.numeric(as.Date(dates)), as.numeric(sorted_dates))
}

# eps_ttm is on the share basis of its fiscal quarter end, which is not a
# column in the db. Annual known_from is the fiscal year end plus 4 months
# for every row, so the quarter end is counted back from that.
prepare_ticker_quarterly <- function(ticker_value, ticker_prices) {
  quarterly <- fundamentals_quarterly %>%
    filter(ticker == ticker_value)
  
  latest_annual <- fundamentals_annual %>%
    filter(ticker == ticker_value) %>%
    slice_max(fyear, n = 1, with_ties = FALSE)
  
  annual_known_from <- as.Date(latest_annual$known_from) %m+%
    years(quarterly$fyearq - latest_annual$fyear)
  
  quarter_end <- annual_known_from %m-% months(4 + 3 * (4 - quarterly$fqtr))
  quarter_end <- ceiling_date(quarter_end, "month") - days(1)
  
  index <- pmax(rows_on_or_before(ticker_prices$date, quarter_end), 1)
  
  quarterly %>%
    mutate(eps_ttm = as.numeric(eps_ttm) / ticker_prices$cfacpr[index])
}

# Annual fundamentals known as of a date, latest HISTORY_YEARS + 1 fiscal years
known_annual_data <- function(ticker_value, as_of_date) {
  get_historical_fundamentals(annual_prepared, ticker_value, as_of_date) %>%
    latest_known_by_year() %>%
    slice_tail(n = HISTORY_YEARS + 1)
}

# The annual snapshot only changes on a known_from date, so the moat is
# memoized by ticker plus the latest known_from on or before the as-of date
annual_known_dates <- lapply(
  split(as.Date(annual_prepared$known_from), annual_prepared$ticker),
  function(dates) sort(unique(dates))
)
moat_cache <- new.env()

moat_as_of <- function(ticker_value, as_of_date) {
  known_dates <- annual_known_dates[[ticker_value]]
  
  if (is.null(known_dates)) {
    return(NA_integer_)
  }
  
  index <- rows_on_or_before(known_dates, as_of_date)
  
  if (index == 0) {
    return(NA_integer_)
  }
  
  key <- paste(ticker_value, index)
  
  if (is.null(moat_cache[[key]])) {
    annual_data <- known_annual_data(ticker_value, as_of_date)
    moat_cache[[key]] <- as.integer(
      assess_moat(calculate_big_five(annual_data)$green_count)
    )
  }
  
  moat_cache[[key]]
}

# 1 only if the moat never fell below its signal-date level at any yearly
# checkpoint of the hold (signal date + 1, 2, ... years, and the target
# date). Checkpoints with no moat are skipped; NA when none has one.
moat_held_through <- function(ticker_value, signal_date, horizon, target_date,
                              signal_moat) {
  checkpoints <- unique(c(signal_date %m+% years(seq_len(horizon)), target_date))
  moats <- sapply(seq_along(checkpoints), function(i) {
    moat_as_of(ticker_value, checkpoints[i])
  })
  moats <- moats[!is.na(moats)]
  
  if (length(moats) == 0) {
    return(NA_integer_)
  }
  
  as.integer(all(moats >= signal_moat))
}

# calculate_rule1_signal() on what was known as of a date.
# NULL when there are no fundamentals yet or TTM EPS is not positive.
rule1_signal_as_of <- function(ticker_value, ticker_prices, ticker_quarterly,
                               as_of_date) {
  annual_data <- known_annual_data(ticker_value, as_of_date)
  
  if (nrow(annual_data) == 0) {
    return(NULL)
  }
  
  # analyst_growth.meanest is in percent (16.2 = 16.2%)
  analyst_rate <- get_analyst_growth(analyst_growth, ticker_value, as_of_date) / 100
  
  calculate_rule1_signal(
    ticker_value = ticker_value,
    annual_data = annual_data,
    quarterly_data = ticker_quarterly,
    prices = ticker_prices,
    analyst_growth_rate = analyst_rate,
    as_of_date = as_of_date
  )
}

big_five_to_json <- function(big_five) {
  as.character(toJSON(
    list(
      sales = unname(big_five$sales_growth["10"]),
      eps = unname(big_five$eps_growth["10"]),
      equity = unname(big_five$equity_growth["10"]),
      fcf = unname(big_five$fcf_growth["10"]),
      roic = big_five$roic,
      green_count = big_five$green_count
    ),
    auto_unbox = TRUE,
    na = "null",
    digits = 6
  ))
}

tools_status_to_json <- function(day) {
  tool_state <- function(flag) if (isTRUE(flag)) "buy" else "no signal"
  
  as.character(toJSON(
    list(
      tools_status = day$tools_status,
      macd = tool_state(day$macd_buy),
      stochastic = if (day$high_low_available) {
        tool_state(day$stoch_buy)
      } else {
        "unavailable (no high/low)"
      },
      ma = tool_state(day$ma_buy)
    ),
    auto_unbox = TRUE
  ))
}

signal_row <- function(ticker_value, day, signal, is_buy_window) {
  has_signal <- !is.null(signal)
  
  data.frame(
    ticker = ticker_value,
    as_of_date = format(day$date),
    big_five_json = if (has_signal) big_five_to_json(signal$big_five) else NA_character_,
    moat_level = if (has_signal) {
      as.integer(signal$moat_level)
    } else {
      moat_as_of(ticker_value, day$date)
    },
    sticker_price = if (has_signal) unname(signal$sticker_price) else NA_real_,
    mos_price = if (has_signal) unname(signal$mos_price) else NA_real_,
    price_at_signal = day$price,
    tools_status_json = tools_status_to_json(day),
    is_buy_window = as.integer(is_buy_window),
    stringsAsFactors = FALSE
  )
}

# Buy-and-hold of every ticker trading on the signal date, equal weights,
# over the same dates as the signal (no timing)
benchmark_return_between <- function(start_date, end_date) {
  returns <- sapply(split(adjusted_prices, adjusted_prices$ticker), function(p) {
    start_index <- rows_on_or_before(p$date, start_date)
    end_index <- rows_on_or_before(p$date, end_date)
    
    if (start_index == 0 || end_index <= start_index) {
      return(NA_real_)
    }
    
    p$close[end_index] / p$close[start_index] - 1
  })
  
  if (all(is.na(returns))) {
    return(NA_real_)
  }
  
  mean(returns, na.rm = TRUE)
}

# One row per horizon in HORIZONS for a buy window, each measured over
# its own holding period
outcome_row <- function(ticker_value, ticker_prices, signal_date, signal) {
  last_date <- max(ticker_prices$date)
  
  # The growth rate the Sticker Price was built on (after the 0-60% clamp)
  growth_rate <- unname(signal$growth_rate)
  sticker_price <- unname(signal$sticker_price)
  
  rows <- list()
  
  for (horizon in HORIZONS) {
    full_target <- signal_date %m+% years(horizon)
    
    # A horizon is only written when the whole hold fits inside the price
    # data. A signal too recent for it gets no row for that horizon, so
    # horizon_years is always the real holding period and can never collide
    # on (ticker, signal_date, horizon_years) with a shorter horizon's row.
    if (full_target > last_date) {
      next
    }
    
    holding <- ticker_prices %>%
      filter(date >= signal_date, date <= full_target)
    
    if (nrow(holding) < 2) {
      next
    }
    
    target_date <- max(holding$date)
    
    price_at_signal <- holding$close[1]
    realized_price <- holding$close[nrow(holding)]
    realized_return <- realized_price / price_at_signal - 1
    daily_returns <- diff(holding$close) / head(holding$close, -1)
    
    moat_held_up <- moat_held_through(
      ticker_value, signal_date, horizon, target_date, signal$moat_level
    )
    benchmark_return <- benchmark_return_between(signal_date, target_date)
    
    rows[[length(rows) + 1]] <- data.frame(
      ticker = ticker_value,
      signal_date = format(signal_date),
      horizon_years = as.integer(horizon),
      target_date = format(target_date),
      realized_price = realized_price,
      realized_return = realized_return,
      projected_return = (1 + growth_rate)^horizon - 1,
      moat_held_up = moat_held_up,
      price_target_hit = as.integer(max(holding$close[-1]) >= sticker_price),
      is_profitable = as.integer(realized_return > 0),
      max_drawdown = min(holding$close / cummax(holding$close) - 1),
      volatility = sd(daily_returns) * sqrt(252),
      benchmark_return = benchmark_return,
      benchmark_delta = realized_return - benchmark_return,
      stringsAsFactors = FALSE
    )
  }
  
  bind_rows(rows)
}

run_ticker_backtest <- function(ticker_value) {
  ticker_prices <- adjusted_prices %>%
    filter(ticker == ticker_value, !is.na(close)) %>%
    arrange(date)
  
  ticker_quarterly <- prepare_ticker_quarterly(ticker_value, ticker_prices)
  
  daily <- calculate_technical_signals(ticker_prices) %>%
    mutate(price = close, mos_price = NA_real_)
  
  # A day can only be a buy day when technical_buy is TRUE, so the
  # Margin-of-Safety price is only worked out for those days
  snapshots <- list()
  
  for (i in which(daily$technical_buy %in% TRUE)) {
    signal <- rule1_signal_as_of(
      ticker_value, ticker_prices, ticker_quarterly, daily$date[i]
    )
    
    if (!is.null(signal)) {
      snapshots[[format(daily$date[i])]] <- signal
      daily$mos_price[i] <- unname(signal$mos_price)
    }
  }
  
  daily <- build_buy_windows(daily)
  
  # A window opens on the first day of a run of buy days and closes on
  # the first day after it that is not a buy day
  in_window <- daily$is_buy_window == 1
  was_in_window <- dplyr::lag(in_window, default = FALSE)
  open_rows <- which(in_window & !was_in_window)
  close_rows <- which(!in_window & was_in_window)
  
  signal_rows <- list()
  outcome_rows <- list()
  
  for (i in open_rows) {
    signal <- snapshots[[format(daily$date[i])]]
    
    signal_rows[[length(signal_rows) + 1]] <- signal_row(
      ticker_value, daily[i, ], signal, TRUE
    )
    outcome_rows[[length(outcome_rows) + 1]] <- outcome_row(
      ticker_value, ticker_prices, daily$date[i], signal
    )
  }
  
  # One is_buy_window = 0 row on each closing day. rule1/backtest.py
  # counts a new window only after a row that is not flagged, so without
  # these every window of a ticker would read as a single window.
  for (i in close_rows) {
    signal <- rule1_signal_as_of(
      ticker_value, ticker_prices, ticker_quarterly, daily$date[i]
    )
    
    signal_rows[[length(signal_rows) + 1]] <- signal_row(
      ticker_value, daily[i, ], signal, FALSE
    )
  }
  
  list(
    signals = bind_rows(signal_rows),
    outcomes = bind_rows(outcome_rows),
    windows = length(open_rows),
    technical_buy_days = sum(daily$technical_buy, na.rm = TRUE),
    two_of_three_windows = sum(!daily$high_low_available[open_rows])
  )
}

# Replace a ticker's rows in one transaction so a rerun never leaves
# half-written or duplicate rows
write_ticker_backtest <- function(ticker_value, result) {
  dbWithTransaction(con, {
    dbExecute(con, "DELETE FROM backtest_signals WHERE ticker = ?",
              params = list(ticker_value))
    dbExecute(con, "DELETE FROM backtest_outcomes WHERE ticker = ?",
              params = list(ticker_value))
    
    if (nrow(result$signals) > 0) {
      dbWriteTable(con, "backtest_signals", result$signals, append = TRUE)
    }
    
    if (nrow(result$outcomes) > 0) {
      dbWriteTable(con, "backtest_outcomes", result$outcomes, append = TRUE)
    }
  })
}

tickers <- sort(unique(prices_daily$ticker))
run_summary <- list()

for (ticker_value in tickers) {
  cat("Backtesting", ticker_value, "...\n")
  
  # An error in one ticker is recorded and reported, not fatal to the run
  run_summary[[ticker_value]] <- tryCatch({
    result <- run_ticker_backtest(ticker_value)
    write_ticker_backtest(ticker_value, result)
    
    data.frame(
      ticker = ticker_value,
      status = "ok",
      technical_buy_days = result$technical_buy_days,
      buy_windows = result$windows,
      two_of_three_windows = result$two_of_three_windows,
      outcome_rows = nrow(result$outcomes),
      error = NA_character_,
      stringsAsFactors = FALSE
    )
  }, error = function(e) {
    data.frame(
      ticker = ticker_value,
      status = "ERROR",
      technical_buy_days = NA_integer_,
      buy_windows = NA_integer_,
      two_of_three_windows = NA_integer_,
      outcome_rows = NA_integer_,
      error = conditionMessage(e),
      stringsAsFactors = FALSE
    )
  })
}

run_summary <- bind_rows(run_summary)

cat("\nBacktest summary:\n")
print(run_summary, row.names = FALSE)

failed_tickers <- run_summary$ticker[run_summary$status == "ERROR"]

if (length(failed_tickers) > 0) {
  cat(
    "\nWARNING: these tickers failed and wrote no rows:",
    paste(failed_tickers, collapse = ", "),
    "\n"
  )
} else {
  cat("\nAll", nrow(run_summary), "tickers completed without errors.\n")
}

dbDisconnect(con)
