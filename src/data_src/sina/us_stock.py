"""Sina US daily bars with the ex-rights reference close implied by Sina forward-adjust factors.

Sina publishes raw daily bars and, per ticker, a forward-adjust table: from each date on,
adjusted = raw * f + c, where f converts older raw prices to today's share units (splits) and
c subtracts the cash dividends paid later (in today's units). Between two dates the reference
close of day t in day-t raw units is therefore

    preclose_t = (close_{t-1} * f_{t-1} - (c_t - c_{t-1})) / f_t,

which is close_{t-1} on ordinary days, close_{t-1} - dividend on ex-dividend days and
close_{t-1} / ratio on split days. Design notes: docs/modules/1-数据/数据获取底层接口模块.md「美股日线」.
"""

from __future__ import annotations

import bisect
import json
from typing import Any

from src.data_src.common import DataSourceError, normalize_us_symbol, to_optional_float
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar

DAILY_URL = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_x=/US_MinKService.getDailyK"
QFQ_URL = "https://finance.sina.com.cn/us_stock/company/reinstatement/{symbol}_qfq.js"


class SinaUsStockDataSource:
    source = "sina_us"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": "https://finance.sina.com.cn"})

    def get_total_return_bars(self, symbol: str, start_date: str, end_date: str) -> list[DailyBar]:
        """Raw bars in [start_date, end_date] carrying the reference close; works for stocks and ETFs."""

        code = normalize_us_symbol(symbol)
        rows = parse_daily_k(self.http.get_text(DAILY_URL, {"symbol": code.lower()}))
        if not rows:
            raise DataSourceError(f"Sina returned no US daily bars for {code}")
        factors = parse_qfq_factors(self.http.get_text(QFQ_URL.format(symbol=code)))
        return bars_with_qfq_preclose(code, rows, factors, start_date, end_date, source=self.source)


def parse_daily_k(text: str) -> list[dict[str, Any]]:
    """Rows of the JSONP daily-K payload, ascending by date."""

    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise DataSourceError("Unexpected Sina US daily-K payload")
    payload = json.loads(text[start + 1:end] or "null") or []
    return sorted((row for row in payload if row.get("d")), key=lambda row: row["d"])


def parse_qfq_factors(text: str) -> list[tuple[str, float, float]]:
    """(effective date, f, c) ascending; an empty table means no corporate actions."""

    start = text.find("{")
    if start < 0:
        raise DataSourceError("Unexpected Sina US adjust-factor payload")
    payload, _ = json.JSONDecoder().raw_decode(text[start:])
    factors = []
    for item in payload.get("data") or ():
        day, f, c = str(item.get("d") or "")[:10], to_optional_float(item.get("f")), to_optional_float(item.get("c"))
        if day and f and c is not None:
            factors.append((day, f, c))
    return sorted(factors)


def bars_with_qfq_preclose(
    symbol: str,
    rows: list[dict[str, Any]],
    factors: list[tuple[str, float, float]],
    start_date: str,
    end_date: str,
    *,
    source: str = "sina_us",
) -> list[DailyBar]:
    dates = [item[0] for item in factors]

    def factor_on(day: str) -> tuple[float, float]:
        if not factors:
            return 1.0, 0.0
        index = bisect.bisect_right(dates, day) - 1
        _, f, c = factors[max(index, 0)]
        return f, c

    output = []
    previous: dict[str, Any] | None = None
    for row in rows:
        day = str(row["d"])[:10]
        close = to_optional_float(row.get("c"))
        if day > end_date[:10]:
            break
        preclose = None
        if previous is not None and close:
            f_prev, c_prev = factor_on(str(previous["d"])[:10])
            f_now, c_now = factor_on(day)
            preclose = (float(previous["c"]) * f_prev - (c_now - c_prev)) / f_now
        if close:
            previous = row
        if day < start_date[:10] or not close:
            continue
        output.append(
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=to_optional_float(row.get("o")),
                high=to_optional_float(row.get("h")),
                low=to_optional_float(row.get("l")),
                close=close,
                volume=to_optional_float(row.get("v")),
                amount=to_optional_float(row.get("a")),
                source=f"{source}:qfq_preclose",
                raw=dict(row),
                preclose=preclose if preclose and preclose > 0 else None,
            )
        )
    return output
