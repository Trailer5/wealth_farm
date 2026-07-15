import tempfile
import unittest
from pathlib import Path

from src.data_src.eastmoney import EastmoneyResearchReportDataSource
from src.data_src.models import DocumentTable, DocumentText, ResearchReport
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeEastmoneyResearchHttp:
    def __init__(self):
        self.last_params = None
        self.last_download_url = None

    def get_json(self, url, params=None):
        self.last_params = params
        return {
            "data": [
                {
                    "infoCode": "AP202607150001",
                    "stockCode": "600519",
                    "stockName": "贵州茅台",
                    "title": "贵州茅台：业绩稳健增长",
                    "orgSName": "测试证券",
                    "publishDate": "2026-07-15 00:00:00.000",
                    "emRatingName": "买入",
                    "indvInduName": "白酒",
                    "predictThisYearEps": "50.0",
                }
            ]
        }

    def download_bytes(self, url):
        self.last_download_url = url
        return b"%PDF fake research"


class FakeResearchSource:
    def search_stock_reports(self, symbol, start_date, end_date):
        return [
            ResearchReport(
                report_id="AP202607150001",
                symbol="600519",
                title="贵州茅台：业绩稳健增长",
                publish_date="2026-07-15",
                institution="测试证券",
                rating="买入",
                industry="白酒",
                pdf_url="https://pdf.dfcfw.com/pdf/H3_AP202607150001_1.pdf",
                source="eastmoney_research",
                raw={"symbol": symbol},
            )
        ]

    def download_pdf(self, report, root_dir):
        return EastmoneyResearchReportDataSource(FakeEastmoneyResearchHttp()).download_pdf(report, root_dir)


class FakePDFTextExtractor:
    def extract_text(self, downloaded_file):
        return DocumentText(
            document_id=downloaded_file.document_id,
            text_content="研报正文\n营业收入 100万元",
            extractor="fake",
            page_count=1,
            status="extracted",
            raw={"file_path": downloaded_file.file_path},
        )


class FakePDFTableExtractor:
    def extract_tables(self, downloaded_file):
        return [
            DocumentTable(
                document_id=downloaded_file.document_id,
                table_index=0,
                page_number=2,
                rows=[["项目", "本期金额"], ["净利润", "20万元"]],
                extractor="fake",
                status="extracted",
                raw={"file_path": downloaded_file.file_path},
            )
        ]


class EastmoneyResearchReportDataSourceTest(unittest.TestCase):
    def test_search_stock_reports_normalizes_records(self):
        fake = FakeEastmoneyResearchHttp()
        source = EastmoneyResearchReportDataSource(fake)

        reports = source.search_stock_reports("600519", "20260701", "20260715")

        self.assertEqual(fake.last_params["code"], "600519")
        self.assertEqual(fake.last_params["qType"], "0")
        self.assertEqual(reports[0].report_id, "AP202607150001")
        self.assertEqual(reports[0].title, "贵州茅台：业绩稳健增长")
        self.assertEqual(reports[0].pdf_url, "https://pdf.dfcfw.com/pdf/H3_AP202607150001_1.pdf")

    def test_download_pdf_requires_report_url_and_writes_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeEastmoneyResearchHttp()
            source = EastmoneyResearchReportDataSource(fake)
            report = source.search_stock_reports("600519", "20260701", "20260715")[0]

            downloaded = source.download_pdf(report, root_dir=temp_dir)

            self.assertTrue(Path(downloaded.file_path).exists())
            self.assertEqual(downloaded.size_bytes, len(b"%PDF fake research"))
            self.assertEqual(fake.last_download_url, report.pdf_url)


class ResearchReportStoreServiceTest(unittest.TestCase):
    def test_research_reports_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                eastmoney_research_source=FakeResearchSource(),
            )

            result = service.fetch_and_store_stock_research_reports("600519", "20260701", "20260715")
            rows = store.get_research_reports("600519")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["institution"], "测试证券")
            self.assertEqual(rows[0]["rating"], "买入")

    def test_research_report_pdf_download_is_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, eastmoney_research_source=FakeResearchSource())
            report = FakeResearchSource().search_stock_reports("600519", "20260701", "20260715")[0]

            with self.assertRaises(ValueError):
                service.download_and_store_research_report_files([report], root_dir=temp_dir)

            files = service.download_and_store_research_report_files([report], root_dir=temp_dir, allow_download=True)

            self.assertEqual(len(files), 1)
            self.assertTrue(Path(files[0].file_path).exists())

    def test_parse_downloaded_research_report_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                eastmoney_research_source=FakeResearchSource(),
                pdf_text_extractor=FakePDFTextExtractor(),
                pdf_table_extractor=FakePDFTableExtractor(),
            )
            report = FakeResearchSource().search_stock_reports("600519", "20260701", "20260715")[0]
            files = service.download_and_store_research_report_files([report], root_dir=temp_dir, allow_download=True)

            parsed = service.parse_and_store_research_report_files(files)
            stored_text = store.get_document_text(report.report_id)
            stored_fields = store.get_financial_fields(report.report_id)

            self.assertEqual(parsed["texts"][0].status, "extracted")
            self.assertIn("研报正文", stored_text["text_content"])
            self.assertTrue(any(field["field_name"] == "net_profit" for field in stored_fields))
            self.assertTrue(any(field["field_name"] == "revenue" for field in stored_fields))


if __name__ == "__main__":
    unittest.main()
