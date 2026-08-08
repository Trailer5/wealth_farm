"""Service for calculating, storing, and querying factors."""

from __future__ import annotations

from dataclasses import replace

from src.data_access import DataAccessResponse
from src.master_data import MasterDataService

from .calculator import MarketFactorCalculator
from .models import FactorValue
from .risk import (
    RiskCalculationResult,
    RiskMetricCalculator,
    RiskMetricParameters,
)
from .sqlite_repository import SQLiteFactorRepository


class FactorService:
    """Calculate versioned factor values for canonical assets."""

    def __init__(
        self,
        repository: SQLiteFactorRepository,
        master_data: MasterDataService,
        calculator: MarketFactorCalculator | None = None,
        risk_calculator: RiskMetricCalculator | None = None,
    ) -> None:
        self.repository = repository
        self.master_data = master_data
        self.calculator = calculator or MarketFactorCalculator()
        self.risk_calculator = (
            risk_calculator or RiskMetricCalculator()
        )
        self.repository.initialize()

    def compute_and_store(
        self,
        *,
        asset_id: str,
        records: list[dict[str, object]],
        source_dataset: str,
    ) -> list[FactorValue]:
        """Calculate all available factors and persist them."""

        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        values = self.calculator.calculate(
            asset_id=asset_id,
            records=records,
            source_dataset=source_dataset,
        )
        self.repository.upsert_values(values)
        return values

    def compute_from_response(
        self,
        response: DataAccessResponse,
    ) -> list[FactorValue]:
        """Calculate factors from a complete unified data response."""

        if not response.complete:
            raise ValueError("Data access response must be complete")
        return self.compute_and_store(
            asset_id=response.asset.asset_id,
            records=[dict(record) for record in response.records],
            source_dataset=response.dataset,
        )

    def compute_risk_and_store(
        self,
        *,
        asset_id: str,
        records: list[dict[str, object]],
        source_dataset: str,
        parameters: RiskMetricParameters | None = None,
    ) -> RiskCalculationResult:
        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        result = self.risk_calculator.calculate(
            asset_id=asset_id,
            records=records,
            source_dataset=source_dataset,
            parameters=parameters,
        )
        self.repository.upsert_values(list(result.values))
        return result

    def compute_risk_from_response(
        self,
        response: DataAccessResponse,
        *,
        parameters: RiskMetricParameters | None = None,
    ) -> RiskCalculationResult:
        if not response.complete:
            raise ValueError(
                "Data access response must be complete"
            )
        return self.compute_risk_and_store(
            asset_id=response.asset.asset_id,
            records=[dict(record) for record in response.records],
            source_dataset=response.dataset,
            parameters=parameters,
        )

    def compute_risk_windows_and_store(
        self,
        *,
        asset_id: str,
        records: list[dict[str, object]],
        source_dataset: str,
        windows: tuple[int | None, ...],
        parameters: RiskMetricParameters | None = None,
    ) -> tuple[RiskCalculationResult, ...]:
        if not windows:
            raise ValueError("windows must not be empty")
        if len(set(windows)) != len(windows):
            raise ValueError("windows must not contain duplicates")
        base = parameters or RiskMetricParameters()
        return tuple(
            self.compute_risk_and_store(
                asset_id=asset_id,
                records=records,
                source_dataset=source_dataset,
                parameters=replace(base, window=window),
            )
            for window in windows
        )

    def list_values(
        self,
        asset_id: str,
        *,
        factor_name: str | None = None,
        as_of_date: str | None = None,
    ) -> list[FactorValue]:
        """Return persisted factors for one asset."""

        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        return self.repository.list_values(
            asset_id,
            factor_name=factor_name,
            as_of_date=as_of_date,
        )
