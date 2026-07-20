"""BaoStock data source adapter."""

from __future__ import annotations

from typing import Any

from ..common import (
    DataSourceError,
    DependencyNotInstalledError,
    baostock_result_to_records,
    ensure_baostock_success,
    normalize_date_dash,
    to_baostock_symbol,
    to_optional_float,
)
from ..models import DailyBar, SecurityInfo


class BaoStockDataSource:
    """Fetch data through BaoStock and expose project-facing methods."""

    source = "baostock"

    _DAILY_FIELDS = ",".join(
        [
            "date",
            "code",
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "adjustflag",
            "turn",
            "tradestatus",
            "pctChg",
            "peTTM",
            "pbMRQ",
            "psTTM",
            "pcfNcfTTM",
            "isST",
        ]
    )

    _MINUTE_FIELDS = ",".join(["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"])

    def __init__(self, baostock_module: Any | None = None) -> None:
        self._baostock = baostock_module
        self._logged_in = False

    def __enter__(self) -> "BaoStockDataSource":
        self.login()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.logout()

    def login(self) -> None:
        """Login to BaoStock if needed."""

        if self._logged_in:
            return
        result = self._load_baostock().login()
        ensure_baostock_success(result, "BaoStock login")
        self._logged_in = True

    def logout(self) -> None:
        """Logout from BaoStock if this adapter opened a session."""

        if not self._logged_in:
            return
        self._load_baostock().logout()
        self._logged_in = False

    def get_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustflag: str = "3",
    ) -> list[DailyBar]:
        """Fetch A-share daily bars."""

        records = self.get_history_records(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=adjustflag,
            fields=self._DAILY_FIELDS,
        )
        return [self._daily_bar_from_record(record) for record in records]

    def get_history_records(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "3",
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw historical K-line records.

        `frequency` follows BaoStock convention: `d`, `w`, `m`, `5`, `15`,
        `30`, or `60`.
        """

        selected_fields = fields or (self._MINUTE_FIELDS if frequency in {"5", "15", "30", "60"} else self._DAILY_FIELDS)
        result = self._query(
            "query_history_k_data_plus",
            to_baostock_symbol(symbol),
            selected_fields,
            start_date=normalize_date_dash(start_date),
            end_date=normalize_date_dash(end_date),
            frequency=frequency,
            adjustflag=adjustflag,
        )
        return baostock_result_to_records(result)

    def get_trade_dates(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Fetch trade calendar records."""

        result = self._query(
            "query_trade_dates",
            start_date=normalize_date_dash(start_date),
            end_date=normalize_date_dash(end_date),
        )
        return baostock_result_to_records(result)

    def get_all_stocks(self, day: str) -> list[dict[str, Any]]:
        """Disabled: full-market list should come from a gentler source.

        Live validation timed this BaoStock endpoint out even with isolated
        90-second runs and backoff. It is not unique because CNINFO mappings,
        single-symbol BaoStock basic info, and later exchange/AkShare lists can
        cover the baseline security universe.
        """

        # Disabled original implementation:
        # result = self._query("query_all_stock", day=normalize_date_dash(day))
        # return baostock_result_to_records(result)
        raise DataSourceError(
            "BaoStock all-stock list is disabled; use CNINFO/security mappings or single-symbol basic info instead."
        )

    def get_stock_basic(
        self,
        symbol: str | None = None,
        code_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch security basic information."""

        code = to_baostock_symbol(symbol) if symbol else None
        result = self._query("query_stock_basic", code=code, code_name=code_name)
        return baostock_result_to_records(result)

    def get_stock_basic_records(
        self,
        symbol: str | None = None,
        code_name: str | None = None,
    ) -> list[SecurityInfo]:
        """Fetch and normalize stock basic information."""

        return [self._security_from_record(record) for record in self.get_stock_basic(symbol, code_name)]

    def get_profit_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly profitability data."""

        return self._query_financial_records("query_profit_data", symbol, year, quarter)

    def get_operation_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly operation capability data."""

        return self._query_financial_records("query_operation_data", symbol, year, quarter)

    def get_growth_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly growth capability data."""

        return self._query_financial_records("query_growth_data", symbol, year, quarter)

    def get_balance_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly balance sheet capability data."""

        return self._query_financial_records("query_balance_data", symbol, year, quarter)

    def get_cash_flow_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly cash flow capability data."""

        return self._query_financial_records("query_cash_flow_data", symbol, year, quarter)

    def get_dupont_data(self, symbol: str, year: int, quarter: int) -> list[dict[str, Any]]:
        """Fetch quarterly DuPont analysis data."""

        return self._query_financial_records("query_dupont_data", symbol, year, quarter)

    def _query_financial_records(
        self,
        api_name: str,
        symbol: str,
        year: int,
        quarter: int,
    ) -> list[dict[str, Any]]:
        result = self._query(api_name, code=to_baostock_symbol(symbol), year=year, quarter=quarter)
        return baostock_result_to_records(result)

    def _query(self, api_name: str, *args: Any, **kwargs: Any) -> Any:
        self.login()
        bs = self._load_baostock()
        api = getattr(bs, api_name, None)
        if api is None:
            raise DataSourceError(f"BaoStock API is not available: {api_name}")
        result = api(*args, **kwargs)
        ensure_baostock_success(result, f"BaoStock API call {api_name}")
        return result

    def _load_baostock(self) -> Any:
        if self._baostock is not None:
            return self._baostock
        try:
            import baostock as bs  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise DependencyNotInstalledError(
                "BaoStock is not installed. Add `baostock` to dependencies and install it."
            ) from exc
        self._baostock = bs
        return bs

    def _daily_bar_from_record(self, record: dict[str, Any]) -> DailyBar:
        return DailyBar(
            symbol=str(record.get("code")),
            trade_date=str(record.get("date")),
            open=to_optional_float(record.get("open")),
            high=to_optional_float(record.get("high")),
            low=to_optional_float(record.get("low")),
            close=to_optional_float(record.get("close")),
            volume=to_optional_float(record.get("volume")),
            amount=to_optional_float(record.get("amount")),
            source=f"{self.source}:query_history_k_data_plus",
            raw=dict(record),
        )

    def _security_from_record(self, record: dict[str, Any]) -> SecurityInfo:
        return SecurityInfo(
            symbol=str(record.get("code") or ""),
            name=record.get("code_name"),
            security_type=str(record.get("type")) if record.get("type") is not None else None,
            exchange=str(record.get("code", "")).split(".")[0] if "." in str(record.get("code", "")) else None,
            source=f"{self.source}:query_stock_basic",
            raw=dict(record),
        )
