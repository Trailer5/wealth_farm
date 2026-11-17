"""Nasdaq quote API: US listing identity and split-adjusted daily bars.

The historical endpoint adjusts for splits but not dividends, so the reference close is the
previous close; this source is the cross-check behind Sina. Design notes:
docs/modules/1-数据/数据获取底层接口模块.md「美股日线」.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

from src.data_src.common import DataSourceError, normalize_us_symbol
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar, SecurityInfo

INFO_URL = "https://api.nasdaq.com/api/quote/{symbol}/info"
HISTORY_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "application/json",
}
# Nasdaq asset classes and the project asset types they map to.
ASSET_CLASSES = (("stocks", "stock"), ("etf", "exchange_fund"))


class NasdaqDataSource:
    source = "nasdaq_us"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers=_HEADERS)

    def get_listing(self, symbol: str) -> SecurityInfo:
        """Name, exchange and asset type of a US listing; tries stocks, then ETFs."""

        code = normalize_us_symbol(symbol)
        # The info endpoint now and then answers with empty data for a valid symbol; ask twice.
        for _ in range(2):
            for asset_class, asset_type in ASSET_CLASSES:
                payload = self.http.get_json(INFO_URL.format(symbol=code), {"assetclass": asset_class})
                info = parse_listing(payload, code, asset_type)
                if info is not None:
                    return info
            time.sleep(1.0)
        raise DataSourceError(f"Nasdaq has no US listing for {code}")

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        *,
        asset_type: str = "stock",
    ) -> list[DailyBar]:
        code = normalize_us_symbol(symbol)
        asset_class = "etf" if asset_type == "exchange_fund" else "stocks"
        payload = self.http.get_json(
            HISTORY_URL.format(symbol=code),
            {
                "assetclass": asset_class,
                # One wide request: narrow windows sometimes come back empty.
                "fromdate": min(start_date[:10], (date.fromisoformat(end_date[:10]) - timedelta(days=90)).isoformat()),
                "todate": end_date[:10],
                "limit": "9999",
            },
        )
        return parse_history(payload, code, start_date, end_date, source=self.source)


def parse_listing(payload: dict[str, Any], symbol: str, asset_type: str) -> SecurityInfo | None:
    data = payload.get("data") or {}
    name = data.get("companyName")
    if not name:
        return None
    return SecurityInfo(
        symbol=symbol,
        name=str(name),
        security_type=asset_type,
        exchange=_exchange(str(data.get("exchange") or "")),
        source="nasdaq_us",
        raw={key: data.get(key) for key in ("symbol", "companyName", "stockType", "exchange", "assetClass")},
    )


def parse_history(
    payload: dict[str, Any],
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    source: str = "nasdaq_us",
) -> list[DailyBar]:
    rows = ((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(str(row["date"]), "%m/%d/%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        close = _money(row.get("close"))
        if close is None:
            continue
        parsed.append((day, row, close))
    parsed.sort(key=lambda item: item[0])
    output = []
    previous_close = None
    for day, row, close in parsed:
        if day > end_date[:10]:
            break
        if day >= start_date[:10]:
            output.append(
                DailyBar(
                    symbol=symbol,
                    trade_date=day,
                    open=_money(row.get("open")),
                    high=_money(row.get("high")),
                    low=_money(row.get("low")),
                    close=close,
                    volume=_money(row.get("volume")),
                    amount=None,
                    source=f"{source}:split_adjusted",
                    raw=dict(row),
                    preclose=previous_close,
                )
            )
        previous_close = close
    return output


def _money(value: Any) -> float | None:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _exchange(text: str) -> str | None:
    upper = text.upper()
    if upper.startswith("NASDAQ"):
        return "nasdaq"
    if "ARCA" in upper:
        return "nyse_arca"
    if "AMERICAN" in upper or upper.startswith("AMEX"):
        return "nyse_american"
    if upper.startswith("NYSE"):
        return "nyse"
    return upper.lower() or None
