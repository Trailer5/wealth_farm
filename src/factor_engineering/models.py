"""Versioned factor values derived from normalized research data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FactorValue:
    asset_id: str
    factor_name: str
    as_of_date: str
    value: float
    calculation_version: str
    parameters: dict[str, Any] = field(default_factory=dict)
    input_start_date: str = ""
    input_end_date: str = ""
    source_dataset: str = ""
    computed_at: str = ""
