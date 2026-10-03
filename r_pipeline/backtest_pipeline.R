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
    min(prices_daily$date, na.rm = TRUE),
    "to",
    max(prices_daily$date, na.rm = TRUE),
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

# Choose the Rule #1 growth rate
pick_rule1_growth_rate <- function(
    historical_equity_growth,
    analyst_growth_estimate,
    fallback_eps_growth = NA_real_
) 
{
  
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
  
  # Buy when MACD crosses above its signal line
  prices$macd_buy <- prices$macd > prices$macd_signal &
    dplyr::lag(prices$macd) <= dplyr::lag(prices$macd_signal)
  
  # Stochastics: 14-day %K with 5-day slow average
  stoch_result <- TTR::stoch(
    prices[, c("high", "low", "close")],
    nFastK = 14,
    nFastD = 5,
    nSlowD = 5
  )
  
  prices$stoch_k <- stoch_result[, "fastK"]
  prices$stoch_slow <- stoch_result[, "slowD"]
  
  # Buy when stochastic crosses upward through 20
  prices$stoch_buy <- prices$stoch_slow > 20 &
    dplyr::lag(prices$stoch_slow) <= 20
  
  # 10-day moving average
  prices$moving_average <- zoo::rollmean(
    prices$close,
    k = 10,
    fill = NA,
    align = "right"
  )
  
  # Buy when price crosses above moving average
  prices$ma_buy <- prices$close > prices$moving_average &
    dplyr::lag(prices$close) <= dplyr::lag(prices$moving_average)
  
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
    fallback_eps_growth = NA_real_
) {
  
  growth_result <- pick_rule1_growth_rate(
    historical_equity_growth,
    analyst_growth_estimate,
    fallback_eps_growth
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

get_analyst_growth <- function(data, ticker_value, as_of_date) {
  
  data %>%
    filter(
      ticker == ticker_value,
      as.Date(known_from) <= as.Date(as_of_date)
    ) %>%
    arrange(statpers, known_from) %>%
    slice_tail(n = 1) %>%
    pull(medest) %>%
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







calculate_rule1_signal <- function(annual_data, quarterly_data, prices,
                                   analyst_growth_rate, as_of_date) {
  
  big_five <- calculate_big_five(annual_data)
  moat_level <- assess_moat(big_five$green_count)
  
  current_eps <- get_current_eps(
    quarterly_data,
    ticker_value = unique(quarterly_data$ticker)[1],
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
    historical_avg_pe = historical_pe
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
