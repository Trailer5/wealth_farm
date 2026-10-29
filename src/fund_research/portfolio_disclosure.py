"""Fund look-through for portfolio diagnosis: asset class, quarterly allocation and top-ten holdings.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「组合诊断」.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.data_src.models import FundPortfolioDisclosure
from src.data_src.process_executor import run_provider_task
from src.master_data import Asset
from src.research_contracts import (
    ASSET_CLASS_BOND,
    ASSET_CLASS_CASH,
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_OTHER,
    ASSET_CLASS_OVERSEAS,
)

from .sqlite_repository import SQLiteFundResearchRepository

# Holdings change quarterly; a weekly refetch picks up a new report within days.
PORTFOLIO_MAX_AGE_DAYS = 7
# Quarterly reports are due 15 working days after quarter end; 25 calendar days is the conservative date.
REPORT_LAG_DAYS = 25

_COMMODITY_WORDS = ("黄金", "白银", "商品", "豆粕", "有色", "能源化工", "原油")

PortfolioFetcher = Callable[[str, float], FundPortfolioDisclosure]
IndexWeightFetcher = Callable[[str, float], list[dict[str, str]]]

# Tracked-index names to China Securities Index codes whose weights csindex publishes.
TRACKED_INDEX_CODES = {
    "沪深300": "000300",
    "中证500": "000905",
    "中证1000": "000852",
    "中证2000": "932000",
    "中证800": "000906",
    "中证100": "000903",
    "中证A500": "000510",
    "中证A50": "930050",
    "中证红利": "000922",
    "上证50": "000016",
    "上证180": "000010",
    "上证380": "000009",
    "上证红利": "000015",
    "科创50": "000688",
    "科创100": "000698",
    "中证白酒": "399997",
    "中证医疗": "399989",
    "中证军工": "399967",
    "中证银行": "399986",
    "中证全指证券公司": "399975",
    "中证新能源汽车": "399976",
    "中证光伏产业": "931151",
    "中证半导体产业": "931865",
    "中华半导体芯片": "990001",
    "中证创新药产业": "931152",
    "中证人工智能主题": "930713",
    "中证全指半导体": "H30184",
    "中证消费电子主题": "931494",
    "中证主要消费": "000932",
    "中证全指医药卫生": "000991",
}


def tracked_index_code(tracking_target: str | None) -> tuple[str, str] | None:
    """(table name, code) of the longest table name in the tracking target, so 中证A500 is not read as 中证A50."""

    text = (tracking_target or "").replace(" ", "")
    for name in sorted(TRACKED_INDEX_CODES, key=len, reverse=True):
        position = text.find(name)
        end = position + len(name)
        if position >= 0 and not (end < len(text) and text[end].isdigit()):
            return name, TRACKED_INDEX_CODES[name]
    return None


def _fetch_weights_in_process(symbol: str, timeout_seconds: float) -> list[dict[str, str]]:
    return run_provider_task(
        "src.data_src.provider_tasks",
        "fetch_index_constituent_weights",
        {"symbol": symbol},
        timeout_seconds=timeout_seconds,
    )


class IndexWeightService:
    """Month-end constituent weights of a tracked index, refetched weekly."""

    def __init__(
        self,
        *,
        repository: SQLiteFundResearchRepository,
        fetcher: IndexWeightFetcher | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher or _fetch_weights_in_process
        self.today = today
        self.repository.initialize()

    def weights(
        self,
        index_code: str,
        *,
        allow_remote: bool = True,
        timeout_seconds: float = 60.0,
    ) -> tuple[list[dict[str, str]] | None, str | None]:
        cached = self.repository.latest_index_weights(index_code)
        fresh = cached is not None and (self.today() - date.fromisoformat(cached[0])).days <= PORTFOLIO_MAX_AGE_DAYS
        if fresh or not allow_remote:
            return (cached[1] if cached else None), None
        try:
            rows = self.fetcher(index_code, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - a stale cache is still better than nothing
            return (cached[1] if cached else None), f"{type(exc).__name__}: {exc}"
        if not rows:
            return (cached[1] if cached else None), "中证指数公司没有返回成分权重"
        self.repository.save_index_weights(index_code, self.today().isoformat(), rows)
        return rows, None


def _fetch_in_process(symbol: str, timeout_seconds: float) -> FundPortfolioDisclosure:
    return run_provider_task(
        "src.data_src.provider_tasks",
        "fetch_fund_portfolio_disclosure",
        {"symbol": symbol},
        timeout_seconds=timeout_seconds,
    )


class FundPortfolioService:
    def __init__(
        self,
        *,
        repository: SQLiteFundResearchRepository,
        fetcher: PortfolioFetcher | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher or _fetch_in_process
        self.today = today
        self.repository.initialize()

    def disclosure(
        self,
        asset: Asset,
        *,
        allow_remote: bool = True,
        timeout_seconds: float = 45.0,
    ) -> tuple[FundPortfolioDisclosure | None, str | None]:
        cached = self.repository.latest_portfolio_disclosure(asset.asset_id)
        fresh = cached is not None and (self.today() - date.fromisoformat(cached.fetched_on)).days <= PORTFOLIO_MAX_AGE_DAYS
        if fresh or not allow_remote:
            return cached, None
        try:
            disclosure = self.fetcher(asset.standard_symbol, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - a stale cache is still better than nothing
            return cached, f"{type(exc).__name__}: {exc}"
        self.repository.save_portfolio_disclosure(asset.asset_id, disclosure)
        return disclosure, None


def available_on(period_end: str) -> str:
    return (date.fromisoformat(period_end) + timedelta(days=REPORT_LAG_DAYS)).isoformat()


def fund_asset_class(fund_type: str | None, tracking_target: str | None, name: str | None) -> str | None:
    """A class the whole fund belongs to, or None when the quarterly allocation decides the split."""

    kind = fund_type or ""
    text = f"{tracking_target or ''}{name or ''}"
    if "货币" in kind:
        return ASSET_CLASS_CASH
    if "QDII" in kind or "海外" in kind:
        return ASSET_CLASS_OVERSEAS
    if "商品" in kind or (kind.startswith("指数型-其他") and any(word in text for word in _COMMODITY_WORDS)):
        return ASSET_CLASS_COMMODITY
    if kind.startswith("指数型-固收"):
        return ASSET_CLASS_BOND
    if "REIT" in kind.upper():
        return ASSET_CLASS_OTHER
    return None


def default_class(fund_type: str | None) -> str:
    """Class assumed when the allocation is unavailable."""

    return ASSET_CLASS_BOND if (fund_type or "").startswith("债券") else ASSET_CLASS_EQUITY


def look_through(disclosure: FundPortfolioDisclosure, *, as_of_date: str) -> dict[str, Any]:
    """The latest allocation and top-ten holdings already published on the cutoff date."""

    allocation = next(
        (item for item in disclosure.allocations if available_on(item.period_end) <= as_of_date),
        None,
    )
    periods = sorted(
        {item.period_end for item in disclosure.holdings if available_on(item.period_end) <= as_of_date},
        reverse=True,
    )
    holdings_period = periods[0] if periods else None
    industry_periods = sorted(
        {item.period_end for item in disclosure.industries if available_on(item.period_end) <= as_of_date},
        reverse=True,
    )
    industry_period = industry_periods[0] if industry_periods else None
    return {
        "industry_period": industry_period,
        "industries": [
            {"scheme": item.scheme, "code": item.code, "name": item.name, "ratio": item.net_asset_ratio}
            for item in disclosure.industries
            if item.period_end == industry_period
        ],
        "allocation": (
            None
            if allocation is None
            else {
                "period_end": allocation.period_end,
                "stock": allocation.stock_ratio or Decimal("0"),
                "bond": allocation.bond_ratio or Decimal("0"),
                "cash": allocation.cash_ratio or Decimal("0"),
            }
        ),
        "holdings_period": holdings_period,
        "holdings": [
            {
                "code": item.code,
                "name": item.name,
                "market": item.market,
                "ratio": item.net_asset_ratio,
            }
            for item in disclosure.holdings
            if item.period_end == holdings_period and item.net_asset_ratio is not None
        ],
        "fetched_on": disclosure.fetched_on,
        "source_urls": list(disclosure.source_urls),
    }
