"""Importable provider tasks used by process isolation."""

from __future__ import annotations

from .akshare import AkShareDataSource
from .baostock import BaoStockDataSource
from .bse import BseDisclosureDataSource
from .cninfo import CninfoDataSource
from .eastmoney import EastmoneyResearchReportDataSource
from .mootdx import MootdxDataSource
from .sina import SinaExchangeFundDataSource
from .sse import SseDisclosureDataSource
from .szse import SzseDisclosureDataSource
from .tencent import TencentExchangeFundDataSource


def fetch_a_share_daily_bars(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "akshare":
        return AkShareDataSource().get_a_share_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
            )
    if source == "mootdx":
        return MootdxDataSource().get_a_share_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "tencent":
        return TencentExchangeFundDataSource().get_a_share_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "sina":
        return SinaExchangeFundDataSource().get_a_share_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(f"Unsupported A-share daily source: {source}")


def fetch_etf_daily_bars(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "akshare":
        return AkShareDataSource().get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
            )
    if source == "mootdx":
        return MootdxDataSource().get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "tencent":
        return TencentExchangeFundDataSource().get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "sina":
        return SinaExchangeFundDataSource().get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(f"Unsupported ETF daily source: {source}")


def fetch_index_daily_bars(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
    exchange: str,
):
    if source == "tencent":
        return TencentExchangeFundDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
        )
    if source == "sina":
        return SinaExchangeFundDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
        )
    if source == "akshare":
        return AkShareDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(f"Unsupported index daily source: {source}")


def fetch_index_constituents(
    *,
    source: str,
    symbol: str,
):
    if source == "akshare":
        return AkShareDataSource().get_index_constituents(symbol)
    raise ValueError(
        f"Unsupported index constituent source: {source}"
    )


def fetch_trade_calendar(
    *,
    source: str,
    start_date: str,
    end_date: str,
):
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_trade_dates(start_date, end_date)
    raise ValueError(f"Unsupported trade calendar source: {source}")


def fetch_open_fund_nav_history(
    *,
    source: str,
    symbol: str,
):
    if source == "akshare":
        return AkShareDataSource().get_open_fund_nav_history_records(symbol)
    raise ValueError(f"Unsupported open fund NAV source: {source}")


def fetch_stock_financial_records(
    *,
    source: str,
    symbol: str,
    dataset: str,
):
    if source != "akshare":
        raise ValueError(
            f"Unsupported stock financial source: {source}"
        )
    provider = AkShareDataSource()
    methods = {
        "stock_income_statements": provider.get_stock_profit_sheet,
        "stock_balance_sheets": provider.get_stock_balance_sheet,
        "stock_cash_flow_statements": (
            provider.get_stock_cash_flow_sheet
        ),
        "stock_financial_indicators": (
            provider.get_stock_financial_indicators
        ),
    }
    method = methods.get(dataset)
    if method is None:
        raise ValueError(
            f"Unsupported stock financial dataset: {dataset}"
        )
    return method(symbol)


def fetch_disclosure_documents(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "cninfo":
        return CninfoDataSource().search_announcements(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    if source == "sse":
        return SseDisclosureDataSource().search_announcements(
            symbol,
            start_date,
            end_date,
        )
    if source == "szse":
        return SzseDisclosureDataSource().search_announcements(
            symbol,
            start_date,
            end_date,
        )
    if source == "bse":
        return BseDisclosureDataSource().search_announcements(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(f"Unsupported disclosure source: {source}")


def fetch_stock_research_reports(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "eastmoney":
        return EastmoneyResearchReportDataSource().search_stock_reports(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(
        f"Unsupported research report source: {source}"
    )


def fetch_fund_estimates(*, source: str):
    if source == "akshare":
        return AkShareDataSource().get_fund_value_estimates()
    raise ValueError(f"Unsupported fund estimate source: {source}")


def fetch_fund_purchase_status(*, source: str):
    if source == "akshare":
        return AkShareDataSource().get_fund_purchase_status_records()
    raise ValueError(
        f"Unsupported fund purchase status source: {source}"
    )


def fetch_fund_scale(
    *,
    source: str,
    fund_type: str,
):
    if source == "akshare":
        return AkShareDataSource().get_fund_scale_records(fund_type)
    raise ValueError(f"Unsupported fund scale source: {source}")


def fetch_fund_allocation(
    *,
    source: str,
    symbol: str,
    year: str,
):
    if source == "akshare":
        return AkShareDataSource().get_fund_industry_allocation(
            symbol,
            year,
        )
    raise ValueError(
        f"Unsupported fund allocation source: {source}"
    )
