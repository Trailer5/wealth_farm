"""Investment thesis: a few observable company metrics with expected ranges, tracked after the decision.

Design notes: docs/modules/5-研究产物/观点账本模块.md, “投资论点”.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ._checks import (
    optional_text,
    require_date,
    require_decimal,
    require_instance,
    require_items,
    require_text,
    require_texts,
)
from .artifacts import ClaimComparison, Confidence, ViewOrigin
from .codec import from_payload, to_payload
from .tasks import ResearchHorizon

MAX_THESIS_INDICATORS = 5
# Statutory latest disclosure date of each quarter-end report (CSRC rules).
_DISCLOSURE_DEADLINES = {"03-31": "04-30", "06-30": "08-31", "09-30": "10-31", "12-31": "04-30"}


def disclosure_deadline(period_end: str) -> str:
    """Latest date the report for a quarter-end period must be published."""

    require_date(period_end, "period_end")
    suffix = period_end[5:]
    if suffix not in _DISCLOSURE_DEADLINES:
        raise ValueError("observe_period must be a quarter end (03-31, 06-30, 09-30, 12-31)")
    year = int(period_end[:4]) + (1 if suffix == "12-31" else 0)
    return f"{year}-{_DISCLOSURE_DEADLINES[suffix]}"


@dataclass(frozen=True)
class ThesisIndicator:
    """Expected range of one metric for one future report period, with an optional invalidation line."""

    indicator_id: str
    metric: str
    label: str
    observe_period: str
    expected_low: Decimal | None = None
    expected_high: Decimal | None = None
    invalidation_comparison: ClaimComparison | None = None
    invalidation_threshold: Decimal | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        require_text(self.indicator_id, "indicator_id")
        require_text(self.metric, "indicator.metric")
        require_text(self.label, "indicator.label")
        disclosure_deadline(self.observe_period)
        optional_text(self.rationale, "indicator.rationale")
        if self.expected_low is None and self.expected_high is None:
            raise ValueError(f"{self.indicator_id}: expected_low or expected_high is required")
        for value, name in ((self.expected_low, "expected_low"), (self.expected_high, "expected_high")):
            if value is not None:
                require_decimal(value, name)
        if (
            self.expected_low is not None
            and self.expected_high is not None
            and self.expected_low >= self.expected_high
        ):
            raise ValueError(f"{self.indicator_id}: expected_low must be below expected_high")
        if (self.invalidation_comparison is None) != (self.invalidation_threshold is None):
            raise ValueError(f"{self.indicator_id}: invalidation needs both comparison and threshold")
        if self.invalidation_comparison is None:
            return
        require_instance(self.invalidation_comparison, ClaimComparison, "invalidation_comparison")
        require_decimal(self.invalidation_threshold, "invalidation_threshold")
        threshold = self.invalidation_threshold
        if self.invalidation_comparison is ClaimComparison.BELOW:
            if self.expected_low is not None and threshold > self.expected_low:
                raise ValueError(f"{self.indicator_id}: a below-invalidation must not exceed expected_low")
        elif self.invalidation_comparison is ClaimComparison.ABOVE:
            if self.expected_high is not None and threshold < self.expected_high:
                raise ValueError(f"{self.indicator_id}: an above-invalidation must not be under expected_high")
        else:
            raise ValueError("invalidation_comparison must be above or below")

    def invalidated_by(self, value: Decimal) -> bool:
        if self.invalidation_comparison is ClaimComparison.BELOW:
            return value < self.invalidation_threshold
        if self.invalidation_comparison is ClaimComparison.ABOVE:
            return value > self.invalidation_threshold
        return False

    def within_expected(self, value: Decimal) -> bool:
        return (self.expected_low is None or value >= self.expected_low) and (
            self.expected_high is None or value <= self.expected_high
        )


@dataclass(frozen=True)
class InvestmentThesis:
    """Why the position is held, as metrics that later reports can confirm or contradict.

    ``reconstructed`` marks a thesis rebuilt from the user's recollection after buying
    outside the system; it is tracked forward from ``as_of_date`` like any other.
    """

    thesis_id: str
    asset_id: str
    origin: ViewOrigin
    summary: str
    as_of_date: str
    expires_on: str
    horizon: ResearchHorizon
    indicators: tuple[ThesisIndicator, ...]
    confidence: Confidence = Confidence.MEDIUM
    user_id: str | None = None
    reconstructed: bool = False
    original_date: str | None = None
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.thesis_id, "thesis_id")
        require_text(self.asset_id, "asset_id")
        require_instance(self.origin, ViewOrigin, "origin")
        require_text(self.summary, "summary")
        start = require_date(self.as_of_date, "as_of_date")
        expiry = require_date(self.expires_on, "expires_on")
        if expiry <= start:
            raise ValueError("expires_on must be after as_of_date")
        require_instance(self.horizon, ResearchHorizon, "horizon")
        require_instance(self.confidence, Confidence, "confidence")
        require_items(self.indicators, ThesisIndicator, "indicators", nonempty=True)
        if len(self.indicators) > MAX_THESIS_INDICATORS:
            raise ValueError(f"a thesis tracks at most {MAX_THESIS_INDICATORS} indicators")
        ids = [item.indicator_id for item in self.indicators]
        if len(set(ids)) != len(ids):
            raise ValueError("indicator_id must be unique within a thesis")
        for item in self.indicators:
            deadline = date.fromisoformat(disclosure_deadline(item.observe_period))
            if deadline <= start:
                raise ValueError(
                    f"{item.indicator_id}: {item.observe_period} 期报告在 {deadline} 前已披露，论点只能跟踪未来报告期"
                )
            if deadline > expiry:
                raise ValueError(
                    f"{item.indicator_id}: expires_on 早于 {item.observe_period} 期的披露截止日 {deadline}"
                )
        optional_text(self.user_id, "user_id")
        if self.origin is ViewOrigin.USER and self.user_id is None:
            raise ValueError("user theses require user_id")
        if self.origin is ViewOrigin.SYSTEM and self.user_id is not None:
            raise ValueError("system theses must not carry user_id")
        if not isinstance(self.reconstructed, bool):
            raise TypeError("reconstructed must be a bool")
        if self.reconstructed and self.origin is not ViewOrigin.USER:
            raise ValueError("a reconstructed thesis records the user's recollection")
        if self.original_date is not None:
            if not self.reconstructed:
                raise ValueError("original_date is only recorded for reconstructed theses")
            if require_date(self.original_date, "original_date") > start:
                raise ValueError("original_date must not be after as_of_date")
        require_texts(self.source_refs, "source_refs")

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InvestmentThesis":
        return from_payload(cls, payload)
