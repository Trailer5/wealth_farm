"""Benchmark selection, data access, and metric persistence."""

from __future__ import annotations

from typing import Any, NoReturn

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_OPEN_FUND_NAV,
    DATASET_STOCK_DAILY_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessResponse,
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
from .composite import build_composite_benchmark_records
from .models import (
    BenchmarkCalculationResult,
    BenchmarkComponentKind,
    BenchmarkKind,
    BenchmarkMetricParameters,
    BenchmarkRelation,
    CompositeBenchmarkDefinition,
)
from .sqlite_repository import SQLiteBenchmarkRepository


class BenchmarkDefinitionIncompleteError(ValueError):
    """Official benchmark text could not be fully resolved."""


class BenchmarkComponentUnavailableError(RuntimeError):
    """A required benchmark component source is unavailable."""


class BenchmarkCoverageError(ValueError):
    """Benchmark components do not have enough common history."""


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
        if benchmark_kind is BenchmarkKind.COMPOSITE:
            raise ValueError(
                "Composite benchmarks require an official definition"
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
        asset_datasets = _datasets_for_asset_type(asset.asset_type)
        benchmark_datasets = _datasets_for_asset_type(
            benchmark.asset_type
        )
        effective_end = min(end_date, as_of_date)
        asset_response = self._get_series(
            asset.asset_id,
            asset_datasets,
            start_date=start_date,
            end_date=effective_end,
            as_of_date=as_of_date,
            allow_remote=allow_remote,
            provider_timeout_seconds=provider_timeout_seconds,
        )
        benchmark_response = self._get_series(
            benchmark.asset_id,
            benchmark_datasets,
            start_date=start_date,
            end_date=effective_end,
            as_of_date=as_of_date,
            allow_remote=allow_remote,
            provider_timeout_seconds=provider_timeout_seconds,
        )
        asset_dataset = asset_response.dataset
        benchmark_dataset = benchmark_response.dataset
        if not asset_response.complete:
            raise BenchmarkComponentUnavailableError(
                "Asset daily data source is incomplete"
            )
        if not benchmark_response.complete:
            raise BenchmarkComponentUnavailableError(
                "Benchmark daily data source is incomplete"
            )
        try:
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
        except ValueError as exc:
            _raise_coverage_error(exc)
        self.factor_repository.upsert_values(
            list(result.values)
        )
        return result

    def _get_series(
        self,
        asset_id: str,
        datasets: tuple[str, ...],
        *,
        start_date: str,
        end_date: str,
        as_of_date: str,
        allow_remote: bool,
        provider_timeout_seconds: float,
    ) -> DataAccessResponse:
        """First complete series in preference order; the last one is returned as is."""

        for index, dataset in enumerate(datasets):
            request = DataAccessRequest(
                asset=AssetInput(asset_id=asset_id),
                dataset=dataset,
                start_date=start_date,
                end_date=end_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=provider_timeout_seconds,
            )
            if index == len(datasets) - 1:
                return self.data_access.get(request)
            try:
                response = self.data_access.get(request)
            except Exception:  # noqa: BLE001 - fall back to the next basis
                continue
            if response.complete and response.records:
                return response
        raise AssertionError("datasets must not be empty")

    def compare_composite(
        self,
        *,
        asset_id: str,
        asset_records: list[dict[str, Any]],
        definition: CompositeBenchmarkDefinition,
        start_date: str,
        end_date: str,
        as_of_date: str,
        parameters: BenchmarkMetricParameters | None = None,
        allow_remote: bool = True,
        provider_timeout_seconds: float = 30.0,
    ) -> BenchmarkCalculationResult:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        if definition.asset_id != asset_id:
            raise ValueError(
                "Composite benchmark definition belongs to another asset"
            )
        if not definition.resolved:
            unresolved = [
                component.label
                for component in definition.components
                if not component.resolved
            ]
            raise BenchmarkDefinitionIncompleteError(
                "Unresolved official benchmark components: "
                + ", ".join(unresolved)
            )

        index_records: dict[str, list[dict[str, Any]]] = {}
        effective_end = min(end_date, as_of_date)
        for component in definition.components:
            if component.kind is BenchmarkComponentKind.CASH_RATE:
                continue
            assert component.index_asset_id is not None
            index_asset = self.master_data.get_asset(
                component.index_asset_id
            )
            if index_asset is None:
                raise BenchmarkDefinitionIncompleteError(
                    f"Unknown benchmark component asset: {component.label}"
                )
            if index_asset.asset_type != ASSET_TYPE_INDEX:
                raise BenchmarkDefinitionIncompleteError(
                    f"Benchmark component is not an index: {component.label}"
                )
            if index_asset.exchange not in {"sh", "sz", "csi"}:
                raise BenchmarkComponentUnavailableError(
                    f"No index provider for component: {component.label}"
                )
            try:
                response = self.data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(
                            asset_id=component.index_asset_id
                        ),
                        dataset=DATASET_INDEX_DAILY_BARS,
                        start_date=start_date,
                        end_date=effective_end,
                        as_of_date=as_of_date,
                        allow_remote=allow_remote,
                        allow_stale_on_error=True,
                        remote_sources=(
                            "csindex",
                            "eastmoney",
                            "akshare",
                        ),
                        provider_timeout_seconds=(
                            provider_timeout_seconds
                        ),
                    )
                )
            except Exception as exc:
                raise BenchmarkComponentUnavailableError(
                    f"{component.label}: {type(exc).__name__}: {exc}"
                ) from exc
            if not response.complete or not response.records:
                reason = response.error or "incomplete index history"
                raise BenchmarkComponentUnavailableError(
                    f"{component.label}: {reason}"
                )
            index_records[component.index_asset_id] = [
                dict(record) for record in response.records
            ]

        try:
            aligned_asset, benchmark_records = (
                build_composite_benchmark_records(
                    asset_records=asset_records,
                    definition=definition,
                    index_records=index_records,
                )
            )
        except ValueError as exc:
            raise BenchmarkCoverageError(str(exc)) from exc
        try:
            result = self.calculator.calculate(
                asset_id=asset_id,
                benchmark_asset_id=definition.synthetic_asset_id,
                benchmark_kind=BenchmarkKind.COMPOSITE,
                asset_records=aligned_asset,
                benchmark_records=benchmark_records,
                asset_dataset=DATASET_OPEN_FUND_NAV,
                benchmark_dataset="composite_benchmark",
                parameters=parameters,
                benchmark_definition=definition,
                benchmark_metadata={
                    "benchmark_effective_date": (
                        definition.effective_date
                    ),
                    "components": [
                        {
                            "label": component.label,
                            "kind": component.kind.value,
                            "weight": str(component.weight),
                            "source": component.source,
                        }
                        for component in definition.components
                    ],
                    "cash_day_count": "actual/365_simple",
                },
            )
        except ValueError as exc:
            _raise_coverage_error(exc)
        self.factor_repository.upsert_values(list(result.values))
        return result


def _raise_coverage_error(exc: ValueError) -> NoReturn:
    if "matching observation interval" in str(exc):
        raise BenchmarkCoverageError(str(exc)) from exc
    raise exc


def _datasets_for_asset_type(asset_type: str) -> tuple[str, ...]:
    """Total-return bars first; unadjusted bars only when they are unavailable."""

    mapping = {
        ASSET_TYPE_STOCK: (
            DATASET_STOCK_TOTAL_RETURN_BARS,
            DATASET_STOCK_DAILY_BARS,
        ),
        ASSET_TYPE_EXCHANGE_FUND: (
            DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
            DATASET_EXCHANGE_FUND_DAILY_BARS,
        ),
        ASSET_TYPE_INDEX: (DATASET_INDEX_DAILY_BARS,),
    }
    datasets = mapping.get(asset_type)
    if datasets is None:
        raise ValueError(
            f"Unsupported benchmark asset_type: {asset_type}"
        )
    return datasets
