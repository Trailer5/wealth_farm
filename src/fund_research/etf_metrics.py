"""Evidence-backed ETF premium and expense metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.factor_engineering import FactorValue, SQLiteFactorRepository
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    MasterDataService,
)

from .models import (
    ETFExpenseSchedule,
    ETFMarketSnapshot,
    ETFMetricSet,
)


ETF_METRIC_VERSION = "exchange_fund_metric_v1"


class ExchangeFundMetricService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        factor_repository: SQLiteFactorRepository,
    ) -> None:
        self.master_data = master_data
        self.factor_repository = factor_repository
        self.factor_repository.initialize()

    def calculate_and_store(
        self,
        *,
        asset_id: str,
        as_of_date: str,
        market: ETFMarketSnapshot,
        expenses: ETFExpenseSchedule,
    ) -> ETFMetricSet:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        if asset.asset_type != ASSET_TYPE_EXCHANGE_FUND:
            raise ValueError(
                "ETF metrics require an exchange_fund asset"
            )
        if (
            market.asset_id != asset_id
            or expenses.asset_id != asset_id
        ):
            raise ValueError(
                "Metric inputs must match the requested asset_id"
            )
        if market.as_of_date > as_of_date:
            raise ValueError(
                "Market snapshot exceeds metric as_of_date"
            )
        if expenses.effective_date > as_of_date:
            raise ValueError(
                "Expense schedule is not effective at as_of_date"
            )

        premium_discount = (
            market.market_price - market.iopv
        ) / market.iopv
        computed_at = datetime.now(timezone.utc).isoformat()
        values = (
            FactorValue(
                asset_id=asset_id,
                factor_name="premium_discount",
                as_of_date=as_of_date,
                value=float(premium_discount),
                calculation_version=ETF_METRIC_VERSION,
                parameters={
                    "market_price": format(
                        market.market_price,
                        "f",
                    ),
                    "iopv": format(market.iopv, "f"),
                    "market_snapshot_date": (
                        market.as_of_date
                    ),
                    "unit": "ratio",
                },
                input_start_date=market.as_of_date,
                input_end_date=market.as_of_date,
                source_dataset=market.source,
                computed_at=computed_at,
            ),
            FactorValue(
                asset_id=asset_id,
                factor_name="expense_ratio",
                as_of_date=as_of_date,
                value=float(
                    expenses.comprehensive_expense_ratio
                ),
                calculation_version=ETF_METRIC_VERSION,
                parameters={
                    "management_fee_rate": format(
                        expenses.management_fee_rate,
                        "f",
                    ),
                    "custody_fee_rate": format(
                        expenses.custody_fee_rate,
                        "f",
                    ),
                    "effective_date": expenses.effective_date,
                    "source_url": expenses.source_url,
                    "unit": "annual_ratio",
                },
                input_start_date=expenses.effective_date,
                input_end_date=as_of_date,
                source_dataset=expenses.source,
                computed_at=computed_at,
            ),
        )
        self.factor_repository.upsert_values(list(values))
        return ETFMetricSet(
            asset_id=asset_id,
            as_of_date=as_of_date,
            values=values,
        )


def percent_to_ratio(value: object) -> Decimal:
    """Convert a provider percentage number to a decimal ratio."""

    return Decimal(str(value)) / Decimal("100")
