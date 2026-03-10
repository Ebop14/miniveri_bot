import os
import sys

from dotenv import load_dotenv

from miniveri.exceptions import MiniveriError
from miniveri.screener import screen


def print_result(result):
    """Pretty-print the screening result."""
    print(f"\n{'='*60}")
    print(f"  MINERVINI SCREEN — {result.ticker}")
    print(f"  {result.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if result.error:
        print(f"  ERROR: {result.error}\n")
        return

    # Trend Template
    print("  TREND TEMPLATE", "PASS" if result.trend_template_pass else "FAIL")
    print(f"  {'-'*56}")
    for key, check in result.trend_checks.items():
        status = "PASS" if check.passed else "FAIL"
        val = f"{check.actual_value}" if check.actual_value is not None else "N/A"
        thr = f"{check.threshold}" if check.threshold is not None else ""
        print(f"  {key}: {check.label:<40} {status}  ({val} vs {thr})")

    # Earnings
    print(f"\n  EARNINGS", "PASS" if result.earnings_pass else "FAIL")
    print(f"  {'-'*56}")
    for key, check in result.earnings_checks.items():
        status = "PASS" if check.passed else "FAIL"
        val = f"{check.actual_value}" if check.actual_value is not None else "N/A"
        thr = f"{check.threshold}" if check.threshold is not None else ""
        print(f"  {key}: {check.label:<50} {status}  ({val} vs {thr})")

    # Revenue
    print(f"\n  REVENUE", "PASS" if result.revenue_pass else "FAIL")
    print(f"  {'-'*56}")
    if result.revenue_check:
        rc = result.revenue_check
        status = "PASS" if rc.passed else "FAIL"
        val = f"{rc.actual_value}" if rc.actual_value is not None else "N/A"
        print(f"  R1: {rc.label:<50} {status}  ({val}% vs {rc.threshold}%)")

    # RS Proxy
    if result.rs_weighted_return is not None:
        print(f"\n  RS PROXY (informational): {result.rs_weighted_return * 100:.1f}% weighted 12-mo return")

    # Profile
    if result.profile:
        p = result.profile
        print(f"\n  PROFILE: {p.sector} / {p.industry}")
        if p.market_cap:
            print(f"  Market Cap: ${p.market_cap:,.0f}")

    # Overall
    overall = "PASS" if result.overall_pass else "FAIL"
    print(f"\n{'='*60}")
    print(f"  OVERALL: {overall}")
    print(f"{'='*60}\n")


def main():
    load_dotenv()
    api_key = os.getenv("FMP_API_KEY")

    if not api_key or api_key == "your_api_key_here":
        print("Error: Set FMP_API_KEY in your .env file.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python main.py <TICKER>")
        print("Example: python main.py AAPL")
        sys.exit(1)

    ticker = sys.argv[1]

    try:
        result = screen(ticker, api_key)
        print_result(result)
    except MiniveriError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
