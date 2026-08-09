"""Application service for market-wide factor calculation and storage."""

from __future__ import annotations

from .calculator import AShareMarketFactorCalculator
from .models import (
    MarketCrossSectionRow,
    MarketFactorParameters,
    MarketFactorSnapshot,
    MarketSeriesObservation,
)
from .sqlite_repository import SQLiteMarketFactorRepository


class MarketAnalysisService:
    def __init__(
        self,
        repository: SQLiteMarketFactorRepository,
        calculator: AShareMarketFactorCalculator | None = None,
    ) -> None:
        self.repository = repository
        self.calculator = calculator or AShareMarketFactorCalculator()
        self.repository.initialize()

    def compute_and_store(
        self,
        rows: list[MarketCrossSectionRow]
        | tuple[MarketCrossSectionRow, ...],
        parameters: MarketFactorParameters,
    ) -> MarketFactorSnapshot:
        snapshot = self.calculator.calculate(rows, parameters)
        self.repository.upsert_values(list(snapshot.values))
        return snapshot

    def store_series_observations(
        self,
        observations: list[MarketSeriesObservation],
    ) -> int:
        return self.repository.upsert_series_observations(observations)
