"""Normalize provider index valuation rows into auditable market series."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .models import MarketSeriesObservation
from .sqlite_repository import SQLiteMarketFactorRepository


_SERIES_FIELDS = {
    "static_pe": ("pe", "market_cap_weighted_static", "multiple", "1"),
    "ttm_pe": ("pe", "market_cap_weighted_ttm", "multiple", "1"),
    "equal_weight_static_pe": ("pe", "equal_weight_static", "multiple", "1"),
    "equal_weight_ttm_pe": ("pe", "equal_weight_ttm", "multiple", "1"),
    "median_static_pe": ("pe", "median_static", "multiple", "1"),
    "median_ttm_pe": ("pe", "median_ttm", "multiple", "1"),
    "pb": ("pb", "market_cap_weighted", "multiple", "1"),
    "equal_weight_pb": ("pb", "equal_weight", "multiple", "1"),
    "median_pb": ("pb", "median", "multiple", "1"),
    "pe1": ("pe", "csindex_official_1", "multiple", "1"),
    "pe2": ("pe", "csindex_official_2", "multiple", "1"),
    "dividend_yield1_percent": (
        "dividend_yield",
        "csindex_official_1",
        "ratio",
        "0.01",
    ),
    "dividend_yield2_percent": (
        "dividend_yield",
        "csindex_official_2",
        "ratio",
        "0.01",
    ),
}


class IndexValuationSeriesService:
    """Persist separate valuation methodologies without collapsing fields."""

    def __init__(
        self,
        repository: SQLiteMarketFactorRepository,
    ) -> None:
        self.repository = repository
        self.repository.initialize()

    def store(
        self,
        *,
        scope_asset_id: str,
        records: list[dict[str, object]],
        acquired_at: str,
    ) -> tuple[MarketSeriesObservation, ...]:
        datetime.fromisoformat(
            acquired_at.replace("Z", "+00:00")
        )
        observations = []
        for record in records:
            observation_date = str(
                record.get("trade_date") or ""
            )
            source = str(record.get("source") or "")
            if not observation_date or not source:
                continue
            for field_name, (
                series_name,
                methodology,
                unit,
                scale,
            ) in _SERIES_FIELDS.items():
                raw_value = record.get(field_name)
                if raw_value is None:
                    continue
                value = Decimal(str(raw_value)) * Decimal(scale)
                if not value.is_finite() or value <= 0:
                    continue
                observations.append(
                    MarketSeriesObservation(
                        scope_asset_id=scope_asset_id,
                        series_name=series_name,
                        observation_date=observation_date,
                        available_at=observation_date,
                        value=value,
                        unit=unit,
                        methodology=methodology,
                        source=source,
                        acquired_at=acquired_at,
                        raw={
                            "index_name": record.get("index_name"),
                            "provider_field": field_name,
                        },
                    )
                )
                if series_name == "pe":
                    observations.append(
                        MarketSeriesObservation(
                            scope_asset_id=scope_asset_id,
                            series_name="earnings_yield",
                            observation_date=observation_date,
                            available_at=observation_date,
                            value=Decimal("1") / value,
                            unit="ratio",
                            methodology=methodology,
                            source=source,
                            acquired_at=acquired_at,
                            raw={
                                "index_name": record.get(
                                    "index_name"
                                ),
                                "derived_from": field_name,
                            },
                        )
                    )
        self.repository.upsert_series_observations(observations)
        return tuple(observations)
