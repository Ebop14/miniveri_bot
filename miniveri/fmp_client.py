import time

import requests

from miniveri.exceptions import NetworkError, RateLimitError, TickerNotFoundError

BASE_URL = "https://financialmodelingprep.com/api"
TIMEOUT = 15


def _get(url: str, params: dict) -> dict | list:
    """Make a GET request to FMP with one automatic retry on timeout."""
    for attempt in range(2):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
        except requests.exceptions.RequestException as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            raise NetworkError(str(exc)) from exc

        if resp.status_code == 404:
            raise TickerNotFoundError(params.get("ticker", "unknown"))
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimitError(int(retry_after) if retry_after else None)
        resp.raise_for_status()
        return resp.json()

    raise NetworkError("Max retries exceeded")


class FMPClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _params(self, **extra) -> dict:
        return {"apikey": self.api_key, **extra}

    def get_quote(self, ticker: str) -> dict:
        data = _get(f"{BASE_URL}/v3/quote/{ticker}", self._params())
        if not data:
            raise TickerNotFoundError(ticker)
        return data[0] if isinstance(data, list) else data

    def get_historical_prices(self, ticker: str) -> list[dict]:
        data = _get(
            f"{BASE_URL}/v3/historical-price-full/{ticker}",
            self._params(timeseries=365),
        )
        if not data or "historical" not in data:
            raise TickerNotFoundError(ticker)
        return data["historical"]

    def get_quarterly_income(self, ticker: str) -> list[dict]:
        data = _get(
            f"{BASE_URL}/v3/income-statement/{ticker}",
            self._params(period="quarter", limit=8),
        )
        if not data:
            return []
        return data

    def get_annual_income(self, ticker: str) -> list[dict]:
        data = _get(
            f"{BASE_URL}/v3/income-statement/{ticker}",
            self._params(period="annual", limit=3),
        )
        if not data:
            return []
        return data

    def get_analyst_estimates(self, ticker: str) -> list[dict]:
        data = _get(
            f"{BASE_URL}/v3/analyst-estimates/{ticker}",
            self._params(period="quarter", limit=4),
        )
        if not data:
            return []
        return data

    def get_profile(self, ticker: str) -> dict:
        data = _get(f"{BASE_URL}/v3/profile/{ticker}", self._params())
        if not data:
            raise TickerNotFoundError(ticker)
        return data[0] if isinstance(data, list) else data
