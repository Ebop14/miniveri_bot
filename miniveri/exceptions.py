class MiniveriError(Exception):
    pass


class TickerNotFoundError(MiniveriError):
    def __init__(self, ticker: str):
        super().__init__(f"Ticker '{ticker}' not found on FMP")
        self.ticker = ticker


class RateLimitError(MiniveriError):
    def __init__(self, retry_after: int | None = None):
        msg = "FMP rate limit exceeded"
        if retry_after:
            msg += f" — retry after {retry_after}s"
        super().__init__(msg)
        self.retry_after = retry_after


class NetworkError(MiniveriError):
    def __init__(self, detail: str = ""):
        super().__init__(f"Network error: {detail}")
