"""Tonghuashun (10jqka) public daily K-line files."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ..common import DataSourceError, normalize_a_share_code, to_optional_float
from ..http_client import UrlLibHttpClient
from ..models import DailyBar


class ThsDataSource:
    """Unadjusted daily bars from yearly 10jqka K-line files."""

    source = "ths"
    KLINE_URL = "http://d.10jqka.com.cn/v6/line/hs_{code}/00/{year}.js"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://stockpage.10jqka.com.cn/",
            },
        )

    def get_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        code = normalize_a_share_code(symbol)
        return self._get_daily_bars(code, code, start_date, end_date)

    def get_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        return self.get_a_share_daily_bars(symbol, start_date, end_date)

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
    ) -> list[DailyBar]:
        code = normalize_a_share_code(symbol)
        return self._get_daily_bars(
            code,
            _index_code(code, exchange),
            start_date,
            end_date,
        )

    def _get_daily_bars(
        self,
        symbol: str,
        ths_code: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        if start > end:
            raise ValueError("start_date must not be after end_date")
        bars = []
        for year in range(start.year, end.year + 1):
            text = self.http.get_text(
                self.KLINE_URL.format(code=ths_code, year=year)
            )
            for row in _rows(text):
                trade_date = f"{row[0][:4]}-{row[0][4:6]}-{row[0][6:8]}"
                if not start.isoformat() <= trade_date <= end.isoformat():
                    continue
                close = to_optional_float(row[4]) if len(row) > 4 else None
                # The current session appears as a placeholder row without OHLC.
                if close is None or not row[1]:
                    continue
                bars.append(
                    DailyBar(
                        symbol=symbol,
                        trade_date=trade_date,
                        open=to_optional_float(row[1]),
                        high=to_optional_float(row[2]),
                        low=to_optional_float(row[3]),
                        close=close,
                        volume=to_optional_float(row[5]) if len(row) > 5 else None,
                        amount=to_optional_float(row[6]) if len(row) > 6 else None,
                        source=f"{self.source}:kline",
                        raw={"row": row},
                    )
                )
        return bars


def _rows(text: str) -> list[list[str]]:
    match = re.search(r"\((\{.*\})\)", text, re.S)
    if match is None:
        raise DataSourceError("10jqka K-line response is not JSONP")
    data = str(json.loads(match.group(1)).get("data") or "")
    return [line.split(",") for line in data.split(";") if line.strip()]


def _index_code(code: str, exchange: str) -> str:
    normalized = exchange.strip().lower()
    if normalized == "sz" and code.startswith("399"):
        return code
    if normalized in {"sh", "csi"} and code.startswith("000"):
        return "1A0001" if code == "000001" and normalized == "sh" else f"1B{code[2:]}"
    raise DataSourceError(f"10jqka has no known index code for {exchange}{code}")
