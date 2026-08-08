"""Batch stock-industry metadata from Eastmoney quote lists."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from src.data_src.models import StockIndustryInfo


class EastmoneyStockIndustryDataSource:
    source = "eastmoney:ulist_industry"
    URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or _IndustryHttpClient(
            {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0",
            }
        )

    def get_stock_industries(
        self,
        symbols: list[str],
        *,
        batch_size: int = 20,
        batch_delay_seconds: float = 0.2,
    ) -> list[StockIndustryInfo]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if batch_delay_seconds < 0:
            raise ValueError(
                "batch_delay_seconds must not be negative"
            )
        normalized = list(dict.fromkeys(str(value) for value in symbols))
        output = []
        for offset in range(0, len(normalized), batch_size):
            batch = normalized[offset : offset + batch_size]
            payload = self.http.get_json(
                self.URL,
                {
                    "secids": ",".join(
                        _secid(symbol) for symbol in batch
                    ),
                    "fields": "f12,f14,f100",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                },
            )
            rows = ((payload.get("data") or {}).get("diff") or [])
            output.extend(
                StockIndustryInfo(
                    symbol=str(row.get("f12") or ""),
                    name=str(row.get("f14") or ""),
                    industry_name=str(row.get("f100") or ""),
                    source=self.source,
                    raw=dict(row),
                )
                for row in rows
                if row.get("f12") and row.get("f100")
            )
            if offset + batch_size < len(normalized):
                time.sleep(batch_delay_seconds)
        return output


def _secid(symbol: str) -> str:
    value = symbol.strip()
    if len(value) != 6 or not value.isdigit():
        raise ValueError(f"Unsupported stock symbol: {symbol}")
    market = "1" if value.startswith(("5", "6", "9")) else "0"
    return f"{market}.{value}"


class _IndustryHttpClient:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers

    def get_json(
        self,
        url: str,
        params: dict[str, object],
    ) -> dict[str, Any]:
        full_url = (
            f"{url}?{urllib.parse.urlencode(params)}"
        )
        last_error = None
        for attempt in range(4):
            try:
                request = urllib.request.Request(
                    full_url,
                    headers=self.headers,
                )
                with urllib.request.urlopen(
                    request,
                    timeout=20,
                ) as response:
                    return json.loads(
                        response.read().decode("utf-8")
                    )
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
        raise last_error or RuntimeError(
            "Industry request failed"
        )
