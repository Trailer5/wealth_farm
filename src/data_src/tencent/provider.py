"""Tencent finance exchange-fund data source skeleton."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.data_src.common import normalize_a_share_code, to_optional_float
from src.data_src.exchange_fund import identify_exchange_fund, to_exchange_symbol
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar


class TencentExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Tencent public quote endpoint."""

    source = "tencent"
    QUOTE_URL = "https://qt.gtimg.cn/q="
    KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()

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
        """Fetch unadjusted A-share daily bars from Tencent."""

        return self._get_daily_bars(symbol, start_date, end_date)

    def get_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        """Fetch unadjusted ETF daily bars from Tencent."""

        return self._get_daily_bars(symbol, start_date, end_date)

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
    ) -> list[DailyBar]:
        """Fetch mainland index daily bars with explicit exchange."""

        return self._get_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange=exchange,
        )

    def _get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str | None = None,
    ) -> list[DailyBar]:
        code = normalize_a_share_code(symbol)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        count = max(20, int((end - start).days * 5 / 7) + 15)
        if count > 640:
            raise ValueError(
                "Tencent baseline provider supports up to 640 daily bars"
            )
        exchange_symbol = (
            f"{exchange.lower()}{code}"
            if exchange is not None
            else to_exchange_symbol(code)
        )
        payload = self.http.get_json(
            self.KLINE_URL,
            params={
                "param": (
                    f"{exchange_symbol},day,{start.isoformat()},"
                    f"{end.isoformat()},{count},"
                )
            },
        )
        item = (payload.get("data") or {}).get(exchange_symbol) or {}
        rows = item.get("day") or item.get("qfqday") or []
        bars = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            trade_date = str(row[0])
            if trade_date < start.isoformat() or trade_date > end.isoformat():
                continue
            bars.append(
                DailyBar(
                    symbol=code,
                    trade_date=trade_date,
                    open=to_optional_float(row[1]),
                    close=to_optional_float(row[2]),
                    high=to_optional_float(row[3]),
                    low=to_optional_float(row[4]),
                    volume=_scale(to_optional_float(row[5]), 100.0),
                    # fqkline puts a dividend-event dict, not the amount, in column 7.
                    amount=(
                        to_optional_float(row[6])
                        if len(row) > 6 and not isinstance(row[6], dict)
                        else None
                    ),
                    source=f"{self.source}:kline",
                    raw={"row": list(row)},
                )
            )
        return bars

    def get_stock_quote_valuations(
        self,
        exchange_symbols: list[str],
        *,
        batch_size: int = 60,
    ) -> list[dict[str, Any]]:
        """Latest price and valuation for many stocks, e.g. ``sh600519``."""

        records = []
        for start in range(0, len(exchange_symbols), batch_size):
            batch = exchange_symbols[start : start + batch_size]
            text = self.http.get_text(f"{self.QUOTE_URL}{','.join(batch)}")
            for line in text.split(";"):
                match = re.search(r'v_([a-z]{2})(\d{6})="(.*)"', line)
                if not match or not match.group(3):
                    continue
                fields = match.group(3).split("~")
                quote_time = _field(fields, 30) or ""
                records.append(
                    {
                        "symbol": match.group(2),
                        "exchange": match.group(1),
                        "close": to_optional_float(_field(fields, 3)),
                        "pre_close": to_optional_float(_field(fields, 4)),
                        "pct_change": to_optional_float(_field(fields, 32)),
                        "amount": _scale(to_optional_float(_field(fields, 37)), 10000.0),
                        "turnover_rate_percent": to_optional_float(_field(fields, 38)),
                        # Field 39 matches Eastmoney PE(TTM), 53 static PE, 46 PB (checked 2026-10-26).
                        "pe_ttm": to_optional_float(_field(fields, 39)),
                        "pe_static": to_optional_float(_field(fields, 53)),
                        "pb": to_optional_float(_field(fields, 46)),
                        "float_market_cap": _scale(to_optional_float(_field(fields, 44)), 1e8),
                        "market_cap": _scale(to_optional_float(_field(fields, 45)), 1e8),
                        "quote_date": (
                            f"{quote_time[:4]}-{quote_time[4:6]}-{quote_time[6:8]}"
                            if len(quote_time) >= 8
                            else None
                        ),
                        "source": f"{self.source}:quote",
                    }
                )
        return records

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


def _parse_date(value: str) -> date:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text)
