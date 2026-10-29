"""Cash dividends per share from implemented distribution announcements.

Design notes: docs/modules/3-分析工具/估值与财务分析模块.md「股息率口径」.
The cash-flow line 分配股利、利润或偿付利息支付的现金 also contains interest paid, so it is not a dividend source.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.data_src.process_executor import run_provider_task

DIVIDEND_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class DividendEvent:
    announce_date: str
    ex_date: str
    cash_per_share: Decimal

    def to_dict(self) -> dict[str, str]:
        return {"announce_date": self.announce_date, "ex_date": self.ex_date, "cash_per_share": str(self.cash_per_share)}


def trailing_dividends(events: tuple[DividendEvent, ...], as_of_date: str) -> tuple[Decimal, tuple[DividendEvent, ...]]:
    """Cash per share with an ex-date in the 365 days up to ``as_of_date``."""

    start = (date.fromisoformat(as_of_date) - timedelta(days=365)).isoformat()
    paid = tuple(item for item in events if start < item.ex_date <= as_of_date and item.announce_date <= as_of_date)
    return sum((item.cash_per_share for item in paid), Decimal("0")), paid


def dividends_by_year(events: tuple[DividendEvent, ...], as_of_date: str) -> dict[str, Decimal]:
    """Cash per share by ex-date calendar year, only for events already ex-dividend by ``as_of_date``."""

    totals: dict[str, Decimal] = {}
    for item in events:
        if item.ex_date <= as_of_date:
            totals[item.ex_date[:4]] = totals.get(item.ex_date[:4], Decimal("0")) + item.cash_per_share
    return dict(sorted(totals.items()))


def _fetch_in_process(symbol: str, timeout_seconds: float) -> list[dict[str, Any]]:
    return run_provider_task(
        "src.data_src.provider_tasks",
        "fetch_stock_dividend_events",
        {"symbol": symbol},
        timeout_seconds=timeout_seconds,
    )


class DividendHistoryService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        fetcher: Callable[[str, float], list[dict[str, Any]]] | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.db_path = Path(db_path)
        self.fetcher = fetcher or _fetch_in_process
        self.today = today
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_dividend_snapshots (
                    symbol TEXT NOT NULL,
                    fetched_on TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (symbol, fetched_on)
                )
                """
            )

    def events(
        self,
        symbol: str,
        *,
        allow_remote: bool = True,
        timeout_seconds: float = 30.0,
    ) -> tuple[tuple[DividendEvent, ...] | None, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_on, payload_json FROM stock_dividend_snapshots WHERE symbol = ? ORDER BY fetched_on DESC LIMIT 1",
                [symbol],
            ).fetchone()
        cached = None if row is None else (row[0], json.loads(row[1]))
        fresh = cached is not None and (self.today() - date.fromisoformat(cached[0])).days <= DIVIDEND_MAX_AGE_DAYS
        error = None
        if not fresh and allow_remote:
            try:
                records = self.fetcher(symbol, timeout_seconds)
                cached = (self.today().isoformat(), records)
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO stock_dividend_snapshots VALUES (?, ?, ?)",
                        [symbol, cached[0], json.dumps(records, ensure_ascii=False)],
                    )
            except Exception as exc:  # noqa: BLE001 - stale cache is still usable
                error = f"{type(exc).__name__}: {exc}"
        if cached is None:
            return None, error
        return tuple(DividendEvent(item["announce_date"], item["ex_date"], Decimal(item["cash_per_share"])) for item in cached[1]), error

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)
