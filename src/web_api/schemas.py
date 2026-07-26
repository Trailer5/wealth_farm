"""Request models for the local research API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class WatchlistItemRequest(BaseModel):
    reason: str = ""
    tags: tuple[str, ...] = ()
    status: str = "active"


class HoldingRequest(BaseModel):
    quantity: float
    average_cost: float | None = None
    currency: str = "CNY"
    note: str = ""


class AnalysisReportRequest(BaseModel):
    asset_id: str
    title: str = Field(min_length=1)
    summary: str = ""
    content_markdown: str = Field(min_length=1)
    status: str = "draft"


class AIAnalysisTaskRequest(BaseModel):
    asset_id: str
    question: str = Field(min_length=1)


class PortfolioValuationRequest(BaseModel):
    as_of_date: str = Field(min_length=1, max_length=32)
    prices: dict[str, float] = Field(min_length=1)


class PriceAlertRuleRequest(BaseModel):
    asset_id: str
    rule_type: str
    threshold: float


class EventAlertRuleRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    asset_id: str | None = None


class MaxWeightAlertRuleRequest(BaseModel):
    threshold: float = Field(ge=0, le=1)


class PriceObservationRequest(BaseModel):
    asset_id: str
    price: float
    observed_at: str = Field(min_length=1, max_length=64)


class DailyBarsRequest(BaseModel):
    dataset: str
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    max_age_seconds: int | None = Field(default=86400, ge=0)
    allow_remote: bool = False
    allow_stale_on_error: bool = True
    remote_sources: tuple[str, ...] = ()
    provider_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
