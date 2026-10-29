"""A-share and exchange-fund daily bars through Tongdaxin TCP servers."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from src.data_src.common import (
    DataSourceError,
    DependencyNotInstalledError,
    normalize_a_share_code,
    to_optional_float,
)
from src.data_src.models import DailyBar
from src.data_src.throttle import default_throttle


class MootdxDataSource:
    """Fetch daily bars from an upstream independent of Eastmoney HTTP."""

    source = "mootdx"

    def __init__(
        self,
        client_factory: Callable[..., Any] | None = None,
        timeout: int = 5,
    ) -> None:
        self.client_factory = client_factory
        self.timeout = timeout

    def get_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        """Fetch A-share daily bars and filter the requested date range."""

        return self._get_daily_bars(symbol, start_date, end_date)

    def get_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[DailyBar]:
        """Fetch exchange-fund daily bars."""

        return self._get_daily_bars(symbol, start_date, end_date)

    def get_xdxr_events(self, symbol: str) -> list[dict[str, Any]]:
        """Ex-rights events: category 1 dividends/bonus/rights, 11 share splits."""

        client = self._create_client()
        try:
            events = client.xdxr(symbol=normalize_a_share_code(symbol))
            records = events.to_dict("records") if events is not None else []
        except Exception as exc:
            raise DataSourceError("mootdx xdxr failed") from exc
        finally:
            client.close()
        output = []
        for record in records:
            category = int(record.get("category") or 0)
            if category not in {1, 11}:
                continue
            output.append(
                {
                    "ex_date": (
                        f"{int(record['year']):04d}-{int(record['month']):02d}-"
                        f"{int(record['day']):02d}"
                    ),
                    "category": category,
                    "cash_per_10": _number(record.get("fenhong")),
                    "bonus_per_10": _number(record.get("songzhuangu")),
                    "rights_per_10": _number(record.get("peigu")),
                    "rights_price": _number(record.get("peigujia")),
                    "split_ratio": _number(record.get("suogu")),
                }
            )
        return output

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
        offset = _required_offset(start)
        if offset > 800:
            raise ValueError(
                "mootdx baseline provider supports up to 800 recent daily bars"
            )
        client = self._create_client()
        try:
            frame = client.bars(
                code,
                frequency=9,
                start=0,
                offset=offset,
            )
            records = frame.to_dict("records")
        except Exception as exc:
            raise DataSourceError("mootdx daily bars failed") from exc
        finally:
            client.close()
        bars = []
        for record in records:
            trade_date = str(record.get("datetime", ""))[:10]
            if trade_date < start.isoformat() or trade_date > end.isoformat():
                continue
            bars.append(
                DailyBar(
                    symbol=code,
                    trade_date=trade_date,
                    open=to_optional_float(record.get("open")),
                    high=to_optional_float(record.get("high")),
                    low=to_optional_float(record.get("low")),
                    close=to_optional_float(record.get("close")),
                    volume=_scale(
                        to_optional_float(
                            record.get("volume", record.get("vol"))
                        ),
                        100.0,
                    ),
                    amount=to_optional_float(record.get("amount")),
                    source=f"{self.source}:bars",
                    raw=dict(record),
                )
            )
        return bars

    def _create_client(self):
        if self.client_factory is not None:
            return self.client_factory("std", timeout=self.timeout)
        try:
            from mootdx.quotes import Quotes
        except ImportError as exc:
            raise DependencyNotInstalledError(
                "mootdx is not installed. Add it to dependencies."
            ) from exc
        default_throttle().acquire("mootdx")
        return Quotes.factory("std", timeout=self.timeout)


def _parse_date(value: str) -> date:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(
            f"{text[:4]}-{text[4:6]}-{text[6:]}"
        )
    return date.fromisoformat(text)


def _required_offset(start: date) -> int:
    calendar_days = max((date.today() - start).days, 0)
    return max(20, int(calendar_days * 5 / 7) + 30)


def _scale(value: float | None, factor: float) -> float | None:
    return value * factor if value is not None else None


def _number(value: Any) -> float:
    number = to_optional_float(value)
    return number if number is not None and number == number else 0.0
