from datetime import datetime

from pydantic import BaseModel


class CheckResult(BaseModel):
    label: str
    passed: bool
    actual_value: float | None = None
    threshold: float | None = None


class ProfileData(BaseModel):
    sector: str | None = None
    industry: str | None = None
    float_shares: float | None = None
    shares_outstanding: float | None = None
    market_cap: float | None = None


class ScreenResult(BaseModel):
    ticker: str
    overall_pass: bool
    trend_template_pass: bool
    trend_checks: dict[str, CheckResult]
    earnings_pass: bool
    earnings_checks: dict[str, CheckResult]
    revenue_pass: bool
    revenue_check: CheckResult | None = None
    rs_weighted_return: float | None = None
    profile: ProfileData | None = None
    fetched_at: datetime
    error: str | None = None
