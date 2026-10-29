"""Importable provider tasks used by process isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .akshare import AkShareDataSource
from .baostock import BaoStockDataSource
from .bse import BseDisclosureDataSource
from .cninfo import CninfoDataSource
from .csindex import CsIndexDataSource
from .cs_fund import CsFundDisclosureDataSource
from .eastmoney import (
    EastmoneyExchangeFundDataSource,
    EastmoneyResearchReportDataSource,
)
from .mootdx import MootdxDataSource
from .nbs import NBSMacroDataSource
from .sina import SinaExchangeFundDataSource
from .sse import SseDisclosureDataSource
from .szse import SzseDisclosureDataSource
from .tencent import TencentExchangeFundDataSource
from .ths import ThsDataSource
from .total_return_bars import (
    sina_stock_total_return_bars,
    sina_tdx_fund_total_return_bars,
    ths_tdx_total_return_bars,
)


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
    if source == "ths":
        return ThsDataSource().get_a_share_daily_bars(
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
    if source == "ths":
        return ThsDataSource().get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
    raise ValueError(f"Unsupported ETF daily source: {source}")


def fetch_total_return_bars(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
    fund: bool,
):
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
            )
    if source == "sina" and not fund:
        return sina_stock_total_return_bars(symbol, start_date, end_date)
    if source == "sina" and fund:
        return sina_tdx_fund_total_return_bars(symbol, start_date, end_date)
    if source == "akshare" and fund:
        return AkShareDataSource().get_etf_total_return_bars(
            symbol,
            start_date,
            end_date,
        )
    if source == "ths":
        return ths_tdx_total_return_bars(symbol, start_date, end_date)
    raise ValueError(f"Unsupported total-return bar source: {source}")


def fetch_etf_nav_history(
    *,
    source: str,
    asset_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "akshare":
        return AkShareDataSource().get_etf_nav_history_records(
            asset_id=asset_id,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    if source == "eastmoney":
        return EastmoneyExchangeFundDataSource().get_etf_nav_history_records(
            asset_id=asset_id,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    raise ValueError(f"Unsupported ETF NAV source: {source}")


def fetch_etf_distribution_events(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "akshare":
        return AkShareDataSource().get_etf_distribution_events(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    raise ValueError(
        f"Unsupported ETF distribution source: {source}"
    )


def fetch_etf_split_events(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
):
    if source == "akshare":
        return AkShareDataSource().get_etf_split_events(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    raise ValueError(f"Unsupported ETF split source: {source}")


def fetch_index_daily_bars(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
    exchange: str,
):
    if source == "csindex":
        return CsIndexDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
        )
    if source == "eastmoney":
        return EastmoneyExchangeFundDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
        )
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
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
            exchange=exchange,
        )
    if source == "ths":
        return ThsDataSource().get_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
        )
    if source == "sw":
        return AkShareDataSource().get_sw_index_daily_bars(
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


def fetch_shenzhen_a_share_catalog():
    return AkShareDataSource().get_shenzhen_a_share_catalog()


def fetch_shenzhen_name_change_history():
    return AkShareDataSource().get_shenzhen_name_change_history()


def fetch_shanghai_market_pe_history():
    return AkShareDataSource().get_shanghai_market_pe_history()


def fetch_shanghai_market_pb_history():
    return AkShareDataSource().get_shanghai_market_pb_history()


def fetch_index_valuation_history(*, symbol: str):
    return AkShareDataSource().get_index_valuation_history(symbol)


def fetch_csindex_official_valuation(*, symbol: str):
    return AkShareDataSource().get_csindex_official_valuation(symbol)


def fetch_market_activity_snapshot():
    return AkShareDataSource().get_market_activity_snapshot()


def fetch_sw_market_daily_metrics(
    *,
    start_date: str,
    end_date: str,
):
    return AkShareDataSource().get_sw_market_daily_metrics(
        start_date,
        end_date,
    )


def fetch_sw_industry_daily_metrics(
    *,
    start_date: str,
    end_date: str,
):
    return AkShareDataSource().get_sw_industry_daily_metrics(
        start_date,
        end_date,
    )


def fetch_sw_industry_catalog(*, level: int):
    return AkShareDataSource().get_sw_industry_catalog(level)


def fetch_sw_constituents(*, codes: list[str], workers: int = 6):
    """Constituents of many Shenwan indexes; failures are returned per code."""

    provider = AkShareDataSource()
    results: dict[str, object] = {}

    def one(code: str) -> None:
        try:
            results[code] = provider.get_sw_index_constituents(code)
        except Exception as exc:  # noqa: BLE001 - reported per code
            results[code] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, codes))
    # Concurrent calls are intermittently rejected; retry failures one at a time.
    for code in [code for code, outcome in results.items() if isinstance(outcome, str)]:
        one(code)
    return results


def fetch_sw_level_metrics(*, category: str, trade_dates: list[str], workers: int = 4):
    """Shenwan level valuation snapshots on given dates; failures per date."""

    provider = AkShareDataSource()
    results: dict[str, object] = {}

    def one(trade_date: str) -> None:
        try:
            results[trade_date] = provider.get_sw_market_daily_metrics(
                trade_date,
                trade_date,
                category=category,
            )
        except Exception as exc:  # noqa: BLE001 - reported per date
            results[trade_date] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, trade_dates))
    return results


def fetch_market_snapshot(*, workers: int = 8):
    """All listed A shares: Sina prices merged with Tencent valuations."""

    listed = AkShareDataSource().get_a_share_spot_sina()
    tencent = TencentExchangeFundDataSource()
    codes = [f"{item['exchange']}{item['symbol']}" for item in listed]
    batches = [codes[start : start + 60] for start in range(0, len(codes), 60)]
    valuations: dict[str, dict] = {}
    errors: list[str] = []

    def one(batch: list[str]) -> None:
        try:
            for item in tencent.get_stock_quote_valuations(batch):
                valuations[item["symbol"]] = item
        except Exception as exc:  # noqa: BLE001 - reported per batch
            errors.append(f"{batch[0]}..: {type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, batches))
    rows = []
    for item in listed:
        quote = valuations.get(item["symbol"], {})
        rows.append(
            {
                **item,
                "close": quote.get("close", item["close"]),
                "pct_change": quote.get("pct_change", item["pct_change"]),
                "amount": quote.get("amount", item["amount"]),
                "turnover_rate_percent": quote.get("turnover_rate_percent"),
                "pe_ttm": quote.get("pe_ttm"),
                "pe_static": quote.get("pe_static"),
                "pb": quote.get("pb"),
                "market_cap": quote.get("market_cap"),
                "float_market_cap": quote.get("float_market_cap"),
                "quote_date": quote.get("quote_date"),
                "source": item["source"] + ("+tencent:quote" if quote else ""),
            }
        )
    return {"rows": rows, "errors": errors}


def fetch_stock_valuation_histories(*, symbols: list[str], workers: int = 6):
    provider = AkShareDataSource()
    results: dict[str, object] = {}

    def one(symbol: str) -> None:
        try:
            results[symbol] = provider.get_stock_valuation_history(symbol)
        except Exception as exc:  # noqa: BLE001 - reported per symbol
            results[symbol] = f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, symbols))
    return results


def fetch_financial_report_cross_section(*, report_period: str):
    return AkShareDataSource().get_financial_report_cross_section(report_period)


def fetch_delisted_a_shares():
    return AkShareDataSource().get_delisted_a_shares()


def fetch_exchange_listing_segment(*, segment: str):
    return AkShareDataSource().get_exchange_listing_segment(segment)


def fetch_index_members_on(*, index_symbol: str, day: str):
    with BaoStockDataSource() as provider:
        return provider.get_index_members_on(index_symbol, day)


def fetch_latest_china_cpi_yoy():
    return NBSMacroDataSource().get_latest_cpi_yoy()


def fetch_china_ten_year_yield_history(*, start_date: str):
    return AkShareDataSource().get_china_ten_year_yield_history(start_date)


def fetch_50etf_qvix_history():
    return AkShareDataSource().get_50etf_qvix_history()


def fetch_trade_calendar(
    *,
    source: str,
    start_date: str,
    end_date: str,
):
    if source == "baostock":
        with BaoStockDataSource() as provider:
            return provider.get_trade_dates(start_date, end_date)
    if source == "sina":
        return AkShareDataSource().get_trade_dates(start_date, end_date)
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
    if source == "cs_fund_disclosure":
        provider = CsFundDisclosureDataSource()
        documents = {}
        for report_type in (
            "latest",
            "annual",
            "semi_annual",
            "q1",
            "q2",
            "q3",
            "q4",
            "other",
        ):
            for document in provider.search_announcements(
                fund_code=symbol,
                report_type=report_type,
            ):
                if (
                    start_date
                    <= document.announcement_date
                    <= end_date
                ):
                    documents[document.document_id] = document
        return sorted(
            documents.values(),
            key=lambda item: (
                item.announcement_date,
                item.document_id,
            ),
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


def fetch_open_fund_dealing_info(*, symbol: str):
    from src.data_src.eastmoney.fund_dealing import EastmoneyFundDealingDataSource

    return EastmoneyFundDealingDataSource().get_dealing_info(symbol)


def fetch_fund_portfolio_disclosure(*, symbol: str):
    from src.data_src.eastmoney.fund_portfolio import EastmoneyFundPortfolioDataSource

    return EastmoneyFundPortfolioDataSource().get_portfolio(symbol)


def fetch_index_constituent_weights(*, symbol: str) -> list[dict[str, str]]:
    """Latest month-end constituent weights from China Securities Index; weight is a fraction."""

    rows = []
    for record in AkShareDataSource().call_api("index_stock_cons_weight_csindex", symbol=symbol):
        code = str(record.get("成分券代码") or "").strip()
        weight = record.get("权重")
        if not code or weight in (None, ""):
            continue
        rows.append(
            {
                "weight_date": str(record.get("日期"))[:10],
                "index_name": str(record.get("指数名称") or ""),
                "code": code.zfill(6),
                "name": str(record.get("成分券名称") or code),
                "exchange": str(record.get("交易所") or ""),
                "weight": str(float(weight) / 100),
            }
        )
    return rows


def fetch_stock_dividend_events(*, symbol: str) -> list[dict[str, str]]:
    """Implemented cash dividends (Sina history); 派息 is per 10 shares."""

    events = []
    for record in AkShareDataSource().call_api("stock_history_dividend_detail", symbol=symbol, indicator="分红"):
        cash = record.get("派息")
        ex_date = str(record.get("除权除息日") or "")[:10]
        if record.get("进度") != "实施" or not cash or len(ex_date) != 10 or not ex_date[0].isdigit():
            continue
        events.append(
            {
                "announce_date": str(record.get("公告日期"))[:10],
                "ex_date": ex_date,
                "cash_per_share": str(Decimal(str(cash)) / 10),
            }
        )
    return events


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
