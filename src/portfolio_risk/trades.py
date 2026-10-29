"""Optional log of the trades a user says they made (stage 8 decision 7).

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「交易流水与偏好校准」.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.research_contracts import from_payload, to_payload


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class TradeRecord:
    """One executed trade as the user stated it; quantity is in units, always positive."""

    user_id: str
    asset_id: str
    side: TradeSide
    trade_date: str
    quantity: Decimal
    price: Decimal | None = None
    note: str = ""
    trade_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.user_id.strip() or not self.asset_id.strip():
            raise ValueError("user_id and asset_id must not be blank")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be TradeSide")
        date.fromisoformat(self.trade_date)
        if not self.quantity > 0:
            raise ValueError("quantity must be positive")
        if self.price is not None and not self.price > 0:
            raise ValueError("price must be positive")
        expected = trade_id_for(self)
        if self.trade_id and self.trade_id != expected:
            raise ValueError("trade_id does not match the trade content")
        object.__setattr__(self, "trade_id", expected)

    @property
    def signed_quantity(self) -> float:
        return float(self.quantity) if self.side is TradeSide.BUY else -float(self.quantity)

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TradeRecord":
        return from_payload(cls, payload)


def trade_id_for(trade: TradeRecord) -> str:
    payload = to_payload(trade)
    payload.pop("trade_id")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"trade:{digest[:16]}"
