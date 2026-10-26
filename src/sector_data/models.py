"""Shenwan industry catalog, memberships and valuation snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwIndustry:
    code: str
    name: str
    level: int
    parent_code: str | None
    constituent_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "name": self.name,
            "level": self.level,
            "parent_code": self.parent_code,
            "constituent_count": self.constituent_count,
        }


@dataclass(frozen=True)
class IndustryMember:
    symbol: str
    name: str
    industry_code: str
    included_on: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "industry_code": self.industry_code,
            "included_on": self.included_on,
        }


@dataclass(frozen=True)
class IndustryPath:
    symbol: str
    name: str
    l1: SwIndustry
    l2: SwIndustry
    l3: SwIndustry
    included_on: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "l1": self.l1.to_dict(),
            "l2": self.l2.to_dict(),
            "l3": self.l3.to_dict(),
            "included_on": self.included_on,
        }


@dataclass(frozen=True)
class IndustryValuationPoint:
    code: str
    trade_date: str
    pe: float | None
    pb: float | None
    dividend_yield_percent: float | None
    amount_share_percent: float | None
    turnover_rate_percent: float | None
    float_market_cap: float | None
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date,
            "pe": self.pe,
            "pb": self.pb,
            "dividend_yield_percent": self.dividend_yield_percent,
            "amount_share_percent": self.amount_share_percent,
            "turnover_rate_percent": self.turnover_rate_percent,
            "float_market_cap": self.float_market_cap,
        }
