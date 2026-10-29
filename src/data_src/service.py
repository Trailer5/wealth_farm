"""Project-facing data fetch service."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from src.data_store import SQLiteDataStore
from src.data_src.akshare import AkShareDataSource
from src.data_src.akshare.provider import EXCHANGE_LISTING_SEGMENTS
from src.data_src.baostock import BaoStockDataSource
from src.data_src.bse import BseDisclosureDataSource
from src.data_src.cninfo import CninfoDataSource
from src.data_src.common import (
    DataSourceError,
    normalize_a_share_code,
    to_akshare_symbol,
)
from src.data_src.csrc import CsrcPublicInfoDataSource
from src.data_src.csindex import CsIndexDataSource
from src.data_src.cs_fund import CsFundDisclosureDataSource
from src.data_src.eastmoney import (
    EastmoneyExchangeFundDataSource,
    EastmoneyResearchReportDataSource,
    EastmoneyStockIndustryDataSource,
)
from src.data_src.fund_company import FundCompanyDisclosureDataSource
from src.data_src.models import (
    DailyBar,
    DataSourceStatus,
    DisclosureDocument,
    DisclosureSourceCheck,
    DocumentTable,
    DocumentText,
    FinancialField,
    DownloadedFile,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
    OCRQueueItem,
    ResearchReport,
    SecurityInfo,
)
from src.data_src.mootdx import MootdxDataSource
from src.data_src.sina import SinaExchangeFundDataSource
from src.data_src.sse import SseDisclosureDataSource
from src.data_src.szse import SzseDisclosureDataSource
from src.data_src.tencent import TencentExchangeFundDataSource
from src.data_src.ths import ThsDataSource
from src.data_src.total_return_bars import (
    sina_stock_total_return_bars,
    sina_tdx_fund_total_return_bars,
    ths_tdx_total_return_bars,
)
from src.data_src.process_executor import run_provider_task
from src.document_processing import (
    FinancialFieldExtractor,
    PaddleOCRTextExtractor,
    PDFTableExtractor,
    PDFTextExtractor,
    TesseractOCRTextExtractor,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    MasterDataService,
    SQLiteMasterDataRepository,
    normalize_asset_type,
)


DEFAULT_EXCHANGE_FUND_SPOT_SOURCES = ["eastmoney", "tencent", "sina", "akshare"]


def _fetch_listing_segment(segment: str) -> list[dict[str, object]]:
    return run_provider_task(
        "src.data_src.provider_tasks",
        "fetch_exchange_listing_segment",
        {"segment": segment},
        timeout_seconds=120.0,
    )

_SEGMENT_TYPES = {
    "sh_stock": ASSET_TYPE_STOCK,
    "sz_stock": ASSET_TYPE_STOCK,
    "bj_stock": ASSET_TYPE_STOCK,
    "sh_delisted": ASSET_TYPE_STOCK,
    "sz_delisted": ASSET_TYPE_STOCK,
    "etf": ASSET_TYPE_EXCHANGE_FUND,
    "lof": ASSET_TYPE_EXCHANGE_FUND,
    "index": ASSET_TYPE_INDEX,
}


@dataclass(frozen=True)
class FetchResult:
    """Result metadata for a fetch-and-store operation."""

    symbol: str
    source: str
    fetched_count: int
    stored_count: int


class DataFetchService:
    """Coordinate external providers and local persistence."""

    def __init__(
        self,
        store: SQLiteDataStore,
        akshare_source: AkShareDataSource | None = None,
        baostock_source: BaoStockDataSource | None = None,
        bse_source: BseDisclosureDataSource | None = None,
        cninfo_source: CninfoDataSource | None = None,
        csrc_source: CsrcPublicInfoDataSource | None = None,
        csindex_source: CsIndexDataSource | None = None,
        cs_fund_source: CsFundDisclosureDataSource | None = None,
        eastmoney_source: EastmoneyExchangeFundDataSource | None = None,
        eastmoney_research_source: EastmoneyResearchReportDataSource | None = None,
        eastmoney_industry_source: EastmoneyStockIndustryDataSource | None = None,
        fund_company_source: FundCompanyDisclosureDataSource | None = None,
        tencent_source: TencentExchangeFundDataSource | None = None,
        sina_source: SinaExchangeFundDataSource | None = None,
        sse_source: SseDisclosureDataSource | None = None,
        szse_source: SzseDisclosureDataSource | None = None,
        pdf_text_extractor: PDFTextExtractor | None = None,
        pdf_table_extractor: PDFTableExtractor | None = None,
        financial_field_extractor: FinancialFieldExtractor | None = None,
        ocr_extractors: dict[str, object] | None = None,
        master_data_service: MasterDataService | None = None,
        mootdx_source: MootdxDataSource | None = None,
        listing_fetcher: Callable[[str], list[dict[str, object]]] | None = None,
    ) -> None:
        self.store = store
        self._listing_fetcher = listing_fetcher or _fetch_listing_segment
        self._master_data = master_data_service
        self.akshare = akshare_source or AkShareDataSource()
        self.baostock = baostock_source or BaoStockDataSource()
        self.bse = bse_source or BseDisclosureDataSource()
        self.cninfo = cninfo_source or CninfoDataSource()
        self.csrc = csrc_source or CsrcPublicInfoDataSource()
        self.csindex = csindex_source or CsIndexDataSource()
        self.cs_fund = cs_fund_source or CsFundDisclosureDataSource()
        self.eastmoney = eastmoney_source or EastmoneyExchangeFundDataSource()
        self.eastmoney_research = eastmoney_research_source or EastmoneyResearchReportDataSource()
        self.eastmoney_industry = (
            eastmoney_industry_source
            or EastmoneyStockIndustryDataSource()
        )
        self.fund_company = fund_company_source or FundCompanyDisclosureDataSource()
        self.tencent = tencent_source or TencentExchangeFundDataSource()
        self.sina = sina_source or SinaExchangeFundDataSource()
        self.sse = sse_source or SseDisclosureDataSource()
        self.szse = szse_source or SzseDisclosureDataSource()
        self.mootdx = mootdx_source or MootdxDataSource()
        self.pdf_text_extractor = pdf_text_extractor or PDFTextExtractor()
        self.pdf_table_extractor = pdf_table_extractor or PDFTableExtractor()
        self.financial_field_extractor = financial_field_extractor or FinancialFieldExtractor()
        self.ocr_extractors = ocr_extractors or {
            "paddleocr": PaddleOCRTextExtractor(),
            "tesseract": TesseractOCRTextExtractor(),
        }

    def initialize(self) -> None:
        """Initialize local storage required by the service."""

        self.store.initialize()

    @property
    def master_data(self) -> MasterDataService:
        """Initialize master data persistence only when identity data is needed."""

        if self._master_data is None:
            self._master_data = MasterDataService(
                SQLiteMasterDataRepository(self.store.db_path)
            )
        return self._master_data

    def fetch_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "baostock",
    ) -> list[DailyBar]:
        """Fetch A-share daily bars from one configured source."""

        if source == "baostock":
            return self.baostock.get_a_share_daily_bars(symbol, start_date, end_date)
        if source == "akshare":
            return self.akshare.get_a_share_daily_bars(symbol, start_date, end_date)
        if source == "mootdx":
            return self.mootdx.get_a_share_daily_bars(symbol, start_date, end_date)
        if source == "tencent":
            return self.tencent.get_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
            )
        if source == "sina":
            return self.sina.get_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
            )
        if source == "ths":
            return ThsDataSource().get_a_share_daily_bars(symbol, start_date, end_date)
        raise ValueError(f"Unsupported A-share daily bar source: {source}")

    def fetch_trade_calendar(
        self,
        start_date: str,
        end_date: str,
        source: str = "baostock",
        timeout_seconds: float | None = None,
    ) -> list[dict[str, object]]:
        """Fetch exchange trading calendar records."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_trade_calendar",
                {
                    "source": source,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "baostock":
            return self.baostock.get_trade_dates(start_date, end_date)
        if source == "sina":
            return self.akshare.get_trade_dates(start_date, end_date)
        raise ValueError(f"Unsupported trade calendar source: {source}")

    def fetch_and_store_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "baostock",
        timeout_seconds: float | None = None,
    ) -> FetchResult:
        """Fetch A-share daily bars and persist them to SQLite."""

        self.initialize()
        fetched_bars = (
            run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_a_share_daily_bars",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout_seconds=timeout_seconds,
            )
            if timeout_seconds is not None
            else self.fetch_a_share_daily_bars(
                symbol,
                start_date,
                end_date,
                source,
            )
        )
        bars = [
            replace(bar, symbol=normalize_a_share_code(bar.symbol))
            for bar in fetched_bars
        ]
        stored_count = self.store.upsert_stock_daily_bars(bars)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source=source,
            fetched_count=len(bars),
            stored_count=stored_count,
        )

    def compare_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        tolerance_pct: float = 1.0,
    ) -> list[dict[str, object]]:
        """Compare BaoStock and AkShare daily bars by date and core fields."""

        left = self.baostock.get_a_share_daily_bars(symbol, start_date, end_date)
        right = self.akshare.get_a_share_daily_bars(symbol, start_date, end_date)
        checks = compare_daily_bars(
            symbol=normalize_a_share_code(symbol),
            left_source="baostock",
            right_source="akshare",
            left_bars=left,
            right_bars=right,
            tolerance_pct=tolerance_pct,
        )
        self.initialize()
        self.store.insert_source_checks(checks)
        return checks

    def fetch_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "akshare",
    ) -> list[DailyBar]:
        """Fetch exchange-traded fund daily bars from one configured source."""

        if source == "akshare":
            return self.akshare.get_etf_daily_bars(symbol, start_date, end_date)
        if source == "baostock":
            return self.baostock.get_a_share_daily_bars(symbol, start_date, end_date)
        if source == "mootdx":
            return self.mootdx.get_etf_daily_bars(symbol, start_date, end_date)
        if source == "tencent":
            return self.tencent.get_etf_daily_bars(
                symbol,
                start_date,
                end_date,
            )
        if source == "sina":
            return self.sina.get_etf_daily_bars(
                symbol,
                start_date,
                end_date,
            )
        if source == "ths":
            return ThsDataSource().get_etf_daily_bars(symbol, start_date, end_date)
        raise ValueError(f"Unsupported ETF daily bar source: {source}")

    def fetch_exchange_fund_spot(
        self,
        source: str,
        symbols: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Fetch exchange-traded fund spot records from a selected source."""

        if source == "eastmoney":
            return self.eastmoney.get_exchange_fund_spot()
        if source == "tencent":
            return self.tencent.get_exchange_fund_spot(symbols or [])
        if source == "sina":
            return self.sina.get_exchange_fund_spot(symbols or [])
        if source == "akshare":
            return self.akshare.get_etf_spot()
        raise ValueError(f"Unsupported exchange fund spot source: {source}")

    def choose_exchange_fund_spot_source(self, sources: list[str] | None = None) -> str:
        """Choose the best exchange-fund spot source from recorded health."""

        self.initialize()
        candidate_sources = sources or DEFAULT_EXCHANGE_FUND_SPOT_SOURCES
        priority = {source: index for index, source in enumerate(candidate_sources)}
        rows = [
            row
            for row in self.store.get_data_source_status("exchange_fund_spot")
            if row["source"] in priority and row["status"] == "ok"
        ]
        if not rows:
            return candidate_sources[0]
        rows.sort(
            key=lambda row: (
                -(row["field_completeness"] or 0.0),
                -row["success_count"],
                row["failure_count"],
                priority[row["source"]],
            )
        )
        return str(rows[0]["source"])

    def fetch_exchange_fund_spot_with_fallback(
        self,
        symbols: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Fetch exchange-fund spot records using preferred source with fallback."""

        candidate_sources = sources or DEFAULT_EXCHANGE_FUND_SPOT_SOURCES
        preferred = self.choose_exchange_fund_spot_source(candidate_sources)
        ordered_sources = [preferred] + [source for source in candidate_sources if source != preferred]
        last_error: Exception | None = None
        for source in ordered_sources:
            try:
                rows = self.fetch_exchange_fund_spot(source, symbols)
                if rows:
                    return rows
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return []

    def compare_exchange_fund_spot(
        self,
        symbols: list[str],
        sources: list[str] | None = None,
        tolerance_pct: float = 1.0,
    ) -> list[dict[str, object]]:
        """Compare exchange-fund spot fields across selected sources."""

        selected_sources = sources or ["eastmoney", "tencent", "sina"]
        records_by_source = {
            source: self.fetch_exchange_fund_spot(source, symbols)
            for source in selected_sources
        }
        checks = compare_exchange_fund_spot_records(
            records_by_source=records_by_source,
            symbols=symbols,
            tolerance_pct=tolerance_pct,
        )
        self.initialize()
        self.store.insert_source_checks(checks)
        return checks

    def assess_exchange_fund_sources(
        self,
        sources: list[str] | None = None,
        symbols: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[DataSourceStatus]:
        """Assess exchange-fund source availability and field completeness."""

        self.initialize()
        selected_sources = sources or ["eastmoney", "tencent", "sina", "akshare"]
        checked_at = _format_time(now or datetime.now())
        statuses: list[DataSourceStatus] = []
        for source in selected_sources:
            try:
                rows = self.fetch_exchange_fund_spot(source, symbols)
                completeness = _field_completeness(
                    rows,
                    fields=("symbol", "name", "fund_type", "exchange", "latest", "volume", "amount"),
                )
                status = "ok" if rows else "empty"
                statuses.append(
                    DataSourceStatus(
                        source=source,
                        category="exchange_fund_spot",
                        status=status,
                        checked_at=checked_at,
                        field_completeness=completeness,
                        record_count=len(rows),
                        raw={"sample": rows[:3]},
                    )
                )
            except Exception as exc:
                statuses.append(
                    DataSourceStatus(
                        source=source,
                        category="exchange_fund_spot",
                        status="failed",
                        checked_at=checked_at,
                        field_completeness=0.0,
                        record_count=0,
                        error=str(exc),
                    )
                )
        self.store.upsert_data_source_status(statuses)
        return statuses

    def fetch_and_store_etf_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> FetchResult:
        """Fetch exchange-traded fund daily bars and persist them to SQLite."""

        self.initialize()
        fetched_bars = (
            run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_etf_daily_bars",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout_seconds=timeout_seconds,
            )
            if timeout_seconds is not None
            else self.fetch_etf_daily_bars(
                symbol,
                start_date,
                end_date,
                source,
            )
        )
        bars = [
            replace(bar, symbol=normalize_a_share_code(bar.symbol))
            for bar in fetched_bars
        ]
        stored_count = self.store.upsert_fund_exchange_daily_bars(bars)
        return FetchResult(
            symbol=to_akshare_symbol(symbol),
            source=source,
            fetched_count=len(bars),
            stored_count=stored_count,
        )

    def fetch_total_return_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        *,
        fund: bool,
        source: str = "baostock",
    ) -> list[DailyBar]:
        """Fetch unadjusted bars that carry the exchange reference close."""

        if source == "baostock":
            return self.baostock.get_a_share_daily_bars(symbol, start_date, end_date)
        if source == "sina" and not fund:
            return sina_stock_total_return_bars(
                symbol,
                start_date,
                end_date,
                sina=self.sina,
                akshare=self.akshare,
            )
        if source == "sina" and fund:
            return sina_tdx_fund_total_return_bars(
                symbol, start_date, end_date, akshare=self.akshare, mootdx=self.mootdx
            )
        if source == "akshare" and fund:
            return self.akshare.get_etf_total_return_bars(
                symbol,
                start_date,
                end_date,
            )
        if source == "ths":
            return ths_tdx_total_return_bars(
                symbol,
                start_date,
                end_date,
                mootdx=self.mootdx,
            )
        raise ValueError(f"Unsupported total-return bar source: {source}")

    def fetch_and_store_total_return_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        *,
        fund: bool,
        source: str = "baostock",
        timeout_seconds: float | None = None,
    ) -> FetchResult:
        """Fetch total-return bars and persist them to SQLite."""

        self.initialize()
        fetched_bars = (
            run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_total_return_bars",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                    "fund": fund,
                },
                timeout_seconds=timeout_seconds,
            )
            if timeout_seconds is not None
            else self.fetch_total_return_bars(
                symbol,
                start_date,
                end_date,
                fund=fund,
                source=source,
            )
        )
        bars = [
            replace(bar, symbol=normalize_a_share_code(bar.symbol))
            for bar in fetched_bars
            if bar.preclose is not None
        ]
        stored_count = self.store.upsert_total_return_bars(
            "fund_exchange_total_return_bars" if fund else "stock_total_return_bars",
            bars,
        )
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source=source,
            fetched_count=len(bars),
            stored_count=stored_count,
        )

    def fetch_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[DailyBar]:
        """Fetch normalized mainland index daily bars."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_index_daily_bars",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                    "exchange": exchange,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "baostock":
            return self.baostock.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
        if source == "csindex":
            return self.csindex.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
        if source == "eastmoney":
            return self.eastmoney.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
        if source == "tencent":
            return self.tencent.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
        if source == "sina":
            return self.sina.get_index_daily_bars(
                symbol,
                start_date,
                end_date,
                exchange,
            )
        if source == "akshare":
            return self.akshare.get_index_daily_bars(
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
            return self.akshare.get_sw_index_daily_bars(
                symbol,
                start_date,
                end_date,
            )
        raise ValueError(f"Unsupported index daily source: {source}")

    def fetch_and_store_index_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        exchange: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> FetchResult:
        """Fetch index daily bars and persist them to SQLite."""

        self.initialize()
        bars = self.fetch_index_daily_bars(
            symbol,
            start_date,
            end_date,
            exchange,
            source=source,
            timeout_seconds=timeout_seconds,
        )
        stored_count = self.store.upsert_index_daily_bars(bars)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source=source,
            fetched_count=len(bars),
            stored_count=stored_count,
        )

    def fetch_index_constituents(
        self,
        symbol: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ):
        """Fetch one current index constituent snapshot."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_index_constituents",
                {
                    "source": source,
                    "symbol": symbol,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "akshare":
            return self.akshare.get_index_constituents(symbol)
        raise ValueError(
            f"Unsupported index constituent source: {source}"
        )

    def fetch_gold_spot_history(
        self,
    ) -> list[dict[str, object]]:
        return self.akshare.get_gold_spot_history()

    def fetch_copper_continuous_history(
        self,
    ) -> list[dict[str, object]]:
        return self.akshare.get_copper_continuous_history()

    def fetch_stock_industries(
        self,
        symbols: list[str],
    ):
        """Eastmoney industries, with BaoStock CSRC industries for gaps."""

        try:
            records = list(
                self.eastmoney_industry.get_stock_industries(symbols)
            )
        except (OSError, RuntimeError, ValueError):
            records = []
        found = {item.symbol for item in records}
        missing = [item for item in symbols if item not in found]
        if missing:
            records.extend(self.baostock.get_stock_industry_records(missing))
        return records

    def fetch_open_fund_nav_history(
        self,
        symbol: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[FundNav]:
        """Fetch normalized open-end fund NAV history."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_open_fund_nav_history",
                {
                    "source": source,
                    "symbol": symbol,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "akshare":
            return self.akshare.get_open_fund_nav_history_records(symbol)
        raise ValueError(f"Unsupported open fund NAV source: {source}")

    def fetch_and_store_open_fund_nav_history(
        self,
        symbol: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> FetchResult:
        """Fetch open-end fund NAV history and persist it to SQLite."""

        self.initialize()
        navs = self.fetch_open_fund_nav_history(
            symbol,
            source,
            timeout_seconds=timeout_seconds,
        )
        stored_count = self.store.upsert_fund_nav_daily(navs)
        return FetchResult(
            symbol=to_akshare_symbol(symbol),
            source=source,
            fetched_count=len(navs),
            stored_count=stored_count,
        )

    def fetch_stock_basic_info(
        self,
        symbol: str | None = None,
        source: str = "baostock",
    ) -> list[SecurityInfo]:
        """Fetch normalized stock basic information."""

        if source == "baostock":
            return self.baostock.get_stock_basic_records(symbol)
        raise ValueError(f"Unsupported stock basic info source: {source}")

    def fetch_and_store_stock_basic_info(
        self,
        symbol: str | None = None,
        source: str = "baostock",
    ) -> FetchResult:
        """Fetch stock basic information and persist it to SQLite."""

        self.initialize()
        securities = self.fetch_stock_basic_info(symbol, source)
        resolutions = [
            self.master_data.ingest_security_info(security)
            for security in securities
        ]
        return FetchResult(
            symbol=normalize_a_share_code(symbol) if symbol else "ALL",
            source=source,
            fetched_count=len(securities),
            stored_count=len(resolutions),
        )

    def fetch_fund_basic_info(
        self,
        source: str = "akshare",
        *,
        symbol: str | None = None,
    ) -> list[SecurityInfo]:
        """Fetch public fund identities, optionally filtered to one code."""

        if source != "akshare":
            raise ValueError(f"Unsupported fund basic info source: {source}")
        securities = self.akshare.get_fund_names()
        if symbol is None:
            return securities
        standard_symbol = normalize_a_share_code(symbol)
        matches = [
            security
            for security in securities
            if normalize_a_share_code(security.symbol) == standard_symbol
        ]
        if len(matches) != 1:
            raise ValueError(
                "fund symbol must resolve to exactly one provider identity: "
                f"{standard_symbol}"
            )
        return matches

    def fetch_and_store_fund_basic_info(
        self,
        source: str = "akshare",
        *,
        symbol: str | None = None,
    ) -> FetchResult:
        """Fetch public fund identities and persist them to SQLite."""

        self.initialize()
        securities = self.fetch_fund_basic_info(symbol=symbol, source=source)
        resolutions = [
            self.master_data.ingest_security_info(security)
            for security in securities
        ]
        return FetchResult(
            symbol=normalize_a_share_code(symbol) if symbol else "ALL",
            source=source,
            fetched_count=len(securities),
            stored_count=len(resolutions),
        )

    def fetch_and_store_identities(
        self,
        symbol: str,
        *,
        asset_type: str | None = None,
    ) -> FetchResult:
        """Register every exchange listing and fund that uses one code.

        Without an asset type both the exchange listing and the fund catalog
        are consulted, because one code can be an index, a stock and a fund.
        Raises DataSourceError when any consulted source fails: resolving from
        a partial lookup could silently pick the wrong product.
        """

        code = normalize_a_share_code(symbol)
        exchange_types = {
            ASSET_TYPE_STOCK,
            ASSET_TYPE_INDEX,
            ASSET_TYPE_EXCHANGE_FUND,
        }
        try:
            listings = (
                self.baostock.get_exchange_listings(code)
                if asset_type is None or asset_type in exchange_types
                else []
            )
        except DataSourceError as exc:
            listings = self._directory_listings(code, asset_type, exc)
        except Exception as exc:  # Provider libraries raise arbitrary errors.
            listings = self._directory_listings(
                code, asset_type, DataSourceError(f"identity lookup failed for {code}: {exc}")
            )
        try:
            funds = (
                [
                    item
                    for item in self.akshare.get_fund_names()
                    if item.symbol.strip() == code
                ]
                if asset_type in (None, ASSET_TYPE_OPEN_FUND)
                else []
            )
        except DataSourceError:
            raise
        except Exception as exc:  # Provider libraries raise arbitrary errors.
            raise DataSourceError(
                f"identity lookup failed for {code}: {exc}"
            ) from exc
        listed_etf = any(
            normalize_asset_type(item.security_type)
            == ASSET_TYPE_EXCHANGE_FUND
            for item in listings
        )
        # The fund catalog also lists exchange ETFs; keep only one identity.
        funds = [
            item
            for item in funds
            if not (listed_etf and "ETF" in (item.name or "").upper())
        ]
        securities = [
            item
            for item in (*listings, *funds)
            if normalize_asset_type(item.security_type)
            in (exchange_types | {ASSET_TYPE_OPEN_FUND})
            and (
                asset_type is None
                or normalize_asset_type(item.security_type) == asset_type
            )
        ]
        self.initialize()
        resolutions = [
            self.master_data.ingest_security_info(security)
            for security in securities
        ]
        return FetchResult(
            symbol=code,
            source="baostock+akshare",
            fetched_count=len(listings) + len(funds),
            stored_count=len(resolutions),
        )

    def refresh_exchange_listings(
        self,
        segments: tuple[str, ...] = EXCHANGE_LISTING_SEGMENTS,
        *,
        max_age_hours: float = 24.0,
    ) -> dict[str, object]:
        """Refresh stale segments of the exchange listing directory; per segment: row count, "cached" or an error."""

        self.initialize()
        known = self.store.get_exchange_listing_segments()
        now = datetime.now(timezone.utc)
        outcome: dict[str, object] = {}
        for segment in segments:
            fetched = known.get(segment)
            if fetched and now - datetime.fromisoformat(fetched["fetched_at"]) < timedelta(hours=max_age_hours):
                outcome[segment] = "cached"
                continue
            try:
                rows = self._listing_fetcher(segment)
            except DataSourceError:
                # Full-list endpoints drop connections now and then; one more try before giving up.
                try:
                    rows = self._listing_fetcher(segment)
                except DataSourceError as exc:
                    outcome[segment] = f"error: {exc}"
                    continue
            if not rows:
                outcome[segment] = "error: empty list"
                continue
            outcome[segment] = self.store.replace_exchange_listing_segment(
                segment, rows, fetched_at=now.isoformat()
            )
        return outcome

    def _directory_listings(
        self,
        code: str,
        asset_type: str | None,
        error: DataSourceError,
    ) -> list[SecurityInfo]:
        """Identity from the exchanges' own lists when BaoStock is unavailable.

        Every segment the asset type needs must have been fetched at least once; otherwise the
        original error is raised, because a partial directory could miss the right product.
        """

        segments = tuple(
            segment
            for segment in EXCHANGE_LISTING_SEGMENTS
            if asset_type is None or _SEGMENT_TYPES[segment] == asset_type
        )
        if not segments:
            return []
        self.refresh_exchange_listings(segments)
        available = self.store.get_exchange_listing_segments()
        if any(segment not in available for segment in segments):
            raise error
        seen: set[tuple[str, str]] = set()
        securities = []
        for row in self.store.get_exchange_listings(code=code):
            if row["segment"] not in segments or (row["exchange"], row["asset_type"]) in seen:
                continue
            seen.add((row["exchange"], row["asset_type"]))
            securities.append(
                SecurityInfo(
                    symbol=f"{row['exchange']}.{code}",
                    name=row["name"],
                    security_type=row["asset_type"],
                    exchange=row["exchange"],
                    source=f"exchange_listing:{row['source']}",
                    raw={
                        key: row[key]
                        for key in ("segment", "listed_on", "delisted_on", "board")
                    },
                )
            )
        return securities

    def fetch_fund_value_estimates(
        self,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[FundEstimate]:
        """Fetch platform-estimated fund values."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_fund_estimates",
                {"source": source},
                timeout_seconds=timeout_seconds,
            )
        if source == "akshare":
            return self.akshare.get_fund_value_estimates()
        raise ValueError(f"Unsupported fund estimate source: {source}")

    def fetch_and_store_fund_value_estimates(self, source: str = "akshare") -> FetchResult:
        """Fetch platform-estimated fund values and persist them separately."""

        self.initialize()
        estimates = self.fetch_fund_value_estimates(source)
        stored_count = self.store.upsert_fund_estimates(estimates)
        return FetchResult(
            symbol="ALL",
            source=source,
            fetched_count=len(estimates),
            stored_count=stored_count,
        )

    def fetch_fund_purchase_status(
        self,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[FundPurchaseStatus]:
        """Fetch fund purchase and redemption statuses."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_fund_purchase_status",
                {"source": source},
                timeout_seconds=timeout_seconds,
            )
        if source == "akshare":
            return self.akshare.get_fund_purchase_status_records()
        raise ValueError(f"Unsupported fund purchase status source: {source}")

    def fetch_and_store_fund_purchase_status(self, source: str = "akshare") -> FetchResult:
        """Fetch fund purchase and redemption statuses and persist them."""

        self.initialize()
        statuses = self.fetch_fund_purchase_status(source)
        stored_count = self.store.upsert_fund_purchase_status(statuses)
        return FetchResult(
            symbol="ALL",
            source=source,
            fetched_count=len(statuses),
            stored_count=stored_count,
        )

    def fetch_fund_scale_records(
        self,
        fund_type: str = "股票型基金",
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[FundReportRecord]:
        """Fetch open-end fund scale records."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_fund_scale",
                {
                    "source": source,
                    "fund_type": fund_type,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "akshare":
            return self.akshare.get_fund_scale_records(fund_type)
        raise ValueError(f"Unsupported fund scale source: {source}")

    def fetch_and_store_fund_scale_records(
        self,
        fund_type: str = "股票型基金",
        source: str = "akshare",
    ) -> FetchResult:
        """Fetch open-end fund scale records and persist them."""

        self.initialize()
        records = self.fetch_fund_scale_records(fund_type, source)
        stored_count = self.store.upsert_fund_report_records(records)
        return FetchResult(symbol="ALL", source=source, fetched_count=len(records), stored_count=stored_count)

    def fetch_and_store_fund_portfolio_records(
        self,
        symbol: str,
        year: str,
        report_type: str,
        source: str = "akshare",
    ) -> FetchResult:
        """Fetch fund holding or allocation records and persist them."""

        self.initialize()
        records = self.fetch_fund_portfolio_records(symbol, year, report_type, source)
        stored_count = self.store.upsert_fund_report_records(records)
        return FetchResult(symbol=to_akshare_symbol(symbol), source=source, fetched_count=len(records), stored_count=stored_count)

    def fetch_fund_portfolio_records(
        self,
        symbol: str,
        year: str,
        report_type: str,
        source: str = "akshare",
    ) -> list[FundReportRecord]:
        """Fetch fund holding or allocation records by report type."""

        if source != "akshare":
            raise ValueError(f"Unsupported fund portfolio source: {source}")
        normalized_type = report_type.strip().lower()
        if normalized_type in {"stock", "stock_holding", "stock_holdings"}:
            # Experimental: live AkShare stock holdings currently fail parsing;
            # prefer official fund disclosure PDF extraction for production use.
            return self.akshare.get_fund_stock_holdings(symbol, year)
        if normalized_type in {"bond", "bond_holding", "bond_holdings"}:
            # Experimental: live AkShare bond holdings currently fail parsing;
            # prefer official fund disclosure PDF extraction for production use.
            return self.akshare.get_fund_bond_holdings(symbol, year)
        if normalized_type in {"industry", "industry_allocation"}:
            return self.akshare.get_fund_industry_allocation(symbol, year)
        raise ValueError(f"Unsupported fund portfolio report type: {report_type}")

    def fetch_fund_allocation_records(
        self,
        symbol: str,
        year: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[FundReportRecord]:
        """Fetch normalized fund industry allocation records."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_fund_allocation",
                {
                    "source": source,
                    "symbol": symbol,
                    "year": year,
                },
                timeout_seconds=timeout_seconds,
            )
        return self.fetch_fund_portfolio_records(
            symbol,
            year,
            "industry_allocation",
            source,
        )

    def fetch_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        category: str = "",
        search_key: str = "",
    ) -> list[DisclosureDocument]:
        """Fetch official disclosure document metadata from CNINFO."""

        return self.cninfo.search_announcements(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            category=category,
            search_key=search_key,
        )

    def fetch_disclosure_documents_by_source(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str,
        timeout_seconds: float | None = None,
    ) -> list[DisclosureDocument]:
        """Fetch one symbol's disclosure metadata from one source."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_disclosure_documents",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout_seconds=timeout_seconds,
            )
        methods = {
            "cninfo": self.fetch_disclosure_documents,
            "sse": self.fetch_sse_disclosure_documents,
            "szse": self.fetch_szse_disclosure_documents,
            "bse": self.fetch_bse_disclosure_documents,
            "cs_fund_disclosure": (
                self.fetch_cs_fund_disclosure_documents_for_range
            ),
        }
        method = methods.get(source)
        if method is None:
            raise ValueError(
                f"Unsupported disclosure source: {source}"
            )
        return method(symbol, start_date, end_date)

    def fetch_and_store_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        category: str = "",
        search_key: str = "",
    ) -> FetchResult:
        """Fetch official disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_disclosure_documents(symbol, start_date, end_date, category, search_key)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source="cninfo",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_financial_report_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        report_type: str,
    ) -> list[DisclosureDocument]:
        """Fetch official financial report documents by report type."""

        category = _cninfo_report_category(report_type)
        return self.fetch_disclosure_documents(symbol, start_date, end_date, category=category)

    def fetch_stock_financial_records(
        self,
        symbol: str,
        dataset: str,
        source: str = "akshare",
        timeout_seconds: float | None = None,
    ) -> list[dict[str, object]]:
        """Fetch one raw A-share financial dataset."""

        if source != "akshare":
            raise ValueError(
                f"Unsupported stock financial source: {source}"
            )
        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_stock_financial_records",
                {
                    "source": source,
                    "symbol": symbol,
                    "dataset": dataset,
                },
                timeout_seconds=timeout_seconds,
            )
        methods = {
            "stock_income_statements": (
                self.akshare.get_stock_profit_sheet
            ),
            "stock_balance_sheets": (
                self.akshare.get_stock_balance_sheet
            ),
            "stock_cash_flow_statements": (
                self.akshare.get_stock_cash_flow_sheet
            ),
            "stock_financial_indicators": (
                self.akshare.get_stock_financial_indicators
            ),
        }
        method = methods.get(dataset)
        if method is None:
            raise ValueError(
                f"Unsupported stock financial dataset: {dataset}"
            )
        return [
            dict(record)
            for record in method(symbol)
        ]

    def fetch_sse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        report_type: str = "ALL",
    ) -> list[DisclosureDocument]:
        """Fetch official disclosure document metadata from SSE."""

        return self.sse.search_announcements(symbol, start_date, end_date, report_type=report_type)

    def fetch_and_store_sse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        report_type: str = "ALL",
    ) -> FetchResult:
        """Fetch SSE disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_sse_disclosure_documents(symbol, start_date, end_date, report_type)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source="sse",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_szse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        channel_code: str = SzseDisclosureDataSource.CHANNEL_LISTED_NOTICE,
    ) -> list[DisclosureDocument]:
        """Fetch official disclosure document metadata from SZSE."""

        return self.szse.search_announcements(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            channel_code=channel_code,
        )

    def fetch_and_store_szse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        channel_code: str = SzseDisclosureDataSource.CHANNEL_LISTED_NOTICE,
    ) -> FetchResult:
        """Fetch SZSE disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_szse_disclosure_documents(symbol, start_date, end_date, channel_code)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source="szse",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_bse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        category: str = "",
        search_key: str = "",
    ) -> list[DisclosureDocument]:
        """Fetch BSE disclosure metadata through CNINFO coverage."""

        return self.bse.search_announcements(symbol, start_date, end_date, category=category, search_key=search_key)

    def fetch_and_store_bse_disclosure_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        category: str = "",
        search_key: str = "",
    ) -> FetchResult:
        """Fetch BSE disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_bse_disclosure_documents(symbol, start_date, end_date, category, search_key)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source="bse_cninfo",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_cs_fund_disclosure_documents(
        self,
        fund_code: str | None = None,
        report_type: str = "latest",
    ) -> list[DisclosureDocument]:
        """Fetch fund disclosure metadata from China Securities Journal pages."""

        return self.cs_fund.search_announcements(fund_code=fund_code, report_type=report_type)

    def fetch_cs_fund_disclosure_documents_for_range(
        self,
        fund_code: str,
        start_date: str,
        end_date: str,
    ) -> list[DisclosureDocument]:
        """Fetch current official fund document categories in a date range."""

        documents: dict[str, DisclosureDocument] = {}
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
            for document in self.fetch_cs_fund_disclosure_documents(
                fund_code=fund_code,
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

    def fetch_and_store_cs_fund_disclosure_documents(
        self,
        fund_code: str | None = None,
        report_type: str = "latest",
    ) -> FetchResult:
        """Fetch CS fund disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_cs_fund_disclosure_documents(fund_code=fund_code, report_type=report_type)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=fund_code or "ALL",
            source="cs_fund_disclosure",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_fund_company_disclosure_documents(
        self,
        page_urls: list[str],
        fund_code: str | None = None,
        keyword: str = "",
        category: str = "fund_company",
    ) -> list[DisclosureDocument]:
        """Fetch fund company official website disclosure metadata."""

        return self.fund_company.search_announcements(
            page_urls=page_urls,
            fund_code=fund_code,
            keyword=keyword,
            category=category,
        )

    def fetch_and_store_fund_company_disclosure_documents(
        self,
        page_urls: list[str],
        fund_code: str | None = None,
        keyword: str = "",
        category: str = "fund_company",
    ) -> FetchResult:
        """Fetch fund company disclosure metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_fund_company_disclosure_documents(page_urls, fund_code, keyword, category)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol=fund_code or "ALL",
            source="fund_company",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def compare_disclosure_sources(
        self,
        left_documents: list[DisclosureDocument],
        right_documents: list[DisclosureDocument],
        left_files: list[DownloadedFile] | None = None,
        right_files: list[DownloadedFile] | None = None,
    ) -> list[DisclosureSourceCheck]:
        """Compare disclosure metadata and downloaded-file hashes across sources."""

        checks = compare_disclosure_documents(
            left_documents=left_documents,
            right_documents=right_documents,
            left_files=left_files or [],
            right_files=right_files or [],
        )
        self.initialize()
        self.store.insert_disclosure_source_checks(checks)
        return checks

    def fetch_csrc_public_documents(
        self,
        page_urls: list[str],
        keyword: str = "",
        category: str = "csrc_public",
    ) -> list[DisclosureDocument]:
        """Fetch CSRC public information metadata from configured pages."""

        return self.csrc.search_public_documents(page_urls=page_urls, keyword=keyword, category=category)

    def fetch_and_store_csrc_public_documents(
        self,
        page_urls: list[str],
        keyword: str = "",
        category: str = "csrc_public",
    ) -> FetchResult:
        """Fetch CSRC public information metadata and persist it to SQLite."""

        self.initialize()
        documents = self.fetch_csrc_public_documents(page_urls=page_urls, keyword=keyword, category=category)
        stored_count = self.store.upsert_disclosure_documents(documents)
        return FetchResult(
            symbol="CSRC",
            source="csrc_public",
            fetched_count=len(documents),
            stored_count=stored_count,
        )

    def fetch_and_store_financial_report_documents(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        report_type: str,
    ) -> FetchResult:
        """Fetch official financial report metadata and persist it to SQLite."""

        category = _cninfo_report_category(report_type)
        return self.fetch_and_store_disclosure_documents(symbol, start_date, end_date, category=category)

    def download_and_store_disclosure_files(
        self,
        documents: list[DisclosureDocument],
        root_dir: str = "data/disclosures",
    ) -> list[DownloadedFile]:
        """Download official disclosure PDFs and persist file metadata."""

        self.initialize()
        provider_by_source = {
            self.cninfo.source: self.cninfo,
            self.cs_fund.source: self.cs_fund,
            self.fund_company.source: self.fund_company,
        }
        files = [
            provider_by_source.get(
                document.source,
                self.cninfo,
            ).download_pdf(
                document,
                root_dir=root_dir,
            )
            for document in documents
        ]
        self.store.upsert_document_files(files)
        return files

    def extract_and_store_document_texts(
        self,
        files: list[DownloadedFile],
        enqueue_ocr: bool = True,
    ) -> list[DocumentText]:
        """Extract text from downloaded disclosure files and persist results."""

        self.initialize()
        texts = [self.pdf_text_extractor.extract_text(file) for file in files]
        self.store.upsert_document_texts(texts)
        if enqueue_ocr:
            self.enqueue_ocr_for_unreadable_documents(files, texts)
        return texts

    def enqueue_ocr_for_unreadable_documents(
        self,
        files: list[DownloadedFile],
        texts: list[DocumentText],
    ) -> list[OCRQueueItem]:
        """Queue failed or empty text extraction results for later OCR."""

        file_by_document = {file.document_id: file for file in files}
        items: list[OCRQueueItem] = []
        for text in texts:
            if text.status not in {"failed", "empty"}:
                continue
            downloaded_file = file_by_document.get(text.document_id)
            if downloaded_file is None:
                continue
            items.append(
                OCRQueueItem(
                    document_id=text.document_id,
                    file_path=downloaded_file.file_path,
                    reason=text.status,
                    status="queued",
                    attempts=0,
                    max_attempts=3,
                    next_attempt_at=None,
                    completed_at=None,
                    source=text.extractor,
                    error=text.error,
                    raw={
                        "text_status": text.status,
                        "file_url": downloaded_file.file_url,
                        "file_sha256": downloaded_file.sha256,
                    },
                )
            )
        self.store.upsert_ocr_queue_items(items)
        return items

    def process_ocr_queue(
        self,
        engine: str = "paddleocr",
        limit: int = 10,
        retry_delay_minutes: int = 60,
        now: datetime | None = None,
    ) -> list[DocumentText]:
        """Process queued OCR items with the selected engine."""

        self.initialize()
        current_time = now or datetime.now()
        extractor = self.ocr_extractors.get(engine)
        if extractor is None:
            raise ValueError(f"Unsupported OCR engine: {engine}")

        queue_rows = self.store.get_ocr_queue_items(
            status="queued",
            due_at=_format_time(current_time),
        )[:limit]
        results: list[DocumentText] = []
        for row in queue_rows:
            item = OCRQueueItem(
                document_id=row["document_id"],
                file_path=row["file_path"],
                reason=row["reason"],
                status=row["status"],
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                next_attempt_at=row["next_attempt_at"],
                completed_at=row["completed_at"],
                source=row["source"],
                error=row["error"],
                raw={"raw_json": row.get("raw_json")},
            )
            result = extractor.extract_text(item)  # type: ignore[attr-defined]
            results.append(result)
            self.store.upsert_document_texts([result])
            next_attempts = item.attempts + 1
            if result.status == "extracted":
                next_status = "done"
                next_attempt_at = None
                completed_at = _format_time(current_time)
            elif next_attempts >= item.max_attempts:
                next_status = "review_required"
                next_attempt_at = None
                completed_at = None
            else:
                next_status = "queued"
                next_attempt_at = _format_time(current_time + timedelta(minutes=retry_delay_minutes))
                completed_at = None
            self.store.update_ocr_queue_item(
                document_id=item.document_id,
                status=next_status,
                attempts=next_attempts,
                error=result.error,
                next_attempt_at=next_attempt_at,
                completed_at=completed_at,
            )
        return results

    def extract_and_store_document_tables(self, files: list[DownloadedFile]) -> list[DocumentTable]:
        """Extract tables from downloaded disclosure files and persist results."""

        self.initialize()
        tables: list[DocumentTable] = []
        for file in files:
            tables.extend(self.pdf_table_extractor.extract_tables(file))
        self.store.upsert_document_tables(tables)
        return tables

    def extract_and_store_financial_fields(self, tables: list[DocumentTable]) -> list[FinancialField]:
        """Extract standard financial fields from parsed tables and persist them."""

        self.initialize()
        fields = self.financial_field_extractor.extract_from_tables(tables)
        self.store.upsert_financial_fields(fields)
        return fields

    def extract_and_store_financial_fields_from_texts(self, texts: list[DocumentText]) -> list[FinancialField]:
        """Extract standard financial fields from parsed text and persist them."""

        self.initialize()
        fields = self.financial_field_extractor.extract_from_texts(texts)
        self.store.upsert_financial_fields(fields)
        return fields

    def fetch_stock_research_reports(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "eastmoney",
        timeout_seconds: float | None = None,
    ) -> list[ResearchReport]:
        """Fetch third-party stock research report metadata."""

        if timeout_seconds is not None:
            return run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_stock_research_reports",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout_seconds=timeout_seconds,
            )
        if source == "eastmoney":
            return self.eastmoney_research.search_stock_reports(symbol, start_date, end_date)
        raise ValueError(f"Unsupported research report source: {source}")

    def fetch_and_store_stock_research_reports(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "eastmoney",
    ) -> FetchResult:
        """Fetch third-party stock research report metadata and persist it."""

        self.initialize()
        reports = self.fetch_stock_research_reports(symbol, start_date, end_date, source)
        stored_count = self.store.upsert_research_reports(reports)
        return FetchResult(
            symbol=normalize_a_share_code(symbol),
            source=source,
            fetched_count=len(reports),
            stored_count=stored_count,
        )

    def download_and_store_research_report_files(
        self,
        reports: list[ResearchReport],
        root_dir: str = "data/research_reports",
        allow_download: bool = False,
    ) -> list[DownloadedFile]:
        """Download third-party research report PDFs only when explicitly allowed."""

        if not allow_download:
            raise ValueError("Research report PDF download requires allow_download=True")
        self.initialize()
        files = [self.eastmoney_research.download_pdf(report, root_dir=root_dir) for report in reports]
        self.store.upsert_document_files(files)
        return files

    def parse_and_store_research_report_files(
        self,
        files: list[DownloadedFile],
    ) -> dict[str, list[object]]:
        """Parse downloaded research report PDFs with the existing document pipeline."""

        texts = self.extract_and_store_document_texts(files)
        tables = self.extract_and_store_document_tables(files)
        fields = self.extract_and_store_financial_fields(tables)
        text_fields = self.extract_and_store_financial_fields_from_texts(texts)
        return {
            "texts": texts,
            "tables": tables,
            "financial_fields": fields + text_fields,
        }


def compare_daily_bars(
    symbol: str,
    left_source: str,
    right_source: str,
    left_bars: list[DailyBar],
    right_bars: list[DailyBar],
    tolerance_pct: float = 1.0,
) -> list[dict[str, object]]:
    """Compare two lists of daily bars and return source check records."""

    left_by_date = {bar.trade_date: bar for bar in left_bars}
    right_by_date = {bar.trade_date: bar for bar in right_bars}
    fields = ("open", "high", "low", "close", "volume", "amount")
    checks: list[dict[str, object]] = []

    for trade_date in sorted(set(left_by_date) & set(right_by_date)):
        left_bar = left_by_date[trade_date]
        right_bar = right_by_date[trade_date]
        for field_name in fields:
            left_value = getattr(left_bar, field_name)
            right_value = getattr(right_bar, field_name)
            if left_value is None or right_value is None:
                continue
            diff_value = float(left_value) - float(right_value)
            reference = abs(float(right_value))
            diff_pct = abs(diff_value) / reference * 100 if reference else 0.0
            checks.append(
                {
                    "check_type": "a_share_daily_bar",
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "field_name": field_name,
                    "left_source": left_source,
                    "right_source": right_source,
                    "left_value": float(left_value),
                    "right_value": float(right_value),
                    "diff_value": diff_value,
                    "diff_pct": diff_pct,
                    "passed": 1 if diff_pct <= tolerance_pct else 0,
                }
            )
    return checks


def compare_exchange_fund_spot_records(
    records_by_source: dict[str, list[dict[str, object]]],
    symbols: list[str],
    tolerance_pct: float = 1.0,
) -> list[dict[str, object]]:
    """Compare normalized exchange-fund spot records by source."""

    fields = ("latest", "open", "pre_close", "volume", "amount")
    checks: list[dict[str, object]] = []
    normalized_symbols = [to_akshare_symbol(symbol) for symbol in symbols]
    source_maps = {
        source: {
            str(record.get("symbol")): record
            for record in records
            if record.get("symbol") is not None
        }
        for source, records in records_by_source.items()
    }
    sources = list(source_maps)
    if len(sources) < 2:
        return checks

    baseline_source = sources[0]
    baseline_records = source_maps[baseline_source]
    for right_source in sources[1:]:
        right_records = source_maps[right_source]
        for symbol in normalized_symbols:
            left_record = baseline_records.get(symbol)
            right_record = right_records.get(symbol)
            if not left_record or not right_record:
                continue
            for field_name in fields:
                left_value = _to_float(left_record.get(field_name))
                right_value = _to_float(right_record.get(field_name))
                if left_value is None or right_value is None:
                    continue
                diff_value = left_value - right_value
                reference = abs(right_value)
                diff_pct = abs(diff_value) / reference * 100 if reference else 0.0
                checks.append(
                    {
                        "check_type": "exchange_fund_spot",
                        "symbol": symbol,
                        "trade_date": None,
                        "field_name": field_name,
                        "left_source": baseline_source,
                        "right_source": right_source,
                        "left_value": left_value,
                        "right_value": right_value,
                        "diff_value": diff_value,
                        "diff_pct": diff_pct,
                        "passed": 1 if diff_pct <= tolerance_pct else 0,
                    }
                )
    return checks


def compare_disclosure_documents(
    left_documents: list[DisclosureDocument],
    right_documents: list[DisclosureDocument],
    left_files: list[DownloadedFile] | None = None,
    right_files: list[DownloadedFile] | None = None,
) -> list[DisclosureSourceCheck]:
    """Compare disclosure documents from two sources."""

    left_file_map = {file.document_id: file for file in left_files or []}
    right_file_map = {file.document_id: file for file in right_files or []}
    checks: list[DisclosureSourceCheck] = []
    for left in left_documents:
        right = _best_disclosure_match(left, right_documents)
        left_file = left_file_map.get(left.document_id)
        right_file = right_file_map.get(right.document_id) if right else None
        left_period = _infer_report_period(left.title)
        right_period = _infer_report_period(right.title) if right else None
        checks.append(
            DisclosureSourceCheck(
                symbol=left.symbol,
                left_source=left.source,
                right_source=right.source if right else "",
                left_document_id=left.document_id,
                right_document_id=right.document_id if right else None,
                title_match=_normalize_title(left.title) == _normalize_title(right.title) if right else False,
                date_match=left.announcement_date == right.announcement_date if right else False,
                report_period_match=None if not left_period or not right_period else left_period == right_period,
                hash_match=None
                if not left_file or not right_file
                else left_file.sha256 == right_file.sha256,
                left_title=left.title,
                right_title=right.title if right else None,
                left_date=left.announcement_date,
                right_date=right.announcement_date if right else None,
                left_sha256=left_file.sha256 if left_file else None,
                right_sha256=right_file.sha256 if right_file else None,
                raw={"left_file_url": left.file_url, "right_file_url": right.file_url if right else None},
            )
        )
    return checks


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field_completeness(rows: list[dict[str, object]], fields: tuple[str, ...]) -> float:
    if not rows:
        return 0.0
    total = len(rows) * len(fields)
    present = 0
    for row in rows:
        present += sum(1 for field in fields if row.get(field) not in (None, ""))
    return present / total if total else 0.0


def _best_disclosure_match(left: DisclosureDocument, candidates: list[DisclosureDocument]) -> DisclosureDocument | None:
    same_symbol = [candidate for candidate in candidates if candidate.symbol == left.symbol]
    if not same_symbol:
        return None
    normalized_left = _normalize_title(left.title)
    for candidate in same_symbol:
        if _normalize_title(candidate.title) == normalized_left:
            return candidate
    left_period = _infer_report_period(left.title)
    if left_period:
        for candidate in same_symbol:
            if _infer_report_period(candidate.title) == left_period and candidate.announcement_date == left.announcement_date:
                return candidate
    for candidate in same_symbol:
        if candidate.announcement_date == left.announcement_date:
            return candidate
    return same_symbol[0]


def _normalize_title(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())


def _infer_report_period(title: str) -> str | None:
    normalized = title.replace(" ", "")
    year_match = re.search(r"(20\d{2})", normalized)
    if not year_match:
        return None
    year = year_match.group(1)
    if "年度报告" in normalized or "年报" in normalized:
        return f"{year}FY"
    if "半年度报告" in normalized or "半年报" in normalized:
        return f"{year}H1"
    if "一季度" in normalized or "第一季度" in normalized:
        return f"{year}Q1"
    if "二季度" in normalized or "第二季度" in normalized:
        return f"{year}Q2"
    if "三季度" in normalized or "第三季度" in normalized:
        return f"{year}Q3"
    if "四季度" in normalized or "第四季度" in normalized:
        return f"{year}Q4"
    return None


def _cninfo_report_category(report_type: str) -> str:
    normalized = report_type.strip().lower().replace("-", "_")
    mapping = {
        "annual": CninfoDataSource.CATEGORY_ANNUAL_REPORT,
        "annual_report": CninfoDataSource.CATEGORY_ANNUAL_REPORT,
        "semi_annual": CninfoDataSource.CATEGORY_SEMI_ANNUAL_REPORT,
        "semi_annual_report": CninfoDataSource.CATEGORY_SEMI_ANNUAL_REPORT,
        "half_year": CninfoDataSource.CATEGORY_SEMI_ANNUAL_REPORT,
        "quarterly": CninfoDataSource.CATEGORY_QUARTERLY_REPORT,
        "quarterly_report": CninfoDataSource.CATEGORY_QUARTERLY_REPORT,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported financial report type: {report_type}")
    return mapping[normalized]


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")
