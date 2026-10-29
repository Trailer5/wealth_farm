"""Trading-calendar-aware effective date calculation for daily datasets."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.data_src.service import DataFetchService

from .sqlite_repository import SQLiteDataAccessRepository


class TradingCalendarService:
    """Cache an official calendar and resolve the latest completed trade date."""

    def __init__(
        self,
        repository: SQLiteDataAccessRepository,
        fetch_service: DataFetchService,
        *,
        market: str = "cn_a",
        source: str = "baostock",
        fallback_sources: tuple[str, ...] = ("sina",),
        provider_timeout_seconds: float = 30.0,
    ) -> None:
        self.repository = repository
        self.fetch_service = fetch_service
        self.market = market
        self.source = source
        self.fallback_sources = fallback_sources
        self.provider_timeout_seconds = provider_timeout_seconds

    def effective_end_date(
        self,
        start_date: str,
        end_date: str,
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Return the last completed trading date inside a requested range."""

        latest_completed = self.latest_completed_trading_date(now=now)
        target_end = min(end_date, latest_completed)
        if start_date > target_end:
            return None
        trading_dates = self._trading_dates(start_date, target_end)
        return trading_dates[-1] if trading_dates else None

    def first_trading_date(
        self,
        start_date: str,
        end_date: str,
    ) -> str | None:
        trading_dates = self._trading_dates(start_date, end_date)
        return trading_dates[0] if trading_dates else None

    def latest_completed_trading_date(
        self,
        *,
        now: datetime | None = None,
    ) -> str:
        """Return the latest market date whose daily bar should be complete."""

        shanghai = ZoneInfo("Asia/Shanghai")
        current = now.astimezone(shanghai) if now is not None else datetime.now(shanghai)
        today = current.date()
        start = (today - timedelta(days=20)).isoformat()
        end = today.isoformat()
        trading_dates = self._trading_dates(start, end)
        if (
            trading_dates
            and trading_dates[-1] == end
            and current.timetz().replace(tzinfo=None) < time(16, 0)
        ):
            trading_dates = trading_dates[:-1]
        if not trading_dates:
            raise RuntimeError("Trading calendar returned no completed date")
        return trading_dates[-1]

    def _trading_dates(self, start_date: str, end_date: str) -> list[str]:
        # BaoStock first; Sina's trade-date list when BaoStock fails (e.g. the IP is blacklisted).
        # Each source keeps its own cached rows and coverage.
        sources = (self.source, *(item for item in self.fallback_sources if item != self.source))
        covered = next(
            (
                source
                for source in sources
                if self.repository.has_trading_calendar_coverage(
                    market=self.market, start_date=start_date, end_date=end_date, source=source
                )
            ),
            None,
        )
        if covered is None:
            errors = []
            for source in sources:
                try:
                    rows = self.fetch_service.fetch_trade_calendar(
                        start_date=start_date,
                        end_date=end_date,
                        source=source,
                        timeout_seconds=self.provider_timeout_seconds,
                    )
                    self.repository.save_trading_calendar(
                        market=self.market,
                        start_date=start_date,
                        end_date=end_date,
                        source=source,
                        rows=rows,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    errors.append(f"{source}: {type(exc).__name__}: {exc}")
                    continue
                covered = source
                break
            if covered is None:
                raise RuntimeError("Trading calendar unavailable: " + "; ".join(errors))
        return self.repository.get_trading_dates(
            market=self.market,
            start_date=start_date,
            end_date=end_date,
            source=covered,
        )
