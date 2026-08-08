"""Versioned candidate catalog for core broad-market ETFs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


STATE_ETF_CANDIDATE_CATALOG_VERSION = "state_etf_candidate_catalog_v1"

Exchange = Literal["sh", "sz"]
IndexFamily = Literal[
    "hs300",
    "sse50",
    "csi500",
    "csi1000",
    "csi_a500",
    "star50",
]


@dataclass(frozen=True)
class StateETFCandidate:
    """A research candidate, not evidence of state ownership."""

    symbol: str
    asset_id: str
    exchange: Exchange
    index_family: IndexFamily
    display_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or re.fullmatch(r"\d{6}", self.symbol) is None:
            raise ValueError("symbol must contain exactly six digits")
        for value, field_name in (
            (self.asset_id, "asset_id"),
            (self.display_name, "display_name"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.exchange not in {"sh", "sz"}:
            raise ValueError("exchange must be sh or sz")
        if self.index_family not in {
            "hs300",
            "sse50",
            "csi500",
            "csi1000",
            "csi_a500",
            "star50",
        }:
            raise ValueError("index_family is not supported")


CORE_BROAD_ETF_CANDIDATES = (
    StateETFCandidate(
        "510300",
        "asset:etf:510300",
        "sh",
        "hs300",
        "华泰柏瑞沪深300ETF",
    ),
    StateETFCandidate(
        "510050",
        "asset:etf:510050",
        "sh",
        "sse50",
        "华夏上证50ETF",
    ),
    StateETFCandidate(
        "510330",
        "asset:etf:510330",
        "sh",
        "hs300",
        "华夏沪深300ETF",
    ),
    StateETFCandidate(
        "510310",
        "asset:etf:510310",
        "sh",
        "hs300",
        "易方达沪深300ETF",
    ),
    StateETFCandidate(
        "510500",
        "asset:etf:510500",
        "sh",
        "csi500",
        "南方中证500ETF",
    ),
    StateETFCandidate(
        "512500",
        "asset:etf:512500",
        "sh",
        "csi500",
        "华夏中证500ETF",
    ),
    StateETFCandidate(
        "512100",
        "asset:etf:512100",
        "sh",
        "csi1000",
        "南方中证1000ETF",
    ),
    StateETFCandidate(
        "560010",
        "asset:etf:560010",
        "sh",
        "csi1000",
        "广发中证1000ETF",
    ),
    StateETFCandidate(
        "563360",
        "asset:etf:563360",
        "sh",
        "csi_a500",
        "华泰柏瑞中证A500ETF",
    ),
    StateETFCandidate(
        "588000",
        "asset:etf:588000",
        "sh",
        "star50",
        "华夏上证科创板50ETF",
    ),
    StateETFCandidate(
        "588080",
        "asset:etf:588080",
        "sh",
        "star50",
        "易方达上证科创板50ETF",
    ),
)


def _validate_catalog(candidates: tuple[StateETFCandidate, ...]) -> None:
    symbols = [candidate.symbol for candidate in candidates]
    asset_ids = [candidate.asset_id for candidate in candidates]
    if len(symbols) != len(set(symbols)):
        raise ValueError("candidate catalog symbols must be unique")
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("candidate catalog asset_ids must be unique")


_validate_catalog(CORE_BROAD_ETF_CANDIDATES)
