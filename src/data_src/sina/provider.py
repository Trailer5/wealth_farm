"""Sina finance exchange-fund data source skeleton."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.data_src.common import normalize_a_share_code, to_optional_float
from src.data_src.exchange_fund import identify_exchange_fund, to_exchange_symbol
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar


class SinaExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Sina public quote endpoint."""

    source = "sina"
    QUOTE_URL = "https://hq.sinajs.cn/list="
    KLINE_URL = (
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        "CN_MarketDataService.getKLineData"
    )

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": "https://finance.sina.com.cn"})

    def get_exchange_fund_spot(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Fetch spot records for specific ETF/LOF/closed-end fund symbols."""

        if not symbols:
            return []
        query = ",".join(to_exchange_symbol(symbol) for symbol in symbols)
        text = self.http.get_text(f"{self.QUOTE_URL}{query}")
        return [self._spot_from_line(line) for line in text.split(";") if line.strip()]

    def get_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        return self._get_daily_bars(symbol, start_date, end_date)

    def get_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        return self._get_daily_bars(symbol, start_date, end_date)

    def _get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        code = normalize_a_share_code(symbol)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        count = max(20, int((end - start).days * 5 / 7) + 15)
        if count > 1023:
            raise ValueError(
                "Sina baseline provider supports up to 1023 daily bars"
            )
        rows = self.http.get_json(
            self.KLINE_URL,
            params={
                "symbol": to_exchange_symbol(code),
                "scale": 240,
                "datalen": count,
            },
        )
        if not isinstance(rows, list):
            return []
        bars = []
        for row in rows:
            trade_date = str(row.get("day", ""))[:10]
            if trade_date < start.isoformat() or trade_date > end.isoformat():
                continue
            bars.append(
                DailyBar(
                    symbol=code,
                    trade_date=trade_date,
                    open=to_optional_float(row.get("open")),
                    high=to_optional_float(row.get("high")),
                    low=to_optional_float(row.get("low")),
                    close=to_optional_float(row.get("close")),
                    volume=to_optional_float(row.get("volume")),
                    amount=to_optional_float(row.get("amount")),
                    source=f"{self.source}:kline",
                    raw=dict(row),
                )
            )
        bars.sort(key=lambda bar: bar.trade_date)
        return bars

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


def _parse_date(value: str) -> date:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text)
