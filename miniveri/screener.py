from datetime import datetime, timezone

import pandas as pd

from miniveri.fmp_client import FMPClient
from miniveri.models import CheckResult, ProfileData, ScreenResult


def _compute_smas(closes: list[float]) -> dict:
    """Compute SMA-50, SMA-150, SMA-200, and SMA-200 from 21 days ago."""
    return {
        "sma_50": sum(closes[-50:]) / 50,
        "sma_150": sum(closes[-150:]) / 150,
        "sma_200": sum(closes[-200:]) / 200,
        "sma_200_21d_ago": sum(closes[-221:-21]) / 200,
    }


def _trend_checks(price: float, smas: dict, high_52w: float, low_52w: float) -> dict[str, CheckResult]:
    """Evaluate the 8 Trend Template conditions (T1-T8)."""
    pct_above_low = (price - low_52w) / low_52w if low_52w else 0
    pct_below_high = (high_52w - price) / high_52w if high_52w else 0

    return {
        "T1": CheckResult(
            label="Price > SMA50",
            passed=price > smas["sma_50"],
            actual_value=round(price, 2),
            threshold=round(smas["sma_50"], 2),
        ),
        "T2": CheckResult(
            label="Price > SMA150",
            passed=price > smas["sma_150"],
            actual_value=round(price, 2),
            threshold=round(smas["sma_150"], 2),
        ),
        "T3": CheckResult(
            label="Price > SMA200",
            passed=price > smas["sma_200"],
            actual_value=round(price, 2),
            threshold=round(smas["sma_200"], 2),
        ),
        "T4": CheckResult(
            label="SMA200 trending up",
            passed=smas["sma_200"] > smas["sma_200_21d_ago"],
            actual_value=round(smas["sma_200"], 2),
            threshold=round(smas["sma_200_21d_ago"], 2),
        ),
        "T5": CheckResult(
            label="SMA50 > SMA150",
            passed=smas["sma_50"] > smas["sma_150"],
            actual_value=round(smas["sma_50"], 2),
            threshold=round(smas["sma_150"], 2),
        ),
        "T6": CheckResult(
            label="SMA150 > SMA200",
            passed=smas["sma_150"] > smas["sma_200"],
            actual_value=round(smas["sma_150"], 2),
            threshold=round(smas["sma_200"], 2),
        ),
        "T7": CheckResult(
            label="Price >= 25% above 52w low",
            passed=pct_above_low >= 0.25,
            actual_value=round(pct_above_low * 100, 1),
            threshold=25.0,
        ),
        "T8": CheckResult(
            label="Price within 25% of 52w high",
            passed=pct_below_high <= 0.25,
            actual_value=round(pct_below_high * 100, 1),
            threshold=25.0,
        ),
    }


def _earnings_checks(quarterly: list[dict], annual: list[dict]) -> dict[str, CheckResult]:
    """Evaluate E1 (quarterly EPS growth), E2 (acceleration), E3 (annual trend)."""
    checks: dict[str, CheckResult] = {}

    # --- E1: Quarterly EPS YoY growth ---
    # quarterly is sorted most-recent-first from FMP
    if len(quarterly) >= 8:
        yoy_growths = []
        for i in range(3):  # most recent 3 quarters
            current_eps = quarterly[i].get("eps", 0)
            prior_eps = quarterly[i + 4].get("eps", 0)
            if prior_eps and abs(prior_eps) > 0:
                growth = (current_eps - prior_eps) / abs(prior_eps)
                yoy_growths.append(growth)
            else:
                yoy_growths.append(None)

        most_recent_growth = yoy_growths[0]
        all_positive = all(g is not None and g > 0 for g in yoy_growths)
        e1_pass = (
            most_recent_growth is not None
            and most_recent_growth >= 0.25
            and all_positive
        )
        checks["E1"] = CheckResult(
            label="Quarterly EPS growth (most recent >= 25%, all 3 positive)",
            passed=e1_pass,
            actual_value=round(most_recent_growth * 100, 1) if most_recent_growth is not None else None,
            threshold=25.0,
        )

        # --- E2: EPS Acceleration ---
        if all(g is not None for g in yoy_growths):
            # yoy_growths[0] is most recent, [2] is oldest of the three
            accelerating = yoy_growths[1] < yoy_growths[0]
            checks["E2"] = CheckResult(
                label="EPS acceleration (growth rate increasing)",
                passed=accelerating,
                actual_value=round(yoy_growths[0] * 100, 1),
                threshold=round(yoy_growths[1] * 100, 1),
            )
        else:
            checks["E2"] = CheckResult(
                label="EPS acceleration (growth rate increasing)",
                passed=False,
                actual_value=None,
                threshold=None,
            )
    else:
        checks["E1"] = CheckResult(
            label="Quarterly EPS growth — INSUFFICIENT_DATA",
            passed=False,
        )
        checks["E2"] = CheckResult(
            label="EPS acceleration — INSUFFICIENT_DATA",
            passed=False,
        )

    # --- E3: Annual EPS trend (monotonically increasing over 3 years) ---
    if len(annual) >= 3:
        # annual is most-recent-first; reverse for chronological
        eps_vals = [a.get("eps", 0) for a in reversed(annual[:3])]
        monotonic = eps_vals[0] < eps_vals[1] < eps_vals[2]
        checks["E3"] = CheckResult(
            label="Annual EPS trend (3-year monotonic increase)",
            passed=monotonic,
            actual_value=eps_vals[2],
            threshold=eps_vals[1],
        )
    else:
        checks["E3"] = CheckResult(
            label="Annual EPS trend — INSUFFICIENT_DATA",
            passed=False,
        )

    return checks


def _revenue_check(quarterly: list[dict]) -> CheckResult:
    """Evaluate R1: Most recent quarter revenue YoY growth >= 20%."""
    if len(quarterly) >= 5:
        current_rev = quarterly[0].get("revenue", 0)
        prior_rev = quarterly[4].get("revenue", 0)
        if prior_rev and prior_rev > 0:
            growth = (current_rev - prior_rev) / prior_rev
            return CheckResult(
                label="Revenue YoY growth >= 20%",
                passed=growth >= 0.20,
                actual_value=round(growth * 100, 1),
                threshold=20.0,
            )
    return CheckResult(
        label="Revenue YoY growth — INSUFFICIENT_DATA",
        passed=False,
    )


def _rs_weighted_return(closes: list[float]) -> float | None:
    """Compute a weighted 12-month return as a relative strength proxy."""
    if len(closes) < 252:
        return None

    # Split the last ~252 trading days into 4 quarters (~63 days each)
    q_len = 63
    start = len(closes) - 252
    quarters = []
    for i in range(4):
        q_start = start + i * q_len
        q_end = q_start + q_len
        if i == 3:
            q_end = len(closes)  # extend last quarter to current
        if q_start >= len(closes) or q_end > len(closes):
            return None
        ret = (closes[q_end - 1] / closes[q_start]) - 1
        quarters.append(ret)

    return round(
        0.20 * quarters[0] + 0.20 * quarters[1] + 0.20 * quarters[2] + 0.40 * quarters[3],
        4,
    )


def screen(ticker: str, api_key: str) -> ScreenResult:
    """Screen a single ticker against the Minervini criteria."""
    ticker = ticker.upper()
    client = FMPClient(api_key)
    fetched_at = datetime.now(timezone.utc)

    # --- Fetch data ---
    quote = client.get_quote(ticker)
    historical = client.get_historical_prices(ticker)
    quarterly_income = client.get_quarterly_income(ticker)
    annual_income = client.get_annual_income(ticker)
    client.get_analyst_estimates(ticker)  # fetched for future use
    profile_data = client.get_profile(ticker)

    # --- Parse historical closes (chronological order) ---
    hist_df = pd.DataFrame(historical).sort_values("date")
    closes = hist_df["close"].tolist()

    if len(closes) < 200:
        return ScreenResult(
            ticker=ticker,
            overall_pass=False,
            trend_template_pass=False,
            trend_checks={},
            earnings_pass=False,
            earnings_checks={},
            revenue_pass=False,
            fetched_at=fetched_at,
            error="INSUFFICIENT_PRICE_HISTORY",
        )

    # --- Price data ---
    current_price = quote["price"]
    high_52w = hist_df["close"].max()
    low_52w = hist_df["close"].min()

    # --- Compute SMAs ---
    smas = _compute_smas(closes)

    # --- Trend Template ---
    t_checks = _trend_checks(current_price, smas, high_52w, low_52w)
    trend_pass = all(c.passed for c in t_checks.values())

    # --- Earnings ---
    e_checks = _earnings_checks(quarterly_income, annual_income)
    earnings_pass = all(c.passed for c in e_checks.values())

    # --- Revenue ---
    r_check = _revenue_check(quarterly_income)

    # --- RS Proxy ---
    rs_return = _rs_weighted_return(closes)

    # --- Profile ---
    profile = ProfileData(
        sector=profile_data.get("sector"),
        industry=profile_data.get("industry"),
        float_shares=profile_data.get("floatShares"),
        shares_outstanding=profile_data.get("sharesOutstanding"),
        market_cap=profile_data.get("mktCap"),
    )

    # --- Overall ---
    overall = trend_pass and earnings_pass and r_check.passed

    return ScreenResult(
        ticker=ticker,
        overall_pass=overall,
        trend_template_pass=trend_pass,
        trend_checks=t_checks,
        earnings_pass=earnings_pass,
        earnings_checks=e_checks,
        revenue_pass=r_check.passed,
        revenue_check=r_check,
        rs_weighted_return=rs_return,
        profile=profile,
        fetched_at=fetched_at,
    )
