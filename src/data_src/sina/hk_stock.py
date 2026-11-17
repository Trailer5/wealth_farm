"""Hong Kong daily bars with the reference close implied by Sina forward-adjust factors.

Sina publishes raw HK daily bars and a multiplicative forward-adjust table (qfq_factor effective from
each date, read through AkShare's ``stock_hk_daily``). With a_t the factor in force on day t, the
reference close of day t in day-t raw units is preclose_t = close_{t-1} * a_{t-1} / a_t: the previous
close on ordinary days, less the dividend on ex-dates and divided by the ratio on splits. Names come
from Tencent's quote line. Design notes: docs/modules/1-数据/数据获取底层接口模块.md「港股日线」.
"""

from __future__ import annotations

import bisect
from typing import Any

from src.data_src.common import DataSourceError
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar, SecurityInfo

QUOTE_URL = "https://qt.gtimg.cn/q=hk{symbol}"


def normalize_hk_symbol(symbol: str) -> str:
    code = symbol.strip().upper().removeprefix("HK").removesuffix(".HK")
    if not code.isdigit() or len(code) > 5:
        raise DataSourceError(f"Not a Hong Kong stock code: {symbol}")
    return code.zfill(5)


class SinaHkStockDataSource:
    source = "sina_hk"

    def get_total_return_bars(self, symbol: str, start_date: str, end_date: str) -> list[DailyBar]:
        import akshare as ak

        code = normalize_hk_symbol(symbol)
        raw = ak.stock_hk_daily(symbol=code, adjust="")
        if raw is None or raw.empty:
            raise DataSourceError(f"Sina returned no HK daily bars for {code}")
        factors = ak.stock_hk_daily(symbol=code, adjust="qfq-factor")
        rows = [
            {"d": str(row["date"])[:10], "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"], "a": row.get("amount")}
            for row in raw.to_dict("records")
        ]
        table = [] if factors is None else [(str(item["date"])[:10], float(item["qfq_factor"])) for item in factors.to_dict("records")]
        return bars_with_factor_preclose(code, rows, table, start_date, end_date, source=self.source)


def hk_listing(symbol: str, http_client: Any | None = None) -> SecurityInfo:
    code = normalize_hk_symbol(symbol)
    http = http_client or UrlLibHttpClient(headers={"Referer": "https://gu.qq.com"})
    text = http.get_text(QUOTE_URL.format(symbol=code))
    fields = text.split('"')[1].split("~") if '"' in text else []
    if len(fields) < 3 or not fields[1]:
        raise DataSourceError(f"Tencent has no HK quote for {code}")
    return SecurityInfo(symbol=code, name=fields[1], security_type="stock", exchange="hkex", source="tencent_hk", raw={"quote": fields[:6]})


def bars_with_factor_preclose(
    symbol: str,
    rows: list[dict[str, Any]],
    factors: list[tuple[str, float]],
    start_date: str,
    end_date: str,
    *,
    source: str = "sina_hk",
) -> list[DailyBar]:
    table = sorted(factors)
    dates = [item[0] for item in table]

    def factor_on(day: str) -> float:
        if not table:
            return 1.0
        index = bisect.bisect_right(dates, day) - 1
        return table[max(index, 0)][1]

    output = []
    previous: dict[str, Any] | None = None
    for row in sorted(rows, key=lambda item: item["d"]):
        day = row["d"]
        close = float(row["c"]) if row.get("c") else None
        if day > end_date[:10]:
            break
        preclose = None
        if previous is not None and close:
            preclose = float(previous["c"]) * factor_on(previous["d"]) / factor_on(day)
        if close:
            previous = row
        if day < start_date[:10] or not close:
            continue
        output.append(
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=_float(row.get("o")),
                high=_float(row.get("h")),
                low=_float(row.get("l")),
                close=close,
                volume=_float(row.get("v")),
                amount=_float(row.get("a")),
                source=f"{source}:qfq_preclose",
                raw={key: (None if value is None else str(value)) for key, value in row.items()},
                preclose=preclose if preclose and preclose > 0 else None,
            )
        )
    return output


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
