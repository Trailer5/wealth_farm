"""Service boundary for ordinary-company fundamental analysis."""

from __future__ import annotations

from decimal import Decimal

from src.financial_data import FinancialResearchBundle
from src.master_data import ASSET_TYPE_STOCK, MasterDataService
from src.research_contracts import PROFILE_ORDINARY_COMPANY

from .assembler import FundamentalSnapshotAssembler
from .calculator import (
    FundamentalMetricCalculator,
    FundamentalMetricParameters,
)
from .models import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    FundamentalMetricSet,
)
from .sqlite_repository import SQLiteFundamentalMetricRepository


class FundamentalAnalysisService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        repository: SQLiteFundamentalMetricRepository,
        assembler: FundamentalSnapshotAssembler | None = None,
        calculator: FundamentalMetricCalculator | None = None,
    ) -> None:
        self.master_data = master_data
        self.repository = repository
        self.assembler = assembler or FundamentalSnapshotAssembler()
        self.calculator = calculator or FundamentalMetricCalculator()
        self.repository.initialize()

    def analyze(
        self,
        bundle: FinancialResearchBundle,
        *,
        market_price: Decimal,
        shares_override: Decimal | None = None,
        currency: str = "CNY",
        profile_id: str = PROFILE_ORDINARY_COMPANY,
        parameters: FundamentalMetricParameters | None = None,
        target_report_period: str | None = None,
    ) -> FundamentalMetricSet:
        asset = self.master_data.get_asset(bundle.asset.asset_id)
        if asset is None:
            raise KeyError(
                f"Unknown asset_id: {bundle.asset.asset_id}"
            )
        if asset.asset_id != bundle.asset.asset_id:
            raise ValueError("Financial bundle asset mismatch")
        if profile_id != PROFILE_ORDINARY_COMPANY:
            raise ValueError(
                f"{FUNDAMENTAL_CALCULATION_VERSION} only supports "
                "ordinary_company"
            )
        if asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(
                f"Research profile {profile_id} does not support "
                f"asset_type {asset.asset_type}"
            )
        if not bundle.complete:
            raise ValueError(
                "Financial research bundle must be complete"
            )
        snapshot = self.assembler.assemble(
            bundle,
            market_price=market_price,
            shares_override=shares_override,
            currency=currency,
            target_report_period=target_report_period,
        )
        result = self.calculator.calculate(
            snapshot,
            parameters=parameters,
        )
        self.repository.upsert_results(list(result.metrics))
        return result

    def list_results(
        self,
        asset_id: str,
        *,
        metric_name: str | None = None,
        as_of_date: str | None = None,
    ) -> list[FundamentalMetricResult]:
        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        return self.repository.list_results(
            asset_id,
            metric_name=metric_name,
            as_of_date=as_of_date,
        )

    def analyze_history(
        self,
        bundle: FinancialResearchBundle,
        *,
        market_prices_by_cutoff: dict[str, Decimal],
        shares_override: Decimal | None = None,
        currency: str = "CNY",
        profile_id: str = PROFILE_ORDINARY_COMPANY,
        parameters: FundamentalMetricParameters | None = None,
    ) -> tuple[FundamentalMetricSet, ...]:
        if not market_prices_by_cutoff:
            raise ValueError(
                "market_prices_by_cutoff must not be empty"
            )
        results = []
        for cutoff_date in sorted(market_prices_by_cutoff):
            filtered = bundle.filtered_as_of(cutoff_date)
            if not filtered.complete:
                raise ValueError(
                    "Financial bundle is incomplete at cutoff: "
                    f"{cutoff_date}"
                )
            results.append(
                self.analyze(
                    filtered,
                    market_price=market_prices_by_cutoff[
                        cutoff_date
                    ],
                    shares_override=shares_override,
                    currency=currency,
                    profile_id=profile_id,
                    parameters=parameters,
                )
            )
        return tuple(results)
