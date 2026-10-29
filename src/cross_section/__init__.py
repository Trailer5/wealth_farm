"""On-demand A-share cross-sections: snapshot, valuations, report-period financials."""

from .derived import DERIVED_BASIS_NOTE, derived_fields, shift_quarter, single_quarter, ttm
from .service import (
    FINANCIAL_BASIS_NOTE,
    CrossSectionFetcher,
    CrossSectionService,
    CrossSectionUnavailable,
    ProviderCrossSectionFetcher,
)
from .sqlite_repository import SQLiteCrossSectionRepository

__all__ = [
    "DERIVED_BASIS_NOTE",
    "FINANCIAL_BASIS_NOTE",
    "CrossSectionFetcher",
    "CrossSectionService",
    "CrossSectionUnavailable",
    "ProviderCrossSectionFetcher",
    "SQLiteCrossSectionRepository",
    "derived_fields",
    "shift_quarter",
    "single_quarter",
    "ttm",
]
