import hashlib
import tempfile
import unittest
from pathlib import Path

from src.data_src.models import DownloadedFile
from src.state_capital_flow import (
    CninfoFundReportDiscovery,
    CompositeFundReportDiscovery,
    OfficialFundReport,
    SseFundReportDiscovery,
    StateHolderBackfillService,
)


class FakeCninfoHttpClient:
    def __init__(self):
        self.payloads = []
        self.download_content = b"%PDF official fund report"

    def post_json(self, url, payload):
        self.payloads.append((url, dict(payload)))
        pages = {
            1: [
                _announcement(
                    "annual-1",
                    "510300",
                    "沪深300ETF 2025年年度报告",
                    "2026-03-30",
                    "finalpage/2026-03-30/annual-1.PDF",
                ),
                _announcement(
                    "other-1",
                    "159919",
                    "沪深300ETF 2025年年度报告",
                    "2026-03-29",
                    "finalpage/2026-03-29/other-1.PDF",
                ),
            ],
            2: [
                _announcement(
                    "summary-1",
                    "510300",
                    "沪深300ETF 2025年年度报告摘要",
                    "2026-03-30",
                    "finalpage/2026-03-30/summary-1.PDF",
                ),
                _announcement(
                    "semi-1",
                    "510300",
                    "<em>沪深300ETF</em> 2026年半年度报告",
                    "2026-08-30",
                    "/finalpage/2026-08-30/semi-1.PDF",
                ),
            ],
        }
        return {
            "announcements": pages.get(payload["pageNum"], []),
            "totalAnnouncement": 4,
        }

    def download_bytes(self, url):
        return self.download_content


class CninfoFundReportDiscoveryTest(unittest.TestCase):
    def test_paginates_filters_and_identifies_periods(self):
        http = FakeCninfoHttpClient()
        discovery = CninfoFundReportDiscovery(http)

        reports = discovery.discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            page_size=2,
            max_pages=10,
            max_reports=10,
        )

        self.assertEqual([item.document_id for item in reports], [
            "annual-1",
            "semi-1",
        ])
        self.assertEqual(
            [(item.report_type, item.report_period) for item in reports],
            [
                ("annual", "2025-12-31"),
                ("semi_annual", "2026-06-30"),
            ],
        )
        self.assertEqual(
            reports[1].pdf_url,
            "https://static.cninfo.com.cn/finalpage/2026-08-30/semi-1.PDF",
        )
        self.assertEqual(len(http.payloads), 2)
        first_payload = http.payloads[0][1]
        self.assertEqual(first_payload["column"], "fund")
        self.assertEqual(first_payload["tabName"], "fulltext")
        self.assertEqual(
            first_payload["category"],
            "category_ndbg_jjgg;category_bndbg_jjgg;",
        )
        self.assertLessEqual(first_payload["pageSize"], 30)

    def test_page_and_report_limits_stop_fetching(self):
        page_limited_http = FakeCninfoHttpClient()
        page_limited = CninfoFundReportDiscovery(
            page_limited_http
        ).discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            page_size=2,
            max_pages=1,
            max_reports=10,
        )
        report_limited_http = FakeCninfoHttpClient()
        report_limited = CninfoFundReportDiscovery(
            report_limited_http
        ).discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            page_size=2,
            max_pages=10,
            max_reports=1,
        )

        self.assertEqual([item.document_id for item in page_limited], [
            "annual-1"
        ])
        self.assertEqual(len(page_limited_http.payloads), 1)
        self.assertEqual([item.document_id for item in report_limited], [
            "annual-1"
        ])
        self.assertEqual(len(report_limited_http.payloads), 1)

    def test_download_path_and_hash_are_idempotent(self):
        http = FakeCninfoHttpClient()
        discovery = CninfoFundReportDiscovery(http)
        report = OfficialFundReport(
            document_id="annual-1",
            symbol="510300",
            title="沪深300ETF 2025年年度报告",
            report_type="annual",
            report_period="2025-12-31",
            published_at="2026-03-30",
            pdf_url=(
                "https://static.cninfo.com.cn/"
                "finalpage/2026-03-30/annual-1.PDF"
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            first = discovery.download(report, temp_dir)
            second = discovery.download(report, temp_dir)
            expected_path = (
                Path(temp_dir)
                / "510300"
                / "2026-03-30__annual-1.pdf"
            )

            self.assertEqual(Path(first.file_path), expected_path)
            self.assertEqual(second.file_path, first.file_path)
            self.assertEqual(
                first.sha256,
                hashlib.sha256(http.download_content).hexdigest(),
            )
            self.assertEqual(second.sha256, first.sha256)
            self.assertEqual(expected_path.read_bytes(), http.download_content)


class FakeSseFundHttpClient:
    def __init__(self):
        self.requests = []
        self.download_content = b"%PDF official SSE fund report"
        self.download_urls = []

    def get_json(self, url, params):
        self.requests.append((url, dict(params)))
        page_no = params["pageHelp.pageNo"]
        pages = {
            1: [
                _sse_row(
                    "510300",
                    "华泰柏瑞沪深300ETF 2025年年度报告",
                    "2026-03-31",
                    "/disclosure/fund/announcement/c/new/"
                    "2026-03-31/510300_20260331_ANNUAL.pdf",
                ),
                _sse_row(
                    "510300",
                    "华泰柏瑞沪深300ETF 2026年第一季度报告",
                    "2026-04-22",
                    "/disclosure/fund/announcement/c/new/"
                    "2026-04-22/510300_20260422_Q1.pdf",
                ),
                _sse_row(
                    "510300",
                    "华泰柏瑞沪深300ETF 2025年年度报告摘要",
                    "2026-03-31",
                    "/disclosure/fund/announcement/c/new/"
                    "2026-03-31/510300_20260331_SUMMARY.pdf",
                ),
            ],
            2: [
                _sse_row(
                    "510300",
                    "华泰柏瑞沪深300ETF 2026年半年度报告",
                    "2026-08-30",
                    "/disclosure/fund/announcement/c/new/"
                    "2026-08-30/510300_20260830_SEMI.pdf",
                )
            ],
        }
        return {
            "pageHelp": {
                "data": pages.get(page_no, []),
                "pageCount": 2,
            }
        }

    def download_bytes(self, url):
        self.download_urls.append(url)
        return self.download_content


class SseFundReportDiscoveryTest(unittest.TestCase):
    def test_discovers_510300_periodic_reports_and_filters_other_titles(self):
        http = FakeSseFundHttpClient()
        discovery = SseFundReportDiscovery(http)

        reports = discovery.discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            page_size=99,
            max_pages=10,
            max_reports=10,
        )

        self.assertEqual(
            [report.document_id for report in reports],
            [
                "510300_20260331_ANNUAL",
                "510300_20260830_SEMI",
            ],
        )
        self.assertEqual(
            [report.report_period for report in reports],
            ["2025-12-31", "2026-06-30"],
        )
        self.assertTrue(all(report.source == "sse:fund_report" for report in reports))
        self.assertEqual(
            reports[0].pdf_url,
            "https://www.sse.com.cn/disclosure/fund/announcement/c/new/"
            "2026-03-31/510300_20260331_ANNUAL.pdf",
        )
        self.assertEqual(len(http.requests), 2)
        first_url, first_params = http.requests[0]
        self.assertEqual(
            first_url,
            "https://query.sse.com.cn/commonQuery.do",
        )
        self.assertEqual(
            first_params["sqlId"],
            "COMMON_PL_JJXX_JJGG_NEW_L",
        )
        self.assertEqual(first_params["SECURITY_CODE"], "510300")
        self.assertEqual(first_params["START_DATE"], "2026-01-01")
        self.assertEqual(first_params["END_DATE"], "2026-12-31")
        self.assertLessEqual(first_params["pageHelp.pageSize"], 30)
        self.assertEqual(
            [request[1]["pageHelp.pageNo"] for request in http.requests],
            [1, 2],
        )

    def test_global_page_and_report_limits_bound_sse_requests(self):
        page_http = FakeSseFundHttpClient()
        page_limited = SseFundReportDiscovery(page_http).discover(
            ["510300", "510050"],
            "2026-01-01",
            "2026-12-31",
            max_pages=1,
            max_reports=10,
        )
        report_http = FakeSseFundHttpClient()
        report_limited = SseFundReportDiscovery(report_http).discover(
            ["510300", "510050"],
            "2026-01-01",
            "2026-12-31",
            max_pages=10,
            max_reports=1,
        )

        self.assertEqual(len(page_http.requests), 1)
        self.assertEqual(len(page_limited), 1)
        self.assertEqual(len(report_http.requests), 1)
        self.assertEqual(len(report_limited), 1)

    def test_sse_download_uses_shared_idempotent_path_and_hash_rule(self):
        http = FakeSseFundHttpClient()
        discovery = SseFundReportDiscovery(http)
        report = discovery.discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            max_pages=1,
            max_reports=1,
        )[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            first = discovery.download(report, temp_dir)
            second = discovery.download(report, temp_dir)
            expected = (
                Path(temp_dir)
                / "510300"
                / "2026-03-31__510300_20260331_ANNUAL.pdf"
            )

            self.assertEqual(Path(first.file_path), expected)
            self.assertEqual(second.file_path, first.file_path)
            self.assertEqual(
                first.sha256,
                hashlib.sha256(http.download_content).hexdigest(),
            )
            self.assertEqual(first.source, "sse:fund_report")


class FakeFundReportDiscovery:
    def __init__(self, source, reports):
        self.source = source
        self.reports = reports
        self.discover_calls = []
        self.downloaded = []

    def discover(self, symbols, start_date, end_date, **limits):
        self.discover_calls.append(dict(limits))
        return self.reports[: limits["max_reports"]]

    def download(self, report, root_dir):
        self.downloaded.append(report.document_id)
        return DownloadedFile(
            document_id=report.document_id,
            file_path=str(Path(root_dir) / f"{report.document_id}.pdf"),
            file_url=report.pdf_url,
            sha256="1" * 64,
            size_bytes=1,
            source=report.source,
        )


class CompositeFundReportDiscoveryTest(unittest.TestCase):
    def test_deduplicates_periods_uses_remaining_limit_and_routes_download(self):
        cninfo_report = _source_report(
            "cninfo-annual",
            "annual",
            "2025-12-31",
            "2026-03-30",
            "cninfo:fund_report",
        )
        duplicate_sse_report = _source_report(
            "sse-annual",
            "annual",
            "2025-12-31",
            "2026-03-31",
            "sse:fund_report",
        )
        semi_sse_report = _source_report(
            "sse-semi",
            "semi_annual",
            "2026-06-30",
            "2026-08-30",
            "sse:fund_report",
        )
        cninfo = FakeFundReportDiscovery(
            "cninfo:fund_report",
            [cninfo_report],
        )
        sse = FakeFundReportDiscovery(
            "sse:fund_report",
            [duplicate_sse_report, semi_sse_report],
        )
        composite = CompositeFundReportDiscovery([cninfo, sse])

        reports = composite.discover(
            ["510300"],
            "2026-01-01",
            "2026-12-31",
            max_pages=3,
            max_reports=3,
        )

        self.assertEqual(
            [report.document_id for report in reports],
            ["cninfo-annual", "sse-semi"],
        )
        self.assertEqual(cninfo.discover_calls[0]["max_reports"], 3)
        self.assertEqual(sse.discover_calls[0]["max_reports"], 2)
        self.assertEqual(cninfo.discover_calls[0]["max_pages"], 3)
        self.assertEqual(sse.discover_calls[0]["max_pages"], 3)

        with tempfile.TemporaryDirectory() as temp_dir:
            composite.download(semi_sse_report, temp_dir)
        self.assertEqual(cninfo.downloaded, [])
        self.assertEqual(sse.downloaded, ["sse-semi"])

    def test_report_source_rejects_non_official_discovery_source(self):
        with self.assertRaisesRegex(ValueError, "source must be"):
            _source_report(
                "bad",
                "annual",
                "2025-12-31",
                "2026-03-30",
                "aggregator:fund_report",
            )


class FakeBackfillDiscovery:
    def __init__(self):
        self.downloaded_ids = []
        self.reports = [
            _report("doc-1", "2026-03-01"),
            _report("doc-fail", "2026-03-02"),
            _report("doc-empty", "2026-03-03"),
        ]

    def discover(
        self,
        symbols,
        start_date,
        end_date,
        *,
        max_pages,
        max_reports,
    ):
        return self.reports[:max_reports]

    def download(self, report, root_dir):
        self.downloaded_ids.append(report.document_id)
        return DownloadedFile(
            document_id=report.document_id,
            file_path=str(Path(root_dir) / f"{report.document_id}.pdf"),
            file_url=report.pdf_url,
            sha256="0" * 64,
            size_bytes=1,
            source=report.source,
        )


class FakeTableExtractor:
    def extract_tables(self, downloaded):
        if downloaded.document_id == "doc-fail":
            raise RuntimeError("broken PDF")
        return [downloaded.document_id]


class FakeHolderParser:
    def parse_document_tables(self, tables, **context):
        if tables == ["doc-empty"]:
            return []
        return [{"document_id": tables[0], "context": context}]


class FakeHolderRepository:
    def __init__(self):
        self.evidence = []

    def upsert_holder_evidence(self, evidence):
        self.evidence.extend(evidence)
        return len(evidence)


class StateHolderBackfillServiceTest(unittest.TestCase):
    def test_backfill_isolates_failures_and_resumes_after_document(self):
        discovery = FakeBackfillDiscovery()
        repository = FakeHolderRepository()
        service = StateHolderBackfillService(
            discovery,
            FakeHolderParser(),
            FakeTableExtractor(),
            repository,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = service.run(
                {"510300": "asset:etf:510300"},
                "2026-01-01",
                "2026-12-31",
                temp_dir,
            )

        self.assertEqual(result.discovered_count, 3)
        self.assertEqual(result.processed_count, 2)
        self.assertEqual(result.evidence_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.last_document_id, "doc-empty")
        self.assertIn("doc-fail: RuntimeError: broken PDF", result.errors)
        self.assertEqual(
            discovery.downloaded_ids,
            ["doc-1", "doc-fail", "doc-empty"],
        )
        self.assertEqual(
            repository.evidence[0]["context"]["instrument_asset_id"],
            "asset:etf:510300",
        )

        resumed_discovery = FakeBackfillDiscovery()
        resumed_service = StateHolderBackfillService(
            resumed_discovery,
            FakeHolderParser(),
            FakeTableExtractor(),
            FakeHolderRepository(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            resumed = resumed_service.run(
                {"510300": "asset:etf:510300"},
                "2026-01-01",
                "2026-12-31",
                temp_dir,
                resume_after_document_id="doc-1",
            )

        self.assertEqual(resumed.processed_count, 1)
        self.assertEqual(resumed.failed_count, 1)
        self.assertEqual(
            resumed_discovery.downloaded_ids,
            ["doc-fail", "doc-empty"],
        )


def _sse_row(symbol, title, published_at, url):
    return {
        "SECURITY_CODE": symbol,
        "TITLE": title,
        "SSEDATE": published_at,
        "URL": url,
    }


def _source_report(
    document_id,
    report_type,
    report_period,
    published_at,
    source,
):
    period_title = (
        f"{report_period[:4]}年年度报告"
        if report_type == "annual"
        else f"{report_period[:4]}年半年度报告"
    )
    return OfficialFundReport(
        document_id=document_id,
        symbol="510300",
        title=f"沪深300ETF {period_title}",
        report_type=report_type,
        report_period=report_period,
        published_at=published_at,
        pdf_url=f"https://example.test/{document_id}.pdf",
        source=source,
    )


def _announcement(
    document_id,
    symbol,
    title,
    published_at,
    adjunct_url,
):
    return {
        "announcementId": document_id,
        "secCode": symbol,
        "announcementTitle": title,
        "announcementDate": published_at,
        "adjunctUrl": adjunct_url,
    }


def _report(document_id, published_at):
    return OfficialFundReport(
        document_id=document_id,
        symbol="510300",
        title="沪深300ETF 2025年年度报告",
        report_type="annual",
        report_period="2025-12-31",
        published_at=published_at,
        pdf_url=f"https://static.cninfo.com.cn/{document_id}.pdf",
    )


if __name__ == "__main__":
    unittest.main()
