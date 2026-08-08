"""Benchmark selection, data access, and metric persistence."""

from __future__ import annotations

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.factor_engineering import SQLiteFactorRepository
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    MasterDataService,
)

from .calculator import BenchmarkMetricCalculator
from .models import (
    BenchmarkCalculationResult,
    BenchmarkKind,
    BenchmarkMetricParameters,
    BenchmarkRelation,
)
from .sqlite_repository import SQLiteBenchmarkRepository


class BenchmarkAnalysisService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        factor_repository: SQLiteFactorRepository,
        benchmark_repository: SQLiteBenchmarkRepository,
        calculator: BenchmarkMetricCalculator | None = None,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.factor_repository = factor_repository
        self.benchmark_repository = benchmark_repository
        self.calculator = calculator or BenchmarkMetricCalculator()
        self.factor_repository.initialize()
        self.benchmark_repository.initialize()

    def set_benchmark(
        self,
        *,
        asset_id: str,
        benchmark_asset_id: str,
        benchmark_kind: BenchmarkKind,
        source: str,
        effective_date: str,
        reason: str,
        is_default: bool = True,
    ) -> BenchmarkRelation:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        benchmark = self.master_data.get_asset(
            benchmark_asset_id
        )
        if benchmark is None:
            raise KeyError(
                f"Unknown benchmark_asset_id: {benchmark_asset_id}"
            )
        expected_type = (
            ASSET_TYPE_INDEX
            if benchmark_kind is BenchmarkKind.INDEX
            else ASSET_TYPE_EXCHANGE_FUND
        )
        if benchmark.asset_type != expected_type:
            raise ValueError(
                f"Benchmark kind {benchmark_kind.value} requires "
                f"asset_type {expected_type}"
            )
        return self.benchmark_repository.save_relation(
            BenchmarkRelation(
                asset_id=asset.asset_id,
                benchmark_asset_id=benchmark.asset_id,
                benchmark_kind=benchmark_kind,
                source=source,
                effective_date=effective_date,
                reason=reason,
                is_default=is_default,
            )
        )

    def compare(
        self,
        *,
        asset_id: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        benchmark_asset_id: str | None = None,
        parameters: BenchmarkMetricParameters | None = None,
        allow_remote: bool = True,
        provider_timeout_seconds: float = 30.0,
    ) -> BenchmarkCalculationResult:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        if benchmark_asset_id is None:
            relation = self.benchmark_repository.get_default(
                asset_id,
                as_of_date=as_of_date,
            )
            if relation is None:
                raise ValueError(
                    f"No default benchmark for asset: {asset_id}"
                )
        else:
            benchmark = self.master_data.get_asset(
                benchmark_asset_id
            )
            if benchmark is None:
                raise KeyError(
                    f"Unknown benchmark_asset_id: "
                    f"{benchmark_asset_id}"
                )
            if benchmark.asset_type not in {
                ASSET_TYPE_INDEX,
                ASSET_TYPE_EXCHANGE_FUND,
            }:
                raise ValueError(
                    "Explicit benchmark must be an index or ETF proxy"
                )
            relation = BenchmarkRelation(
                asset_id=asset_id,
                benchmark_asset_id=benchmark_asset_id,
                benchmark_kind=(
                    BenchmarkKind.INDEX
                    if benchmark.asset_type == ASSET_TYPE_INDEX
                    else BenchmarkKind.ETF_PROXY
                ),
                source="request_override",
                effective_date=start_date,
                reason="Explicit research task benchmark",
                is_default=False,
            )
        benchmark = self.master_data.get_asset(
            relation.benchmark_asset_id
        )
        if benchmark is None:
            raise KeyError(
                f"Unknown benchmark_asset_id: "
                f"{relation.benchmark_asset_id}"
            )
        asset_dataset = _dataset_for_asset_type(asset.asset_type)
        benchmark_dataset = _dataset_for_asset_type(
            benchmark.asset_type
        )
        effective_end = min(end_date, as_of_date)
        asset_response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=asset_dataset,
                start_date=start_date,
                end_date=effective_end,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=provider_timeout_seconds,
            )
        )
        benchmark_response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=benchmark.asset_id),
                dataset=benchmark_dataset,
                start_date=start_date,
                end_date=effective_end,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=provider_timeout_seconds,
            )
        )
        if not asset_response.complete:
            raise ValueError("Asset daily data is incomplete")
        if not benchmark_response.complete:
            raise ValueError("Benchmark daily data is incomplete")
        result = self.calculator.calculate(
            asset_id=asset.asset_id,
            benchmark_asset_id=benchmark.asset_id,
            benchmark_kind=relation.benchmark_kind,
            asset_records=[
                dict(record)
                for record in asset_response.records
            ],
            benchmark_records=[
                dict(record)
                for record in benchmark_response.records
            ],
            asset_dataset=asset_dataset,
            benchmark_dataset=benchmark_dataset,
            parameters=parameters,
        )
        self.factor_repository.upsert_values(
            list(result.values)
        )
        return result


def _dataset_for_asset_type(asset_type: str) -> str:
    mapping = {
        ASSET_TYPE_STOCK: DATASET_STOCK_DAILY_BARS,
        ASSET_TYPE_EXCHANGE_FUND: (
            DATASET_EXCHANGE_FUND_DAILY_BARS
        ),
        ASSET_TYPE_INDEX: DATASET_INDEX_DAILY_BARS,
    }
    dataset = mapping.get(asset_type)
    if dataset is None:
        raise ValueError(
            f"Unsupported benchmark asset_type: {asset_type}"
        )
    return dataset
