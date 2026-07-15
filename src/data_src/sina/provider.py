"""Sina finance exchange-fund data source skeleton."""

from __future__ import annotations

import re
from typing import Any

from src.data_src.common import to_optional_float
from src.data_src.exchange_fund import identify_exchange_fund, to_exchange_symbol
from src.data_src.http_client import UrlLibHttpClient


class SinaExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Sina public quote endpoint."""

    source = "sina"
    QUOTE_URL = "https://hq.sinajs.cn/list="

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": "https://finance.sina.com.cn"})

    def get_exchange_fund_spot(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Fetch spot records for specific ETF/LOF/closed-end fund symbols."""

        if not symbols:
            return []
        query = ",".join(to_exchange_symbol(symbol) for symbol in symbols)
        text = self.http.get_text(f"{self.QUOTE_URL}{query}")
        return [self._spot_from_line(line) for line in text.split(";") if line.strip()]

    def _spot_from_line(self, line: str) -> dict[str, Any]:
        match = re.search(r'var hq_str_([a-z]{2}\d{6})="(.*)"', line)
        if not match:
            return {"source": self.source, "raw": line}
        exchange_symbol = match.group(1)
        fields = match.group(2).split(",")
        code = exchange_symbol[-6:]
        name = _field(fields, 0)
        identity = identify_exchange_fund(code, name=name)
        return {
            "symbol": code,
            "name": name,
            "fund_type": identity.fund_type,
            "exchange": identity.exchange,
            "open": to_optional_float(_field(fields, 1)),
            "pre_close": to_optional_float(_field(fields, 2)),
            "latest": to_optional_float(_field(fields, 3)),
            "high": to_optional_float(_field(fields, 4)),
            "low": to_optional_float(_field(fields, 5)),
            "volume": to_optional_float(_field(fields, 8)),
            "amount": to_optional_float(_field(fields, 9)),
            "source": self.source,
            "raw": line,
        }


def _field(fields: list[str], index: int) -> str | None:
    return fields[index] if index < len(fields) else None
