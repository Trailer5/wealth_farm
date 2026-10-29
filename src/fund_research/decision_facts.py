"""Facts an Agent needs for open-fund timing and sizing: dealing rules, costs, tenure, underlying valuation.

Design notes: docs/modules/3-分析工具/基金与ETF研究数据模块.md「场外基金时点与仓位事实」.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from src.data_src.models import OpenFundDealingInfo
from src.data_src.process_executor import run_provider_task
from src.master_data import Asset

from .dealing import dealing_summary, manager_tenure
from .sqlite_repository import SQLiteFundResearchRepository
from .underlying_valuation import UnderlyingValuationService

# Fee pages change rarely; refetch weekly so purchase suspensions are still noticed.
DEALING_MAX_AGE_DAYS = 7

DealingFetcher = Callable[[str, float], OpenFundDealingInfo]


def _fetch_in_process(symbol: str, timeout_seconds: float) -> OpenFundDealingInfo:
    return run_provider_task(
        "src.data_src.provider_tasks",
        "fetch_open_fund_dealing_info",
        {"symbol": symbol},
        timeout_seconds=timeout_seconds,
    )


class OpenFundDecisionFactsService:
    def __init__(
        self,
        *,
        repository: SQLiteFundResearchRepository,
        underlying_valuation: UnderlyingValuationService,
        fetcher: DealingFetcher | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.repository = repository
        self.underlying_valuation = underlying_valuation
        self.fetcher = fetcher or _fetch_in_process
        self.today = today
        self.repository.initialize()

    def dealing_info(
        self,
        asset: Asset,
        *,
        allow_remote: bool = True,
        timeout_seconds: float = 30.0,
    ) -> tuple[OpenFundDealingInfo | None, str | None]:
        cached = self.repository.latest_dealing_info(asset.asset_id)
        fresh = cached is not None and (self.today() - date.fromisoformat(cached.fetched_on)).days <= DEALING_MAX_AGE_DAYS
        if fresh or not allow_remote:
            return cached, None
        try:
            info = self.fetcher(asset.standard_symbol, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - a stale cache is still better than nothing
            return cached, f"{type(exc).__name__}: {exc}"
        self.repository.save_dealing_info(asset.asset_id, info)
        return info, None

    def build(
        self,
        asset: Asset,
        *,
        as_of_date: str,
        nav_points: list[tuple[str, float]],
        allow_remote: bool = True,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        info, error = self.dealing_info(asset, allow_remote=allow_remote, timeout_seconds=timeout_seconds)
        gaps = []
        facts: dict[str, Any] = {"as_of_date": as_of_date}
        if error:
            gaps.append(f"申赎规则抓取失败：{error}" + ("，使用缓存" if info else ""))
        if info is None:
            gaps.append("没有申赎规则与费率，无法计算交易成本、费率拖累和现任经理任期")
        else:
            facts["dealing"] = dealing_summary(info)
            facts["manager_tenure"] = manager_tenure(info.manager_terms, nav_points, as_of_date=as_of_date)
            if info.fetched_on > as_of_date:
                gaps.append(f"申赎规则与费率是 {info.fetched_on} 抓取的当前值，截止日 {as_of_date} 时可能不同")
        facts["underlying_valuation"] = self.underlying_valuation.assess(
            benchmark_text=info.benchmark_text if info else None,
            tracking_target=info.tracking_target if info else None,
            as_of_date=as_of_date,
            allow_remote=allow_remote,
        )
        facts["gaps"] = gaps
        return facts
