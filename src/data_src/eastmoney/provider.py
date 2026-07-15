"""Eastmoney exchange-fund data source skeleton."""

from __future__ import annotations

from typing import Any

from src.data_src.common import to_optional_float
from src.data_src.exchange_fund import identify_exchange_fund
from src.data_src.http_client import UrlLibHttpClient


class EastmoneyExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Eastmoney public endpoints."""

    source = "eastmoney"
    CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://quote.eastmoney.com/",
            },
            retry_count=3,
        )

    def get_exchange_fund_spot(self) -> list[dict[str, Any]]:
        """Fetch ETF/LOF/closed-end fund spot records.

        Eastmoney's `fs` universe string is intentionally kept in one method so
        it can be adjusted when upstream categories change.
        """

        payload = self.http.get_json(
            self.CLIST_URL,
            {
                "pn": 1,
                "pz": 5000,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "b:MK0021,b:MK0022,b:MK0023",
                "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
            },
        )
        rows = ((payload.get("data") or {}).get("diff") or [])
        return [self._spot_from_row(row) for row in rows]

    def _spot_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        code = str(row.get("f12") or "")
        name = row.get("f14")
        identity = identify_exchange_fund(code, name=name)
        return {
            "symbol": code,
            "name": name,
            "fund_type": identity.fund_type,
            "exchange": identity.exchange,
            "latest": to_optional_float(row.get("f2")),
            "pct_change": to_optional_float(row.get("f3")),
            "change": to_optional_float(row.get("f4")),
            "volume": _scale(to_optional_float(row.get("f5")), 100.0),
            "amount": to_optional_float(row.get("f6")),
            "high": to_optional_float(row.get("f15")),
            "low": to_optional_float(row.get("f16")),
            "open": to_optional_float(row.get("f17")),
            "pre_close": to_optional_float(row.get("f18")),
            "source": self.source,
            "raw": dict(row),
        }


def _scale(value: float | None, factor: float) -> float | None:
    return value * factor if value is not None else None
