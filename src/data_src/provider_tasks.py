"""Importable provider tasks used by process isolation."""

from __future__ import annotations

from .akshare import AkShareDataSource
from .baostock import BaoStockDataSource
from .mootdx import MootdxDataSource
from .sina import SinaExchangeFundDataSource
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
