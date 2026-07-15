"""Project-facing data fetch service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.data_store import SQLiteDataStore
from src.data_src.akshare import AkShareDataSource
from src.data_src.baostock import BaoStockDataSource
from src.data_src.bse import BseDisclosureDataSource
from src.data_src.cninfo import CninfoDataSource
from src.data_src.common import normalize_a_share_code, to_akshare_symbol
from src.data_src.csrc import CsrcPublicInfoDataSource
from src.data_src.cs_fund import CsFundDisclosureDataSource
from src.data_src.eastmoney import EastmoneyExchangeFundDataSource, EastmoneyResearchReportDataSource
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
from src.data_src.sina import SinaExchangeFundDataSource
from src.data_src.sse import SseDisclosureDataSource
from src.data_src.szse import SzseDisclosureDataSource
from src.data_src.tencent import TencentExchangeFundDataSource
from src.document_processing import (
    FinancialFieldExtractor,
    PaddleOCRTextExtractor,
    PDFTableExtractor,
    PDFTextExtractor,
    TesseractOCRTextExtractor,
)


DEFAULT_EXCHANGE_FUND_SPOT_SOURCES = ["eastmoney", "tencent", "sina", "akshare"]


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
        cs_fund_source: CsFundDisclosureDataSource | None = None,
        eastmoney_source: EastmoneyExchangeFundDataSource | None = None,
        eastmoney_research_source: EastmoneyResearchReportDataSource | None = None,
        fund_company_source: FundCompanyDisclosureDataSource | None = None,
        tencent_source: TencentExchangeFundDataSource | None = None,
        sina_source: SinaExchangeFundDataSource | None = None,
        sse_source: SseDisclosureDataSource | None = None,
        szse_source: SzseDisclosureDataSource | None = None,
        pdf_text_extractor: PDFTextExtractor | None = None,
        pdf_table_extractor: PDFTableExtractor | None = None,
        financial_field_extractor: FinancialFieldExtractor | None = None,
        ocr_extractors: dict[str, object] | None = None,
    ) -> None:
        self.store = store
        self.akshare = akshare_source or AkShareDataSource()
        self.baostock = baostock_source or BaoStockDataSource()
        self.bse = bse_source or BseDisclosureDataSource()
        self.cninfo = cninfo_source or CninfoDataSource()
        self.csrc = csrc_source or CsrcPublicInfoDataSource()
        self.cs_fund = cs_fund_source or CsFundDisclosureDataSource()
        self.eastmoney = eastmoney_source or EastmoneyExchangeFundDataSource()
        self.eastmoney_research = eastmoney_research_source or EastmoneyResearchReportDataSource()
        self.fund_company = fund_company_source or FundCompanyDisclosureDataSource()
        self.tencent = tencent_source or TencentExchangeFundDataSource()
        self.sina = sina_source or SinaExchangeFundDataSource()
        self.sse = sse_source or SseDisclosureDataSource()
        self.szse = szse_source or SzseDisclosureDataSource()
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
        raise ValueError(f"Unsupported A-share daily bar source: {source}")

    def fetch_and_store_a_share_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = "baostock",
    ) -> FetchResult:
        """Fetch A-share daily bars and persist them to SQLite."""

        self.initialize()
        bars = self.fetch_a_share_daily_bars(symbol, start_date, end_date, source)
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
    ) -> FetchResult:
        """Fetch exchange-traded fund daily bars and persist them to SQLite."""

        self.initialize()
        bars = self.fetch_etf_daily_bars(symbol, start_date, end_date, source)
        stored_count = self.store.upsert_fund_exchange_daily_bars(bars)
        return FetchResult(
            symbol=to_akshare_symbol(symbol),
            source=source,
            fetched_count=len(bars),
            stored_count=stored_count,
        )

    def fetch_open_fund_nav_history(
        self,
        symbol: str,
        source: str = "akshare",
    ) -> list[FundNav]:
        """Fetch normalized open-end fund NAV history."""

        if source == "akshare":
            return self.akshare.get_open_fund_nav_history_records(symbol)
        raise ValueError(f"Unsupported open fund NAV source: {source}")

    def fetch_and_store_open_fund_nav_history(
        self,
        symbol: str,
        source: str = "akshare",
    ) -> FetchResult:
        """Fetch open-end fund NAV history and persist it to SQLite."""

        self.initialize()
        navs = self.fetch_open_fund_nav_history(symbol, source)
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
        stored_count = self.store.upsert_securities(securities)
        return FetchResult(
            symbol=normalize_a_share_code(symbol) if symbol else "ALL",
            source=source,
            fetched_count=len(securities),
            stored_count=stored_count,
        )

    def fetch_fund_basic_info(self, source: str = "akshare") -> list[SecurityInfo]:
        """Fetch normalized public fund basic information."""

        if source == "akshare":
            return self.akshare.get_fund_names()
        raise ValueError(f"Unsupported fund basic info source: {source}")

    def fetch_and_store_fund_basic_info(self, source: str = "akshare") -> FetchResult:
        """Fetch public fund basic information and persist it to SQLite."""

        self.initialize()
        securities = self.fetch_fund_basic_info(source)
        stored_count = self.store.upsert_securities(securities)
        return FetchResult(
            symbol="ALL",
            source=source,
            fetched_count=len(securities),
            stored_count=stored_count,
        )

    def fetch_fund_value_estimates(self, source: str = "akshare") -> list[FundEstimate]:
        """Fetch platform-estimated fund values."""

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

    def fetch_fund_purchase_status(self, source: str = "akshare") -> list[FundPurchaseStatus]:
        """Fetch fund purchase and redemption statuses."""

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

    def fetch_fund_scale_records(self, fund_type: str = "股票型基金", source: str = "akshare") -> list[FundReportRecord]:
        """Fetch open-end fund scale records."""

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
            return self.akshare.get_fund_stock_holdings(symbol, year)
        if normalized_type in {"bond", "bond_holding", "bond_holdings"}:
            return self.akshare.get_fund_bond_holdings(symbol, year)
        if normalized_type in {"industry", "industry_allocation"}:
            return self.akshare.get_fund_industry_allocation(symbol, year)
        raise ValueError(f"Unsupported fund portfolio report type: {report_type}")

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
        files = [self.cninfo.download_pdf(document, root_dir=root_dir) for document in documents]
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
    ) -> list[ResearchReport]:
        """Fetch third-party stock research report metadata."""

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
