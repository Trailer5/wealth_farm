"""Tencent finance exchange-fund data source skeleton."""

from __future__ import annotations

import re
from typing import Any

from src.data_src.common import to_optional_float
from src.data_src.exchange_fund import identify_exchange_fund, to_exchange_symbol
from src.data_src.http_client import UrlLibHttpClient


class TencentExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Tencent public quote endpoint."""

    source = "tencent"
    QUOTE_URL = "https://qt.gtimg.cn/q="

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()

    def get_exchange_fund_spot(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Fetch spot records for specific ETF/LOF/closed-end fund symbols."""

        if not symbols:
            return []
        query = ",".join(to_exchange_symbol(symbol) for symbol in symbols)
        text = self.http.get_text(f"{self.QUOTE_URL}{query}")
        return [self._spot_from_line(line) for line in text.split(";") if line.strip()]

    def _spot_from_line(self, line: str) -> dict[str, Any]:
        match = re.search(r'v_([a-z]{2}\d{6})="(.*)"', line)
        if not match:
            return {"source": self.source, "raw": line}
        exchange_symbol = match.group(1)
        fields = match.group(2).split("~")
        code = exchange_symbol[-6:]
        name = _field(fields, 1)
        identity = identify_exchange_fund(code, name=name)
        return {
            "symbol": code,
            "name": name,
            "fund_type": identity.fund_type,
            "exchange": identity.exchange,
            "latest": to_optional_float(_field(fields, 3)),
            "pre_close": to_optional_float(_field(fields, 4)),
            "open": to_optional_float(_field(fields, 5)),
            "volume": _scale(to_optional_float(_field(fields, 6)), 100.0),
            "amount": _scale(to_optional_float(_field(fields, 37)), 10000.0),
            "source": self.source,
            "raw": line,
        }


def _field(fields: list[str], index: int) -> str | None:
    return fields[index] if index < len(fields) else None


def _scale(value: float | None, factor: float) -> float | None:
    return value * factor if value is not None else None
