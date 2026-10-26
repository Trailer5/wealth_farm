"""Price series for settlement, read through unified data access."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from src.data_access import (
    DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_OPEN_FUND_NAV,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    PRICE_BASIS_PRICE,
    PRICE_BASIS_TOTAL_RETURN,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
    adjusted_nav_levels,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    MasterDataService,
)

from .settlement import PriceDataUnavailable, PricePoint

# Stocks and exchange funds use total-return bars, open funds NAV with
# distributions reinvested; indexes only have price levels.
_SERIES = {
    ASSET_TYPE_STOCK: (DATASET_STOCK_TOTAL_RETURN_BARS, PRICE_BASIS_TOTAL_RETURN),
    ASSET_TYPE_EXCHANGE_FUND: (
        DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
        PRICE_BASIS_TOTAL_RETURN,
    ),
    ASSET_TYPE_INDEX: (DATASET_INDEX_DAILY_BARS, PRICE_BASIS_PRICE),
    ASSET_TYPE_OPEN_FUND: (DATASET_OPEN_FUND_NAV, PRICE_BASIS_TOTAL_RETURN),
}
_LEVEL_QUANTUM = Decimal("0.00000001")


class DataAccessPriceSeriesSource:
    def __init__(
        self,
        *,
        data_access: DataAccessService,
        master_data: MasterDataService,
        allow_remote: bool = True,
        provider_timeout_seconds: float = 90.0,
    ) -> None:
        self.data_access = data_access
        self.master_data = master_data
        self.allow_remote = allow_remote
        self.provider_timeout_seconds = provider_timeout_seconds

    def series(
        self,
        asset_id: str,
        start: str,
        end: str,
    ) -> tuple[PricePoint, ...]:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise PriceDataUnavailable(f"未知标的 {asset_id}")
        fields = _SERIES.get(asset.asset_type)
        if fields is None:
            raise PriceDataUnavailable(
                f"{asset.asset_type} 没有可用于结算的价格序列"
            )
        dataset, basis = fields
        try:
            response = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset_id),
                    dataset=dataset,
                    start_date=start,
                    end_date=end,
                    as_of_date=end,
                    allow_remote=self.allow_remote,
                    provider_timeout_seconds=self.provider_timeout_seconds,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise PriceDataUnavailable(str(exc)) from exc
        if response.error:
            raise PriceDataUnavailable(response.error)
        records = [
            record
            for record in response.records
            if not record.get("estimated")
        ]
        if dataset == DATASET_OPEN_FUND_NAV:
            levels = adjusted_nav_levels(
                item for item in records if str(item.get("nav_date") or "") <= end
            )
        else:
            levels = {
                str(record.get("trade_date") or ""): record.get("close")
                for record in records
            }
        points = {}
        for day, raw in levels.items():
            if not day or day > end or raw is None:
                continue
            try:
                # Adjusted levels are floats; drop binary noise beyond 8 places.
                value = Decimal(str(raw)).quantize(_LEVEL_QUANTUM)
            except InvalidOperation:
                continue
            if value > 0:
                points[day] = PricePoint(day, value, basis)
        return tuple(points[day] for day in sorted(points))
