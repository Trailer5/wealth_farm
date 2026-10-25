"""Official China Securities Index historical-performance source."""

from __future__ import annotations

from typing import Any

from src.data_src.common import normalize_date_compact, to_optional_float
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar


class CsIndexDataSource:
    """Fetch daily index levels from the public CSIndex endpoint."""

    source = "csindex"
    HISTORY_URL = (
        "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    )

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.csindex.com.cn/",
            },
            retry_count=3,
        )

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
    ) -> list[DailyBar]:
        if exchange.strip().lower() not in {"sh", "sz", "csi"}:
            raise ValueError(
                "CSIndex source supports sh, sz, and csi indices"
            )
        code = symbol.strip().upper()
        payload = self.http.get_json(
            self.HISTORY_URL,
            {
                "indexCode": code,
                "startDate": normalize_date_compact(start_date),
                "endDate": normalize_date_compact(end_date),
            },
        )
        if str(payload.get("code") or "") != "200":
            raise RuntimeError(
                f"CSIndex request failed: {payload.get('msg')}"
            )
        bars = []
        for row in payload.get("data") or []:
            returned_code = str(row.get("indexCode") or "").upper()
            if returned_code != code:
                continue
            trade_date = _compact_date(row.get("tradeDate"))
            close = to_optional_float(row.get("close"))
            if not trade_date or close is None:
                continue
            bars.append(
                DailyBar(
                    symbol=code,
                    trade_date=trade_date,
                    open=to_optional_float(row.get("open")),
                    high=to_optional_float(row.get("high")),
                    low=to_optional_float(row.get("low")),
                    close=close,
                    volume=to_optional_float(row.get("tradingVol")),
                    amount=to_optional_float(row.get("tradingValue")),
                    source=f"{self.source}:index_perf",
                    raw=dict(row),
                )
            )
        bars.sort(key=lambda item: item.trade_date)
        return bars


def _compact_date(value: object | None) -> str:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"
