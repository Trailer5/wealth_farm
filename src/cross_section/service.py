"""On-demand A-share cross-sections: current snapshot, valuations, report-period financials."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from src.data_src.process_executor import run_provider_task

from .sqlite_repository import SQLiteCrossSectionRepository

_TASKS = "src.data_src.provider_tasks"
_REFERENCE_MAX_AGE = timedelta(days=30)
_HISTORY_MAX_AGE = timedelta(days=1)
# Reports keep arriving (and being revised) for about four months after a period ends.
_OPEN_PERIOD_DAYS = 130
_OPEN_PERIOD_MAX_AGE = timedelta(days=1)
_CLOSED_PERIOD_MAX_AGE = timedelta(days=90)
FINANCIAL_BASIS_NOTE = (
    "东方财富按报告期的全市场业绩报表、资产负债表与现金流量表摘要；"
    "按“最新公告日期”过滤，修订过的报告以修订日为准（偏保守）"
)


class CrossSectionFetcher(Protocol):
    def market_snapshot(self) -> dict[str, Any]: ...

    def valuation_histories(self, symbols: list[str]) -> dict[str, Any]: ...

    def report_cross_section(self, report_period: str) -> list[dict[str, Any]]: ...

    def delisted(self) -> list[dict[str, Any]]: ...

    def index_members_on(self, index_symbol: str, day: str) -> list[dict[str, Any]]: ...


class ProviderCrossSectionFetcher:
    def market_snapshot(self) -> dict[str, Any]:
        return run_provider_task(_TASKS, "fetch_market_snapshot", {}, timeout_seconds=300.0)

    def valuation_histories(self, symbols: list[str]) -> dict[str, Any]:
        return run_provider_task(
            _TASKS,
            "fetch_stock_valuation_histories",
            {"symbols": symbols},
            timeout_seconds=60.0 + 3.0 * len(symbols),
        )

    def report_cross_section(self, report_period: str) -> list[dict[str, Any]]:
        return run_provider_task(
            _TASKS,
            "fetch_financial_report_cross_section",
            {"report_period": report_period},
            timeout_seconds=180.0,
        )

    def delisted(self) -> list[dict[str, Any]]:
        return run_provider_task(_TASKS, "fetch_delisted_a_shares", {}, timeout_seconds=60.0)

    def index_members_on(self, index_symbol: str, day: str) -> list[dict[str, Any]]:
        return run_provider_task(
            _TASKS,
            "fetch_index_members_on",
            {"index_symbol": index_symbol, "day": day},
            timeout_seconds=60.0,
        )


class CrossSectionUnavailable(RuntimeError):
    """Data is not cached and could not be fetched."""


class CrossSectionService:
    def __init__(
        self,
        repository: SQLiteCrossSectionRepository,
        *,
        fetcher: CrossSectionFetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher or ProviderCrossSectionFetcher()
        self.clock = clock
        self.repository.initialize()

    def market_snapshot(
        self,
        *,
        max_age_hours: float = 6.0,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Latest snapshot of all listed A shares; every fetch is kept as a point-in-time record."""

        latest = self.repository.latest_snapshot()
        fresh = latest is not None and self.clock() - datetime.fromisoformat(
            latest["fetched_at"]
        ) <= timedelta(hours=max_age_hours)
        if not fresh and allow_remote:
            try:
                fetched = self.fetcher.market_snapshot()
            except Exception as exc:  # noqa: BLE001 - fall back to the cached snapshot
                fetched = None
                error = f"{type(exc).__name__}: {exc}"
            if fetched is not None and fetched["rows"]:
                now = self.clock().isoformat()
                dates = Counter(row.get("quote_date") for row in fetched["rows"] if row.get("quote_date"))
                self.repository.save_snapshot(
                    f"snapshot:{now}",
                    fetched_at=now,
                    trade_date=dates.most_common(1)[0][0] if dates else None,
                    rows=fetched["rows"],
                    errors=fetched["errors"],
                )
                latest = self.repository.latest_snapshot()
            elif latest is None:
                raise CrossSectionUnavailable(
                    error if fetched is None else "Market snapshot returned no rows"
                )
        if latest is None:
            raise CrossSectionUnavailable("No cached market snapshot and remote is disabled")
        snapshot = self.repository.get_snapshot(latest["snapshot_id"])
        rows = snapshot["rows"]
        snapshot["coverage"] = {
            "rows": len(rows),
            "with_pe_ttm": sum(1 for row in rows if row.get("pe_ttm") is not None),
            "with_pb": sum(1 for row in rows if row.get("pb") is not None),
            "with_market_cap": sum(1 for row in rows if row.get("market_cap") is not None),
        }
        snapshot["note"] = "当前全市场截面（新浪行情 + 腾讯估值），只有最新一天，不能用于回溯"
        return snapshot

    def valuation_history(
        self,
        symbols: list[str],
        *,
        start_date: str,
        end_date: str,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Daily PE, PB, PS and market cap per stock (Eastmoney, since 2018), cached per stock."""

        codes = [symbol.strip().zfill(6) for symbol in symbols]
        stale = [code for code in codes if self._stale("valuation", code, _HISTORY_MAX_AGE)]
        errors: dict[str, str] = {}
        if stale and allow_remote:
            try:
                outcomes = self.fetcher.valuation_histories(stale)
            except Exception as exc:  # noqa: BLE001 - cached rows remain usable
                outcomes = {code: f"{type(exc).__name__}: {exc}" for code in stale}
            for code, outcome in outcomes.items():
                if isinstance(outcome, str) or not outcome:
                    errors[code] = outcome if isinstance(outcome, str) else "no rows"
                    self._log("valuation", code, 0, errors[code])
                    continue
                self.repository.upsert_valuations(code, outcome)
                self._log("valuation", code, len(outcome), None)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "series": {
                code: self.repository.list_valuations(code, start_date, end_date) for code in codes
            },
            "provider_errors": errors,
            "note": "东方财富个股估值（2018 年起日频）；PE(TTM) 为负或空表示亏损或无数据",
        }

    def report_period(
        self,
        report_period: str,
        *,
        as_of_date: str | None = None,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """One report period for all stocks; with as_of_date only rows announced by then."""

        period = date.fromisoformat(report_period).isoformat()
        error = self._refresh_period(period, allow_remote)
        rows = self.repository.list_report_rows(period)
        if as_of_date is not None:
            rows = [
                row for row in rows
                if row.get("announced_on") and row["announced_on"] <= as_of_date
            ]
        return {
            "report_period": period,
            "as_of_date": as_of_date,
            "rows": rows,
            "row_count": len(rows),
            "provider_error": error,
            "basis_note": FINANCIAL_BASIS_NOTE,
        }

    def latest_financials(
        self,
        as_of_date: str,
        *,
        periods: int = 5,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Per stock, the newest report period already announced on as_of_date."""

        candidates = _quarter_ends_before(as_of_date, periods)
        latest: dict[str, dict[str, Any]] = {}
        errors = {}
        for period in candidates:
            result = self.report_period(period, as_of_date=as_of_date, allow_remote=allow_remote)
            if result["provider_error"]:
                errors[period] = result["provider_error"]
            for row in result["rows"]:
                latest.setdefault(row["symbol"], row)
        return {
            "as_of_date": as_of_date,
            "periods_checked": candidates,
            "rows": [latest[symbol] for symbol in sorted(latest)],
            "row_count": len(latest),
            "period_counts": dict(Counter(row["report_period"] for row in latest.values())),
            "provider_errors": errors,
            "basis_note": FINANCIAL_BASIS_NOTE,
        }

    def delisted(self, *, allow_remote: bool = True) -> list[dict[str, Any]]:
        if self._stale("delisted", "all", _REFERENCE_MAX_AGE) and allow_remote:
            try:
                rows = self.fetcher.delisted()
            except Exception as exc:  # noqa: BLE001 - keep the cached list
                self._log("delisted", "all", 0, f"{type(exc).__name__}: {exc}")
            else:
                self.repository.replace_reference("delisted", "all", rows)
                self._log("delisted", "all", len(rows), None)
        return self.repository.list_reference("delisted", "all")

    def index_members_on(
        self,
        index_symbol: str,
        day: str,
        *,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Historical CSI 300, CSI 500 or SSE 50 constituents in effect on a date (BaoStock)."""

        key = f"{index_symbol}:{day}"
        rows = self.repository.list_reference("index_members", key)
        if not rows and allow_remote:
            try:
                rows = self.fetcher.index_members_on(index_symbol, day)
            except Exception as exc:  # noqa: BLE001 - reported as unavailable
                self._log("index_members", key, 0, f"{type(exc).__name__}: {exc}")
                raise CrossSectionUnavailable(
                    f"历史指数成分不可用（BaoStock）：{type(exc).__name__}: {exc}"
                ) from exc
            self.repository.replace_reference("index_members", key, rows)
            self._log("index_members", key, len(rows), None)
        if not rows:
            raise CrossSectionUnavailable(f"No cached members for {index_symbol} on {day}")
        return {"index_symbol": index_symbol, "date": day, "members": rows, "count": len(rows)}

    def _refresh_period(self, period: str, allow_remote: bool) -> str | None:
        open_period = (self.clock().date() - date.fromisoformat(period)).days <= _OPEN_PERIOD_DAYS
        max_age = _OPEN_PERIOD_MAX_AGE if open_period else _CLOSED_PERIOD_MAX_AGE
        if not self._stale("report_period", period, max_age) or not allow_remote:
            return None
        try:
            rows = self.fetcher.report_cross_section(period)
        except Exception as exc:  # noqa: BLE001 - cached rows remain usable
            error = f"{type(exc).__name__}: {exc}"
            self._log("report_period", period, 0, error)
            return error
        if rows:
            self.repository.replace_report_rows(period, rows)
        self._log("report_period", period, len(rows), None if rows else "no rows")
        return None if rows else "no rows"

    def _stale(self, kind: str, key: str, max_age: timedelta) -> bool:
        last = self.repository.last_success(kind, key)
        return last is None or self.clock() - datetime.fromisoformat(last) > max_age

    def _log(self, kind: str, key: str, count: int, error: str | None) -> None:
        self.repository.log_fetch(
            kind,
            key,
            fetched_at=self.clock().isoformat(),
            record_count=count,
            error=error,
        )


def _quarter_ends_before(as_of_date: str, count: int) -> list[str]:
    day = date.fromisoformat(as_of_date)
    year, quarter = day.year, (day.month - 1) // 3
    ends = []
    while len(ends) < count:
        if quarter == 0:
            year, quarter = year - 1, 4
        month = quarter * 3
        end = date(year, month, 31 if month in (3, 12) else 30)
        if end < day:
            ends.append(end.isoformat())
        quarter -= 1
    return ends
