# Minervini Method Stock Screener — Technical Specification
**Single-Ticker Analysis via Financial Modeling Prep API**
Version 1.0 | March 2026

---

## 1. Overview

This document specifies the design and implementation of a single-ticker stock screener that evaluates whether a given equity passes Mark Minervini's selection criteria, as described in *Trade Like a Stock Market Wizard*. The system accepts a stock ticker as input, fetches all required data from the Financial Modeling Prep (FMP) API, runs the Trend Template and fundamental checks, and returns a structured pass/fail result with per-criterion detail.

### 1.1 Goals

- Given a single ticker symbol, determine pass/fail status against all Minervini criteria.
- Fetch all required data from FMP using the minimum necessary API calls.
- Return per-criterion results so callers can inspect which checks failed.
- Keep the implementation stateless — no caching or persistence layer in v1.

### 1.2 Out of Scope

- Universe-wide screening across all tickers (v2 concern).
- Relative Strength percentile ranking (requires a comparative universe).
- Real-time / intraday data.
- Portfolio construction or position sizing.

---

## 2. Architecture

The system is a single Python module with three layers:

| Layer | Responsibility | Key Components |
|---|---|---|
| Data Fetcher | HTTP calls to FMP endpoints; response validation and normalisation | `fmp_client.py` |
| Calculator | Computes SMAs, growth rates, relative strength, and all rule checks | `screener.py` |
| Result Model | Structured output with per-criterion pass/fail and raw values | `models.py` |

Entry point: a single function `screen(ticker: str, api_key: str) -> ScreenResult`. Callers pass the ticker and their FMP API key; the function returns a fully populated `ScreenResult` dataclass.

---

## 3. Data Requirements & FMP Endpoints

Six FMP endpoints are required. All are called sequentially on each invocation. The FMP base URL is `https://financialmodelingprep.com/api`.

### 3.1 Endpoint Summary

| # | Endpoint | Data Used For | Params |
|---|---|---|---|
| 1 | `/v3/quote/{ticker}` | Current price, today's volume, quick 52-week high/low | — |
| 2 | `/v3/historical-price-full/{ticker}` | Daily OHLCV for SMA calculation, 52-week range, volume trend | `timeseries=365` |
| 3 | `/v3/income-statement/{ticker}` (quarterly) | Quarterly EPS and revenue for acceleration checks | `period=quarter, limit=8` |
| 4 | `/v3/income-statement/{ticker}` (annual) | Annual EPS for 3-year trend check | `period=annual, limit=3` |
| 5 | `/v3/analyst-estimates/{ticker}` | Forward EPS estimates — analyst growth expectations | `period=quarter, limit=4` |
| 6 | `/v3/profile/{ticker}` | Float, shares outstanding, sector, industry | — |

### 3.2 Endpoint Detail

#### Endpoint 1 — Quote

```
GET /v3/quote/{ticker}?apikey={key}
```

Fields consumed: `price`, `volume`, `yearHigh`, `yearLow`. Used to seed the 52-week range (cross-validated against historical data) and as the current price reference for all Trend Template checks.

#### Endpoint 2 — Historical Price

```
GET /v3/historical-price-full/{ticker}?timeseries=365&apikey={key}
```

Fields consumed: `date`, `close`, `volume` from the historical array. 365 calendar days gives approximately 252 trading days, sufficient for the 200-day SMA plus a 21-day lookback for slope detection. The 52-week high and low are derived from this dataset rather than the quote endpoint for precision.

#### Endpoint 3 — Quarterly Income Statement

```
GET /v3/income-statement/{ticker}?period=quarter&limit=8&apikey={key}
```

Fields consumed: `date`, `eps`, `revenue`. Eight quarters gives four quarters of current-year data and four quarters of prior-year data, enabling YoY growth and acceleration calculations across the most recent three to four quarters.

#### Endpoint 4 — Annual Income Statement

```
GET /v3/income-statement/{ticker}?period=annual&limit=3&apikey={key}
```

Fields consumed: `date`, `eps`. Three years of annual EPS establishes the long-term upward trend requirement. The check passes if Year 1 < Year 2 < Year 3.

#### Endpoint 5 — Analyst Estimates

```
GET /v3/analyst-estimates/{ticker}?period=quarter&limit=4&apikey={key}
```

Fields consumed: `date`, `estimatedEpsAvg`, `estimatedRevenueAvg`. At least one future-dated estimate must show positive growth vs. the same quarter's actuals. This is a soft signal; the screener records it but does not hard-fail on absence of estimates (small caps may have no analyst coverage).

#### Endpoint 6 — Profile

```
GET /v3/profile/{ticker}?apikey={key}
```

Fields consumed: `floatShares`, `sharesOutstanding`, `sector`, `industry`, `mktCap`. Enriches the output record for human review. Float and sector are not used in pass/fail logic in v1 but are surfaced in the result for downstream use.

---

## 4. Calculations

### 4.1 Moving Averages

All SMAs are computed from the sorted historical close price array (most recent last). The array must contain at least 200 entries; if not, the screener returns an `INSUFFICIENT_DATA` error rather than a partial result.

| Variable | Calculation | Notes |
|---|---|---|
| `sma_50` | Mean of last 50 closes | Requires ≥ 50 data points |
| `sma_150` | Mean of last 150 closes | Requires ≥ 150 data points |
| `sma_200` | Mean of last 200 closes | Requires ≥ 200 data points |
| `sma_200_21d_ago` | Mean of `closes[−221:−21]` | Used to assess 200-day slope |

### 4.2 Trend Template Checks

All eight conditions must be true for the Trend Template to pass. A stock failing any single condition fails the template.

| Check | Condition | Formula |
|---|---|---|
| T1 | Price above 50-day SMA | `current_price > sma_50` |
| T2 | Price above 150-day SMA | `current_price > sma_150` |
| T3 | Price above 200-day SMA | `current_price > sma_200` |
| T4 | 200-day SMA trending up | `sma_200 > sma_200_21d_ago` |
| T5 | 50-day SMA above 150-day SMA | `sma_50 > sma_150` |
| T6 | 150-day SMA above 200-day SMA | `sma_150 > sma_200` |
| T7 | Price ≥ 25% above 52-week low | `(price − low_52w) / low_52w >= 0.25` |
| T8 | Price within 25% of 52-week high | `(high_52w − price) / high_52w <= 0.25` |

### 4.3 Earnings Checks

#### Quarterly EPS Growth (E1)

For each of the three most recent quarters, compute YoY growth against the same quarter one year prior. The most recent quarter must show ≥ 25% YoY growth. All three quarters must show positive YoY growth.

```
yoy_growth[q] = (eps[q] − eps[q−4]) / abs(eps[q−4])
```

#### Earnings Acceleration (E2)

The YoY growth rate must be increasing quarter over quarter across the most recent three quarters — i.e., growth is not just positive, it is getting stronger.

```
yoy_growth[q−1] < yoy_growth[q]  (for at least the last two quarters)
```

#### Annual EPS Trend (E3)

Annual EPS must show a monotonically increasing trend over the three most recent fiscal years.

```
eps_annual[y−2] < eps_annual[y−1] < eps_annual[y]
```

### 4.4 Revenue Check (R1)

Most recent quarter revenue must show ≥ 20% YoY growth. Computed identically to quarterly EPS growth using the `revenue` field.

```
revenue_yoy = (revenue[q] − revenue[q−4]) / revenue[q−4] >= 0.20
```

### 4.5 Relative Strength Proxy (RS1)

A true IBD-style RS Rating requires a universe percentile. For single-ticker use, the system computes a weighted 12-month return as a directional signal only. The result is informational — not a hard pass/fail gate in v1.

| Period | Weight | Return Calculation |
|---|---|---|
| Q1 (oldest) | 20% | Close at end of Q1 / close at start of period − 1 |
| Q2 | 20% | Close at end of Q2 / close at start of Q2 − 1 |
| Q3 | 20% | Close at end of Q3 / close at start of Q3 − 1 |
| Q4 (most recent) | 40% | Current price / close at start of Q4 − 1 |

```
rs_score = (0.20 * q1) + (0.20 * q2) + (0.20 * q3) + (0.40 * q4)
```

---

## 5. Result Schema

The `screen()` function returns a `ScreenResult` dataclass with the following structure:

| Field | Type | Description |
|---|---|---|
| `ticker` | `str` | Input ticker symbol, uppercased |
| `overall_pass` | `bool` | True only if all hard-pass checks pass |
| `trend_template_pass` | `bool` | True if all 8 T-checks pass |
| `trend_checks` | `dict[str, CheckResult]` | Per-check result for T1–T8 |
| `earnings_pass` | `bool` | True if E1, E2, E3 all pass |
| `earnings_checks` | `dict[str, CheckResult]` | Per-check result for E1–E3 |
| `revenue_pass` | `bool` | True if R1 passes |
| `rs_weighted_return` | `float` | Weighted 12-month return (informational) |
| `profile` | `ProfileData` | Sector, industry, float, market cap |
| `fetched_at` | `datetime` | UTC timestamp of data fetch |
| `error` | `str \| None` | Set if a fetch or data error occurred |

`CheckResult` is a dataclass with fields: `passed` (bool), `actual_value` (float), `threshold` (float), and `label` (str).

---

## 6. Error Handling

| Error Condition | Behaviour |
|---|---|
| Ticker not found (FMP 404) | Raise `TickerNotFoundError` |
| FMP rate limit exceeded (429) | Raise `RateLimitError` with retry-after hint |
| Fewer than 200 days of price history | Return result with `error = INSUFFICIENT_PRICE_HISTORY`, `overall_pass = False` |
| Fewer than 8 quarters of earnings | Run available checks; set missing checks to `INSUFFICIENT_DATA` |
| No analyst estimates available | Set `rs_weighted_return` to `None`; do not fail `overall_pass` |
| Network timeout | Retry once after 2s; raise `NetworkError` on second failure |

---

## 7. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `requests` | ≥ 2.31 | HTTP calls to FMP |
| `pandas` | ≥ 2.0 | SMA and time-series calculations |
| `pydantic` | ≥ 2.0 | Response validation and result modelling |
| `python-dateutil` | ≥ 2.8 | Date parsing from FMP response strings |

---

## 8. Example Output (Illustrative)

```python
screen("AAPL", api_key="...")
```

| Check | Result | Value | Threshold |
|---|---|---|---|
| T1: Price > SMA50 | ✅ PASS | $189.42 vs $181.30 | > SMA50 |
| T2: Price > SMA150 | ✅ PASS | $189.42 vs $173.80 | > SMA150 |
| T3: Price > SMA200 | ✅ PASS | $189.42 vs $168.50 | > SMA200 |
| T4: SMA200 trending up | ✅ PASS | $168.50 vs $163.20 (21d ago) | Slope > 0 |
| T5: SMA50 > SMA150 | ✅ PASS | $181.30 vs $173.80 | > SMA150 |
| T6: SMA150 > SMA200 | ✅ PASS | $173.80 vs $168.50 | > SMA200 |
| T7: 25% above 52w low | ✅ PASS | +48.2% | ≥ 25% |
| T8: Within 25% of 52w high | ✅ PASS | 3.1% below high | ≤ 25% |
| E1: Recent qtr EPS growth | ✅ PASS | +31% YoY | ≥ 25% |
| E2: EPS acceleration | ✅ PASS | 18% → 24% → 31% | Increasing |
| E3: Annual EPS trend | ✅ PASS | $4.30 → $5.10 → $6.08 | Monotonic |
| R1: Revenue growth | ✅ PASS | +22% YoY | ≥ 20% |
| RS Proxy (informational) | — | +41% weighted return | — |

**OVERALL: ✅ PASS — All hard checks satisfied.**

---

## 9. Known Limitations

- **Relative Strength is a proxy only.** Percentile ranking against a universe is deferred to v2.
- **FMP fundamentals may lag SEC filings by up to 7 days.** The `fetched_at` timestamp is included so callers can assess freshness.
- **Forward estimates are absent for many small and micro-cap stocks.** The system degrades gracefully but callers should note the absence.
- **EPS acceleration is checked across only the most recent two quarter transitions** due to data availability constraints on the free FMP tier.
- **No adjustment is made for stock splits in EPS history.** FMP reports split-adjusted EPS on most tickers, but this should be validated against a second source for backtesting use.
