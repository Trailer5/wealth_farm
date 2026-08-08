"""AkShare data source adapter."""

from __future__ import annotations

from typing import Any

from ..common import (
    DataSourceError,
    DependencyNotInstalledError,
    dataframe_to_records,
    normalize_date_compact,
    to_akshare_market_symbol,
    to_akshare_symbol,
    to_date_string,
    to_optional_float,
)
from ..models import (
    DailyBar,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
    IndexConstituent,
    SecurityInfo,
)


class AkShareDataSource:
    """Fetch data through AkShare and expose project-facing methods."""

    source = "akshare"

    def __init__(self, akshare_module: Any | None = None) -> None:
        self._akshare = akshare_module

    def get_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> list[DailyBar]:
        """Fetch A-share daily bars."""

        return self.get_a_share_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period="daily",
            adjust=adjust,
        )

    def get_a_share_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "",
    ) -> list[DailyBar]:
        """Fetch A-share historical bars.

        `period` follows AkShare's `stock_zh_a_hist` convention: `daily`,
        `weekly`, or `monthly`.
        """

        normalized_symbol = to_akshare_symbol(symbol)
        frame = self._call_dataframe(
            "stock_zh_a_hist",
            symbol=normalized_symbol,
            period=period,
            start_date=normalize_date_compact(start_date),
            end_date=normalize_date_compact(end_date),
            adjust=adjust,
        )
        return [
            self._daily_bar_from_record(record, normalized_symbol, source=f"{self.source}:stock_zh_a_hist")
            for record in dataframe_to_records(frame)
        ]

    def get_a_share_spot(self) -> list[dict[str, Any]]:
        """Disabled: A-share real-time quotes need a separate, gentler provider.

        The AkShare Eastmoney full-market endpoint repeatedly disconnects under
        validation pressure and is not required for the current daily-level
        baseline.
        """

        # Disabled original implementation:
        # return self._records_from_api("stock_zh_a_spot_em")
        raise DataSourceError(
            "AkShare A-share spot is disabled; use daily bars now and add an independent quote provider later."
        )

    def get_a_share_minute_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "5",
        adjust: str = "",
    ) -> list[dict[str, Any]]:
        """Disabled: minute bars are outside the current daily-level baseline.

        The AkShare minute endpoint repeatedly disconnects during validation.
        Reintroduce minute bars through a dedicated provider when minute-level
        research becomes a milestone.
        """

        # Disabled original implementation:
        # return self._records_from_api(
        #     "stock_zh_a_hist_min_em",
        #     symbol=to_akshare_symbol(symbol),
        #     start_date=_normalize_minute_datetime(start_date),
        #     end_date=_normalize_minute_datetime(end_date),
        #     period=period,
        #     adjust=adjust,
        # )
        raise DataSourceError(
            "AkShare A-share minute bars are disabled; the current baseline only requires daily bars."
        )

    def get_etf_spot(self) -> list[dict[str, Any]]:
        """Fetch ETF real-time quotes from Eastmoney via AkShare."""

        return self._records_from_api("fund_etf_spot_em")

    def get_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
        period: str = "daily",
    ) -> list[DailyBar]:
        """Fetch ETF historical bars."""

        normalized_symbol = to_akshare_symbol(symbol)
        frame = self._call_dataframe(
            "fund_etf_hist_em",
            symbol=normalized_symbol,
            period=period,
            start_date=normalize_date_compact(start_date),
            end_date=normalize_date_compact(end_date),
            adjust=adjust,
        )
        return [
            self._daily_bar_from_record(record, normalized_symbol, source=f"{self.source}:fund_etf_hist_em")
            for record in dataframe_to_records(frame)
        ]

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
    ) -> list[DailyBar]:
        """Fetch mainland index historical bars."""

        normalized_symbol = to_akshare_symbol(symbol)
        frame = self._call_dataframe(
            "index_zh_a_hist",
            symbol=normalized_symbol,
            period=period,
            start_date=normalize_date_compact(start_date),
            end_date=normalize_date_compact(end_date),
        )
        return [
            self._daily_bar_from_record(
                record,
                normalized_symbol,
                source=f"{self.source}:index_zh_a_hist",
            )
            for record in dataframe_to_records(frame)
        ]

    def get_index_constituents(
        self,
        symbol: str,
    ) -> list[IndexConstituent]:
        """Fetch a current CSIndex constituent snapshot."""

        normalized_symbol = _to_akshare_index_symbol(symbol)
        frame = self._call_dataframe(
            "index_stock_cons_csindex",
            symbol=normalized_symbol,
        )
        records = dataframe_to_records(frame)
        return [
            IndexConstituent(
                index_symbol=str(
                    record.get("指数代码")
                    or normalized_symbol
                ),
                index_name=str(record.get("指数名称") or ""),
                constituent_symbol=to_akshare_symbol(
                    str(record.get("成分券代码") or "")
                ),
                constituent_name=str(
                    record.get("成分券名称") or ""
                ),
                exchange=_constituent_exchange(
                    str(record.get("交易所") or "")
                ),
                effective_date=to_date_string(
                    record.get("日期")
                ),
                source=(
                    f"{self.source}:"
                    "index_stock_cons_csindex"
                ),
                raw=dict(record),
            )
            for record in records
            if record.get("成分券代码")
            and to_date_string(record.get("日期"))
        ]

    def get_gold_spot_history(self) -> list[dict[str, Any]]:
        records = self._records_from_api(
            "spot_hist_sge",
            symbol="Au99.99",
        )
        return [
            {
                **record,
                "date": to_date_string(record.get("date")),
                "source": f"{self.source}:spot_hist_sge",
                "benchmark_symbol": "Au99.99",
            }
            for record in records
        ]

    def get_copper_continuous_history(
        self,
    ) -> list[dict[str, Any]]:
        records = self._records_from_api(
            "futures_zh_daily_sina",
            symbol="CU0",
        )
        return [
            {
                **record,
                "date": to_date_string(record.get("date")),
                "source": f"{self.source}:futures_zh_daily_sina",
                "benchmark_symbol": "CU0",
            }
            for record in records
        ]

    def get_open_fund_daily_navs(self) -> list[dict[str, Any]]:
        """Fetch latest open-end fund NAV table."""

        return self._records_from_api("fund_open_fund_daily_em")

    def get_fund_names(self) -> list[SecurityInfo]:
        """Fetch public fund name table and normalize as securities."""

        records = self._records_from_api("fund_name_em")
        return [self._fund_security_from_record(record) for record in records]

    def get_fund_purchase_status(self) -> list[dict[str, Any]]:
        """Fetch public fund purchase and redemption status."""

        return self._records_from_api("fund_purchase_em")

    def get_fund_purchase_status_records(self) -> list[FundPurchaseStatus]:
        """Fetch and normalize public fund purchase/redemption status."""

        return [self._fund_purchase_status_from_record(record) for record in self.get_fund_purchase_status()]

    def get_open_fund_nav_history(
        self,
        symbol: str,
        indicator: str = "单位净值走势",
        period: str = "成立来",
    ) -> list[dict[str, Any]]:
        """Fetch open-end fund NAV history from Eastmoney/Tiantian fund data."""

        return self._records_from_api(
            "fund_open_fund_info_em",
            symbol=to_akshare_symbol(symbol),
            indicator=indicator,
            period=period,
        )

    def get_open_fund_nav_history_records(
        self,
        symbol: str,
        indicator: str = "单位净值走势",
        period: str = "成立来",
    ) -> list[FundNav]:
        """Fetch and normalize open-end fund NAV history."""

        normalized_symbol = to_akshare_symbol(symbol)
        records = self.get_open_fund_nav_history(normalized_symbol, indicator=indicator, period=period)
        return [self._fund_nav_from_record(record, normalized_symbol) for record in records]

    def get_money_fund_daily_navs(self) -> list[dict[str, Any]]:
        """Fetch latest money market fund NAV table."""

        return self._records_from_api("fund_money_fund_daily_em")

    def get_fund_value_estimates(self) -> list[FundEstimate]:
        """Fetch platform-estimated open-end fund values.

        Estimates are not official NAV data and must remain isolated from
        `fund_nav_daily`.
        """

        records = self._records_from_api("fund_value_estimation_em")
        return [self._fund_estimate_from_record(record) for record in records]

    def get_fund_scale_records(self, fund_type: str = "股票型基金") -> list[FundReportRecord]:
        """Fetch open-end fund scale records by fund type."""

        records = self._records_from_api("fund_scale_open_sina", symbol=fund_type)
        return [self._fund_report_from_record(record, "fund_scale_open_sina") for record in records]

    def get_fund_stock_holdings(self, symbol: str, year: str) -> list[FundReportRecord]:
        """Fetch public fund stock holdings for a year.

        This endpoint has useful fund look-through value, but the current
        AkShare parser fails against live upstream responses. Keep this as an
        experimental route and prefer official fund disclosure PDF parsing when
        fund holdings become required.
        """

        records = self._records_from_api("fund_portfolio_hold_em", symbol=to_akshare_symbol(symbol), date=str(year))
        return [self._fund_report_from_record(record, "fund_portfolio_hold_em", fallback_symbol=symbol) for record in records]

    def get_fund_bond_holdings(self, symbol: str, year: str) -> list[FundReportRecord]:
        """Fetch public fund bond holdings for a year.

        This endpoint has useful bond-fund look-through value, but the current
        AkShare parser fails against live upstream responses. Keep this as an
        experimental route and prefer official fund disclosure PDF parsing when
        bond holdings become required.
        """

        records = self._records_from_api("fund_portfolio_bond_hold_em", symbol=to_akshare_symbol(symbol), date=str(year))
        return [self._fund_report_from_record(record, "fund_portfolio_bond_hold_em", fallback_symbol=symbol) for record in records]

    def get_fund_industry_allocation(self, symbol: str, year: str) -> list[FundReportRecord]:
        """Fetch public fund industry allocation for a year."""

        records = self._records_from_api(
            "fund_portfolio_industry_allocation_em",
            symbol=to_akshare_symbol(symbol),
            date=str(year),
        )
        return [
            self._fund_report_from_record(record, "fund_portfolio_industry_allocation_em", fallback_symbol=symbol)
            for record in records
        ]

    def get_fund_asset_allocation_cninfo(self, **kwargs: Any) -> list[FundReportRecord]:
        """Fetch CNINFO public fund asset allocation reports."""

        records = self._records_from_api("fund_report_asset_allocation_cninfo")
        return [self._fund_report_from_record(record, "fund_report_asset_allocation_cninfo") for record in records]

    def get_fund_industry_allocation_cninfo(self, **kwargs: Any) -> list[FundReportRecord]:
        """Fetch CNINFO public fund industry allocation reports."""

        records = self._records_from_api("fund_report_industry_allocation_cninfo", **kwargs)
        return [self._fund_report_from_record(record, "fund_report_industry_allocation_cninfo") for record in records]

    def get_stock_profit_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch A-share income statement by reporting period."""

        return self._records_from_api("stock_profit_sheet_by_report_em", symbol=_to_akshare_prefixed_symbol(symbol))

    def get_stock_balance_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch A-share balance sheet by reporting period."""

        return self._records_from_api("stock_balance_sheet_by_report_em", symbol=_to_akshare_prefixed_symbol(symbol))

    def get_stock_cash_flow_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch A-share cash flow statement by reporting period."""

        return self._records_from_api("stock_cash_flow_sheet_by_report_em", symbol=_to_akshare_prefixed_symbol(symbol))

    def get_stock_financial_indicators(
        self,
        symbol: str,
        indicator: str = "按报告期",
    ) -> list[dict[str, Any]]:
        """Fetch A-share financial indicators."""

        return self._records_from_api(
            "stock_financial_analysis_indicator_em",
            symbol=to_akshare_market_symbol(symbol),
            indicator=indicator,
        )

    def get_stock_report_disclosure(self, market: str = "沪深京") -> list[dict[str, Any]]:
        """Fetch scheduled financial report disclosure dates."""

        return self._records_from_api("stock_report_disclosure", market=market)

    def get_industry_board_names(self) -> list[dict[str, Any]]:
        """Fetch Eastmoney industry board list."""

        return self._records_from_api("stock_board_industry_name_em")

    def get_industry_board_constituents(self, board_name: str) -> list[dict[str, Any]]:
        """Disabled: industry board constituents need a separate provider.

        The AkShare wrapper re-enters Eastmoney board mapping endpoints and
        repeatedly disconnects under validation. Industry constituents are not
        required by the current daily/fund/disclosure baseline.
        """

        # Disabled original implementation:
        # return self._records_from_api("stock_board_industry_cons_em", symbol=board_name)
        raise DataSourceError(
            "AkShare industry board constituents are disabled; use board names now and add an independent provider later."
        )

    def get_concept_board_names(self) -> list[dict[str, Any]]:
        """Fetch Eastmoney concept board list."""

        return self._records_from_api("stock_board_concept_name_em")

    def get_concept_board_constituents(self, board_name: str) -> list[dict[str, Any]]:
        """Fetch Eastmoney concept board constituents."""

        return self._records_from_api("stock_board_concept_cons_em", symbol=board_name)

    def get_stock_margin_details(
        self,
        *,
        exchange: str,
        trade_date: str,
    ) -> list[dict[str, Any]]:
        """Return normalized stock-level margin records in CNY."""

        normalized_exchange = exchange.lower()
        if normalized_exchange not in {"sh", "sz"}:
            raise ValueError("exchange must be sh or sz")
        api_name = (
            "stock_margin_detail_sse"
            if normalized_exchange == "sh"
            else "stock_margin_detail_szse"
        )
        records = self._records_from_api(
            api_name,
            date=normalize_date_compact(trade_date),
        )
        return [
            {
                "symbol": _security_code(
                    record.get("标的证券代码")
                    or record.get("证券代码")
                ),
                "trade_date": to_date_string(
                    record.get("信用交易日期")
                    or record.get("交易日期")
                    or trade_date
                ),
                "margin_balance": to_optional_float(
                    record.get("融资余额")
                ),
                "margin_buy_amount": to_optional_float(
                    record.get("融资买入额")
                ),
                "margin_repay_amount": to_optional_float(
                    record.get("融资偿还额")
                ),
                "short_balance": to_optional_float(
                    record.get("融券余额")
                    or record.get("融券余量金额")
                ),
                "short_volume": to_optional_float(
                    record.get("融券余量")
                ),
                "short_sell_volume": to_optional_float(
                    record.get("融券卖出量")
                ),
                "short_repay_volume": to_optional_float(
                    record.get("融券偿还量")
                ),
                "total_margin_balance": to_optional_float(
                    record.get("融资融券余额")
                ),
                "source": f"{self.source}:{api_name}",
                "scope": "stock",
            }
            for record in records
            if (
                record.get("标的证券代码") is not None
                or record.get("证券代码") is not None
            )
        ]

    def get_exchange_fund_share_records(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Return official exchange ETF share observations."""

        normalized_exchange = exchange.lower()
        if normalized_exchange == "sh":
            api_name = "fund_etf_scale_sse"
            records = self._records_from_api(
                api_name,
                date=normalize_date_compact(end_date),
            )
        elif normalized_exchange == "sz":
            api_name = "fund_scale_daily_szse"
            records = self._records_from_api(
                api_name,
                start_date=normalize_date_compact(start_date),
                end_date=normalize_date_compact(end_date),
                symbol="ETF",
            )
        else:
            raise ValueError("exchange must be sh or sz")
        return [
            {
                "symbol": _security_code(record.get("基金代码")),
                "stat_date": to_date_string(
                    record.get("统计日期")
                    or record.get("日期")
                ),
                "shares": str(record.get("基金份额")),
                "source": f"{self.source}:{api_name}",
                "split_adjusted": False,
            }
            for record in records
            if record.get("基金代码") is not None
            and record.get("基金份额") is not None
        ]

    def get_exchange_fund_nav_records(
        self,
    ) -> list[dict[str, Any]]:
        """Return the latest two disclosed ETF unit-NAV observations."""

        api_name = "fund_etf_fund_daily_em"
        records = self._records_from_api(api_name)
        result = []
        for record in records:
            symbol = record.get("基金代码")
            if symbol is None:
                continue
            for field_name, field_value in record.items():
                if (
                    not str(field_name).endswith("-单位净值")
                    or field_value in (None, "", "-")
                ):
                    continue
                nav_date = str(field_name)[:10]
                result.append(
                    {
                        "symbol": _security_code(symbol),
                        "nav_date": to_date_string(nav_date),
                        "unit_nav": str(field_value),
                        "source": f"{self.source}:{api_name}",
                        "estimated": True,
                    }
                )
        return result

    def get_lhb_activity_records(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Return normalized Dragon-Tiger List activity summaries."""

        api_name = "stock_lhb_detail_em"
        records = self._records_from_api(
            api_name,
            start_date=normalize_date_compact(start_date),
            end_date=normalize_date_compact(end_date),
        )
        return [
            {
                "symbol": _security_code(record.get("代码")),
                "activity_type": "lhb_listing",
                "trade_date": to_date_string(record.get("上榜日")),
                "published_date": to_date_string(
                    record.get("上榜日")
                ),
                "source": f"{self.source}:{api_name}",
                "net_buy_amount": to_optional_float(
                    record.get("龙虎榜净买额")
                ),
                "turnover_amount": to_optional_float(
                    record.get("市场总成交额")
                ),
                "float_market_cap": to_optional_float(
                    record.get("流通市值")
                ),
                "metadata": {
                    "listing_reason": record.get("上榜原因"),
                    "interpretation": record.get("解读"),
                    "identity_inference": False,
                },
            }
            for record in records
            if record.get("代码") is not None
        ]

    def get_block_trade_activity_records(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Return normalized A-share block-trade activity."""

        api_name = "stock_dzjy_mrmx"
        records = self._records_from_api(
            api_name,
            symbol="A股",
            start_date=normalize_date_compact(start_date),
            end_date=normalize_date_compact(end_date),
        )
        result = []
        for record in records:
            symbol = record.get("证券代码")
            if symbol is None:
                continue
            amount = to_optional_float(record.get("成交额"))
            cap_ratio_percent = to_optional_float(
                record.get("成交额/流通市值")
            )
            float_market_cap = None
            if (
                amount is not None
                and cap_ratio_percent is not None
                and cap_ratio_percent > 0
            ):
                float_market_cap = amount / (
                    cap_ratio_percent / 100.0
                )
            trade_date = to_date_string(record.get("交易日期"))
            result.append(
                {
                    "symbol": _security_code(symbol),
                    "activity_type": "block_trade",
                    "trade_date": trade_date,
                    "published_date": trade_date,
                    "source": f"{self.source}:{api_name}",
                    "amount": amount,
                    "turnover_amount": amount,
                    "trade_price": to_optional_float(
                        record.get("成交价")
                    ),
                    "reference_close": to_optional_float(
                        record.get("收盘价")
                    ),
                    "float_market_cap": float_market_cap,
                    "metadata": {
                        "buyer_branch": record.get("买方营业部"),
                        "seller_branch": record.get("卖方营业部"),
                        "reported_premium_discount_percent": (
                            record.get("折溢率")
                        ),
                        "identity_inference": False,
                    },
                }
            )
        return result

    def call_api(self, api_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Call an AkShare API by name and return DataFrame records.

        Use this for low-frequency or exploratory AkShare APIs that are recorded
        in `capability_matrix.md` but do not yet deserve a stable project
        wrapper.
        """

        return self._records_from_api(api_name, **kwargs)

    def _records_from_api(self, api_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        frame = self._call_dataframe(api_name, **kwargs)
        return dataframe_to_records(frame)

    def _call_dataframe(self, api_name: str, **kwargs: Any) -> Any:
        ak = self._load_akshare()
        api = getattr(ak, api_name, None)
        if api is None:
            raise DataSourceError(f"AkShare API is not available: {api_name}")
        try:
            return api(**kwargs)
        except Exception as exc:  # pragma: no cover - provider/network failure
            raise DataSourceError(f"AkShare API call failed: {api_name}") from exc

    def _load_akshare(self) -> Any:
        if self._akshare is not None:
            return self._akshare
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise DependencyNotInstalledError(
                "AkShare is not installed. Add `akshare` to dependencies and install it."
            ) from exc
        self._akshare = ak
        return ak

    def _daily_bar_from_record(
        self,
        record: dict[str, Any],
        fallback_symbol: str,
        source: str,
    ) -> DailyBar:
        return DailyBar(
            symbol=str(record.get("股票代码") or record.get("代码") or fallback_symbol),
            trade_date=str(record.get("日期")),
            open=to_optional_float(record.get("开盘")),
            high=to_optional_float(record.get("最高")),
            low=to_optional_float(record.get("最低")),
            close=to_optional_float(record.get("收盘")),
            volume=to_optional_float(record.get("成交量")),
            amount=to_optional_float(record.get("成交额")),
            source=source,
            raw=dict(record),
        )

    def _fund_nav_from_record(self, record: dict[str, Any], fallback_symbol: str) -> FundNav:
        return FundNav(
            symbol=str(record.get("基金代码") or record.get("代码") or fallback_symbol),
            nav_date=to_date_string(record.get("净值日期") or record.get("日期")),
            unit_nav=to_optional_float(record.get("单位净值")),
            accumulated_nav=to_optional_float(record.get("累计净值")),
            daily_growth_rate=to_optional_float(record.get("日增长率")),
            source=f"{self.source}:fund_open_fund_info_em",
            estimated=False,
            raw=dict(record),
        )

    def _fund_security_from_record(self, record: dict[str, Any]) -> SecurityInfo:
        return SecurityInfo(
            symbol=str(record.get("基金代码") or record.get("代码") or ""),
            name=record.get("基金简称") or record.get("基金名称") or record.get("简称"),
            security_type=record.get("基金类型") or "fund",
            exchange=None,
            source=f"{self.source}:fund_name_em",
            raw=dict(record),
        )

    def _fund_estimate_from_record(self, record: dict[str, Any]) -> FundEstimate:
        date_value = (
            record.get("估算日期")
            or record.get("净值日期")
            or record.get("日期")
            or record.get("更新时间")
            or ""
        )
        time_value = record.get("估算时间") or record.get("时间") or record.get("更新时间")
        return FundEstimate(
            symbol=str(record.get("基金代码") or record.get("代码") or ""),
            estimate_date=to_date_string(date_value),
            estimate_time=None if time_value is None else str(time_value),
            estimated_nav=to_optional_float(record.get("估算净值") or record.get("估值")),
            estimated_growth_rate=to_optional_float(record.get("估算涨幅") or record.get("估算增长率")),
            source=f"{self.source}:fund_value_estimation_em",
            raw=dict(record),
        )

    def _fund_purchase_status_from_record(self, record: dict[str, Any]) -> FundPurchaseStatus:
        return FundPurchaseStatus(
            symbol=str(record.get("基金代码") or record.get("代码") or ""),
            name=record.get("基金简称") or record.get("基金名称"),
            fund_type=record.get("基金类型"),
            purchase_status=record.get("申购状态"),
            redemption_status=record.get("赎回状态"),
            next_open_date=to_date_string(record.get("下一开放日")) or None,
            min_purchase_amount=to_optional_float(record.get("购买起点")),
            daily_limit_amount=to_optional_float(record.get("日累计限定金额")),
            fee_rate=to_optional_float(record.get("手续费")),
            source=f"{self.source}:fund_purchase_em",
            raw=dict(record),
        )

    def _fund_report_from_record(
        self,
        record: dict[str, Any],
        report_type: str,
        fallback_symbol: str | None = None,
    ) -> FundReportRecord:
        symbol = str(record.get("基金代码") or record.get("代码") or fallback_symbol or "")
        item_name = _first_present(
            record,
            ("基金简称", "股票名称", "债券名称", "行业类别", "资产类型", "项目", "名称"),
        )
        report_date = to_date_string(
            _first_present(record, ("截止时间", "更新日期", "报告期", "季度", "日期", "净值日期"))
        )
        item_value = to_optional_float(_first_present(record, ("市值", "持仓市值", "最近总份额", "总募集规模", "金额")))
        ratio = to_optional_float(_first_present(record, ("占净值比例", "占比", "比例")))
        return FundReportRecord(
            symbol=symbol,
            report_type=report_type,
            report_date=report_date,
            item_name=str(item_name or ""),
            item_value=item_value,
            ratio=ratio,
            source=f"{self.source}:{report_type}",
            raw=dict(record),
        )


def _first_present(record: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def _normalize_minute_datetime(value: str) -> str:
    text = value.strip()
    if ":" in text:
        return text
    return normalize_date_compact(text)


def _security_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6)


def _to_akshare_prefixed_symbol(symbol: str) -> str:
    market_symbol = to_akshare_market_symbol(symbol)
    code, suffix = market_symbol.split(".")
    return f"{suffix}{code}"


def _to_akshare_index_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    if len(value) != 6 or not value.isdigit():
        raise ValueError(
            f"Unsupported mainland index symbol format: {symbol}"
        )
    return value


def _constituent_exchange(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {
        "上海证券交易所",
        "shanghai stock exchange",
        "sse",
        "sh",
    }:
        return "sh"
    if normalized in {
        "深圳证券交易所",
        "shenzhen stock exchange",
        "szse",
        "sz",
    }:
        return "sz"
    if normalized in {
        "北京证券交易所",
        "beijing stock exchange",
        "bse",
        "bj",
    }:
        return "bj"
    return normalized
