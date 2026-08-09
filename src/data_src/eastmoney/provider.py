"""Eastmoney exchange-fund data source skeleton."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.data_src.common import (
    normalize_a_share_code,
    normalize_date_compact,
    to_date_string,
    to_optional_float,
)
from src.data_src.exchange_fund import identify_exchange_fund
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DailyBar


class EastmoneyExchangeFundDataSource:
    """Fetch exchange-traded fund quotes from Eastmoney public endpoints."""

    source = "eastmoney"
    CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"

    def __init__(self, http_client: Any | None = None) -> None:
        self._provided_http = http_client is not None
        self.http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://quote.eastmoney.com/",
            },
            retry_count=3,
        )
        self.nav_http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://fundf10.eastmoney.com/",
            },
            retry_count=3,
        )

    def get_exchange_fund_spot(self) -> list[dict[str, Any]]:
        """Fetch ETF/LOF/closed-end fund spot records.

        Eastmoney's `fs` universe string is intentionally kept in one method so
        it can be adjusted when upstream categories change.
        """

        payload = self.http.get_json(
            self.CLIST_URL,
            {
                "pn": 1,
                "pz": 5000,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "b:MK0021,b:MK0022,b:MK0023",
                "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
            },
        )
        rows = ((payload.get("data") or {}).get("diff") or [])
        return [self._spot_from_row(row) for row in rows]

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
    ) -> list[DailyBar]:
        """Fetch a mainland index without querying Eastmoney's symbol map."""

        normalized_exchange = exchange.strip().lower()
        market_id = {"sh": "1", "sz": "0", "csi": "2"}.get(
            normalized_exchange
        )
        if market_id is None:
            raise ValueError("index exchange must be sh, sz, or csi")
        code = (
            _normalize_csi_index_code(symbol)
            if normalized_exchange == "csi"
            else normalize_a_share_code(symbol)
        )
        payload = self.http.get_json(
            self.KLINE_URL,
            {
                "secid": f"{market_id}.{code}",
                "klt": 101,
                "fqt": 0,
                "beg": normalize_date_compact(start_date),
                "end": normalize_date_compact(end_date),
                "lmt": 100000,
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                "fields2": (
                    "f51,f52,f53,f54,f55,f56,"
                    "f57,f58,f59,f60,f61"
                ),
            },
        )
        rows = ((payload.get("data") or {}).get("klines") or [])
        bars = []
        for raw in rows:
            fields = str(raw).split(",")
            if len(fields) < 7:
                continue
            bars.append(
                DailyBar(
                    symbol=code,
                    trade_date=fields[0],
                    open=to_optional_float(fields[1]),
                    close=to_optional_float(fields[2]),
                    high=to_optional_float(fields[3]),
                    low=to_optional_float(fields[4]),
                    volume=to_optional_float(fields[5]),
                    amount=to_optional_float(fields[6]),
                    source=f"{self.source}:stock_kline",
                    raw={
                        "secid": f"{market_id}.{code}",
                        "kline": raw,
                    },
                )
            )
        return bars

    def get_etf_nav_history_records(
        self,
        *,
        asset_id: str,
        symbol: str,
        start_date: str,
        end_date: str,
    ):
        """Fetch ETF unit and accumulated NAV from Eastmoney F10."""

        from src.fund_research.total_return import ETFNavPoint

        code = normalize_a_share_code(symbol)
        start = _date_parameter(start_date)
        end = _date_parameter(end_date)
        page = 1
        page_size = 20
        records = []
        nav_http = self.nav_http
        if not self._provided_http:
            nav_http = UrlLibHttpClient(
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": (
                        "https://fundf10.eastmoney.com/"
                        f"jjjz_{code}.html"
                    ),
                },
                retry_count=3,
            )
        while True:
            payload = nav_http.get_json(
                self.NAV_URL,
                {
                    "fundCode": code,
                    "pageIndex": page,
                    "pageSize": page_size,
                    "startDate": start,
                    "endDate": end,
                },
            )
            data = payload.get("Data")
            if data is not None and not isinstance(data, dict):
                raise RuntimeError(
                    "Eastmoney ETF NAV endpoint rejected the request"
                )
            rows = ((data or {}).get("LSJZList") or [])
            records.extend(rows)
            total_count = int(payload.get("TotalCount") or 0)
            if not rows or page * page_size >= total_count:
                break
            page += 1
        result = []
        for record in records:
            nav_date = to_date_string(record.get("FSRQ"))
            unit_nav = _positive_decimal(record.get("DWJZ"))
            if not nav_date or unit_nav is None:
                continue
            result.append(
                ETFNavPoint(
                    asset_id=asset_id,
                    date=nav_date,
                    unit_nav=unit_nav,
                    accumulated_nav=_positive_decimal(
                        record.get("LJJZ")
                    ),
                    available_at=(
                        date.fromisoformat(nav_date)
                        + timedelta(days=1)
                    ).isoformat(),
                    source=f"{self.source}:f10_lsjz",
                )
            )
        return sorted(result, key=lambda item: item.date)

    def _spot_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        code = str(row.get("f12") or "")
        name = row.get("f14")
        identity = identify_exchange_fund(code, name=name)
        return {
            "symbol": code,
            "name": name,
            "fund_type": identity.fund_type,
            "exchange": identity.exchange,
            "latest": to_optional_float(row.get("f2")),
            "pct_change": to_optional_float(row.get("f3")),
            "change": to_optional_float(row.get("f4")),
            "volume": _scale(to_optional_float(row.get("f5")), 100.0),
            "amount": to_optional_float(row.get("f6")),
            "high": to_optional_float(row.get("f15")),
            "low": to_optional_float(row.get("f16")),
            "open": to_optional_float(row.get("f17")),
            "pre_close": to_optional_float(row.get("f18")),
            "source": self.source,
            "raw": dict(row),
        }


def _scale(value: float | None, factor: float) -> float | None:
    return value * factor if value is not None else None


def _date_parameter(value: str) -> str:
    compact = normalize_date_compact(value)
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _positive_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return converted if converted.is_finite() and converted > 0 else None


def _normalize_csi_index_code(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if len(value) != 6 or not value.isalnum():
        raise ValueError("CSI index code must contain six letters/digits")
    return value
