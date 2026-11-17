"""Price history of one or more listings (A-share or US): total return, drawdown and range.

Reads total-return bars through unified data access, so the bars are cached and cross-checked
like everywhere else. Design notes: docs/modules/1-数据/存储与统一访问模块.md「美股」.
"""

from __future__ import annotations

from typing import Any

from src.data_access import (
    DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_src.service import DataFetchService
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    MARKET_HK,
    MARKET_US,
    Asset,
    MasterDataService,
)

_DATASETS = {
    ASSET_TYPE_STOCK: DATASET_STOCK_TOTAL_RETURN_BARS,
    ASSET_TYPE_EXCHANGE_FUND: DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
}


class PriceHistoryService:
    def __init__(
        self,
        *,
        data_access: DataAccessService,
        master_data: MasterDataService,
        data_fetch: DataFetchService,
    ) -> None:
        self.data_access = data_access
        self.master_data = master_data
        self.data_fetch = data_fetch

    def resolve(self, symbol: str, *, market: str | None = None, asset_type: str | None = None) -> Asset:
        """US tickers are registered from Nasdaq on first use; A-share codes from the exchange listings."""

        if market == MARKET_HK:
            code = symbol.strip().upper().removeprefix("HK").removesuffix(".HK").zfill(5)
            known = [item for item in self.master_data.find_assets(standard_symbol=code) if item.market == MARKET_HK]
            return known[0] if known else self.data_fetch.register_hk_listing(code)
        if market == MARKET_US:
            known = [
                item
                for item in self.master_data.find_assets(standard_symbol=symbol.strip().upper(), asset_type=asset_type)
                if item.market == MARKET_US
            ]
            return known[0] if known else self.data_fetch.register_us_listing(symbol)
        candidates = [
            item
            for item in self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
            if item.asset_type in _DATASETS and item.market not in (MARKET_US, MARKET_HK)
        ]
        if not candidates:
            self.data_fetch.fetch_and_store_identities(symbol, asset_type=asset_type)
            candidates = [
                item
                for item in self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
                if item.asset_type in _DATASETS and item.market not in (MARKET_US, MARKET_HK)
            ]
        if len(candidates) != 1:
            raise ValueError(
                f"{symbol} 对应 {len(candidates)} 个股票或场内基金；用 --asset-type 区分，美股加 --market us"
            )
        return candidates[0]

    def history(
        self,
        asset: Asset,
        *,
        start_date: str,
        end_date: str,
        rows: bool = False,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        dataset = _DATASETS.get(asset.asset_type)
        if dataset is None:
            raise ValueError(f"{asset.asset_type} 没有总回报日线；指数用 market-archive 或 sector-archive")
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=dataset,
                start_date=start_date,
                end_date=end_date,
                as_of_date=end_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
            )
        )
        bars = [item for item in response.records if item.get("close")]
        payload: dict[str, Any] = {
            "asset": {
                "asset_id": asset.asset_id,
                "symbol": asset.standard_symbol,
                "name": asset.display_name,
                "asset_type": asset.asset_type,
                "market": asset.market,
                "exchange": asset.exchange,
                "currency": {MARKET_US: "USD", MARKET_HK: "HKD"}.get(asset.market, "CNY"),
            },
            "requested": {"start_date": start_date, "end_date": end_date},
            "effective_end_date": response.metadata.get("effective_end_date", end_date),
            "sources": list(response.sources),
            "provider_errors": response.metadata.get("provider_errors") or {},
            "source_conflict_count": response.metadata.get("source_conflict_count", 0),
            "stale": response.stale,
            "error": response.error,
        }
        payload.update(_summary(bars))
        payload["notes"] = _notes(asset, payload)
        if rows:
            payload["rows"] = [
                {
                    "date": item["trade_date"],
                    "close": item.get("unadjusted_close"),
                    "total_return_level": round(float(item["close"]), 6),
                    "source": item.get("source"),
                }
                for item in bars
            ]
        return payload


def _summary(bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {"bars": 0}
    first, last = bars[0], bars[-1]
    peak = bars[0]
    worst = (0.0, first, first)
    for item in bars:
        if float(item["close"]) > float(peak["close"]):
            peak = item
        drawdown = float(item["close"]) / float(peak["close"]) - 1
        if drawdown < worst[0]:
            worst = (drawdown, peak, item)
    high = max(bars, key=lambda item: float(item["close"]))
    low = min(bars, key=lambda item: float(item["close"]))
    return {
        "bars": len(bars),
        "first": {"date": first["trade_date"], "close": first.get("unadjusted_close")},
        "last": {"date": last["trade_date"], "close": last.get("unadjusted_close")},
        "total_return": round(float(last["close"]) / float(first["close"]) - 1, 6),
        "max_drawdown": {
            "value": round(worst[0], 6),
            "peak_date": worst[1]["trade_date"],
            "trough_date": worst[2]["trade_date"],
        },
        "high": {"date": high["trade_date"], "close": high.get("unadjusted_close")},
        "low": {"date": low["trade_date"], "close": low.get("unadjusted_close")},
        "from_high": round(float(last["close"]) / float(high["close"]) - 1, 6),
    }


def _notes(asset: Asset, payload: dict[str, Any]) -> list[str]:
    notes = [
        "收益与回撤按复权总回报（含分红再投资）计算，close 是当日实际收盘价；区间从第一根日线收盘起算。",
    ]
    if asset.market == MARKET_US:
        notes.append(
            "美股：美元计价，不做汇率换算；日线按美东收盘后才算可得；新浪原始日线+复权因子为主、Nasdaq（只含拆股调整）交叉核对。"
        )
    if asset.market == MARKET_HK:
        notes.append(
            "港股：港元计价，不做汇率换算；日线按香港 16:30 后才算可得；新浪原始日线 + 前复权因子（经 AkShare）推算参考收盘，只有这一个来源。"
        )
    if payload.get("source_conflict_count"):
        notes.append("来源之间有收盘或日收益不一致的日期，引用单日数据前先核对。")
    if payload.get("stale"):
        notes.append("远程获取失败，返回的是本地已有数据，可能不完整。")
    return notes
