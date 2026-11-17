"""Holdings the user states only as shares of the portfolio (no quantities or amounts)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from src.research_contracts import ASSET_CLASS_LABELS, from_payload, to_payload

ZERO = Decimal("0")
ONE = Decimal("1")
# Stated shares rarely add up exactly; beyond this the user should restate them.
SUM_TOLERANCE = Decimal("0.02")
# Amounts in reports built from shares are per this notional total and scale with the real one.
NOTIONAL_TOTAL = 1_000_000.0


@dataclass(frozen=True)
class WeightItem:
    """One line: a registered asset (``asset_id``) or a bucket the user did not itemize (``asset_class``)."""

    label: str
    weight: Decimal
    asset_id: str | None = None
    asset_class: str | None = None
    # A registered asset whose prices stand in for a bucket (e.g. a bond ETF for "other bonds").
    proxy_asset_id: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("label must not be blank")
        if not ZERO < self.weight <= ONE:
            raise ValueError(f"{self.label}: weight must be within (0, 1]")
        if (self.asset_id is None) == (self.asset_class is None):
            raise ValueError(f"{self.label}: give exactly one of asset_id and asset_class")
        if self.asset_class is not None and self.asset_class not in ASSET_CLASS_LABELS:
            raise ValueError(f"{self.label}: unknown asset_class {self.asset_class}")
        if self.proxy_asset_id is not None and self.asset_class is None:
            raise ValueError(f"{self.label}: only a bucket takes a proxy")


@dataclass(frozen=True)
class HoldingWeights:
    """The user's holdings on one date as shares of the whole portfolio, cash included."""

    user_id: str
    as_of_date: str
    items: tuple[WeightItem, ...]
    user_statement: str
    snapshot_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be blank")
        date.fromisoformat(self.as_of_date)
        if not self.items:
            raise ValueError("at least one line is needed")
        if not self.user_statement.strip():
            raise ValueError("user_statement must keep the user's own words")
        total = sum((item.weight for item in self.items), ZERO)
        if abs(total - ONE) > SUM_TOLERANCE:
            raise ValueError(f"weights add up to {total}, not 100%; restate them with the user")
        keys = [item.asset_id or f"{item.asset_class}:{item.label}" for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("each asset or bucket appears once")
        expected = _snapshot_id(self)
        if self.snapshot_id and self.snapshot_id != expected:
            raise ValueError("snapshot_id does not match the content")
        object.__setattr__(self, "snapshot_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HoldingWeights":
        return from_payload(cls, payload)

    def total(self) -> Decimal:
        return sum((item.weight for item in self.items), ZERO)


def _snapshot_id(snapshot: HoldingWeights) -> str:
    payload = to_payload(snapshot)
    payload.pop("snapshot_id")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"weights:{digest[:16]}"
