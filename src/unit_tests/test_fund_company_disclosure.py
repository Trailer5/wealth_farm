import tempfile
import unittest
from pathlib import Path

from src.data_src.fund_company import FundCompanyDisclosureDataSource
from src.data_src.models import DisclosureDocument, DownloadedFile
from src.data_src.service import DataFetchService, compare_disclosure_documents
from src.data_store import SQLiteDataStore


class FakeFundCompanyHttpClient:
    def __init__(self):
        self.last_url = None

    def get_text(self, url, params=None):
        self.last_url = url
        return """
        <div>
          <span>000001</span>
          <span>2026/03/30</span>
          <a href="/upload/report/000001-annual.pdf">测试基金2025年年度报告</a>
          <a href="/upload/report/000002-annual.pdf">其他基金2025年年度报告</a>
        </div>
        """


class FundCompanyDisclosureDataSourceTest(unittest.TestCase):
    def test_search_announcements_parses_official_website_links(self):
        fake = FakeFundCompanyHttpClient()
        source = FundCompanyDisclosureDataSource(fake)

        documents = source.search_announcements(
            ["https://fund.example.com/disclosure/index.html"],
            fund_code="000001",
            keyword="年度报告",
            category="annual",
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].symbol, "000001")
        self.assertEqual(documents[0].title, "测试基金2025年年度报告")
        self.assertEqual(documents[0].announcement_date, "2026-03-30")
        self.assertEqual(documents[0].file_url, "https://fund.example.com/upload/report/000001-annual.pdf")


class FundCompanyDisclosureServiceTest(unittest.TestCase):
    def test_fund_company_disclosure_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                fund_company_source=FundCompanyDisclosureDataSource(FakeFundCompanyHttpClient()),
            )

            result = service.fetch_and_store_fund_company_disclosure_documents(
                ["https://fund.example.com/disclosure/index.html"],
                fund_code="000001",
                keyword="年度报告",
                category="annual",
            )
            rows = store.get_disclosure_documents("000001", source="fund_company")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "测试基金2025年年度报告")

    def test_compare_fund_company_with_cninfo_disclosures(self):
        left = [
            DisclosureDocument(
                document_id="fund-company-1",
                symbol="000001",
                title="测试基金2025年年度报告",
                announcement_date="2026-03-30",
                category="annual",
                source="fund_company",
                file_url="https://fund.example.com/000001.pdf",
            )
        ]
        right = [
            DisclosureDocument(
                document_id="cninfo-1",
                symbol="000001",
                title="测试基金2025年年度报告",
                announcement_date="2026-03-30",
                category="annual",
                source="cninfo",
                file_url="https://static.cninfo.com.cn/000001.pdf",
            )
        ]
        left_files = [
            DownloadedFile(
                document_id="fund-company-1",
                file_path="left.pdf",
                file_url="https://fund.example.com/000001.pdf",
                sha256="abc",
                size_bytes=10,
                source="fund_company",
            )
        ]
        right_files = [
            DownloadedFile(
                document_id="cninfo-1",
                file_path="right.pdf",
                file_url="https://static.cninfo.com.cn/000001.pdf",
                sha256="abc",
                size_bytes=10,
                source="cninfo",
            )
        ]

        checks = compare_disclosure_documents(left, right, left_files, right_files)

        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0].title_match)
        self.assertTrue(checks[0].date_match)
        self.assertTrue(checks[0].report_period_match)
        self.assertTrue(checks[0].hash_match)

    def test_disclosure_source_checks_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store)
            left = [
                DisclosureDocument(
                    document_id="fund-company-1",
                    symbol="000001",
                    title="测试基金2025年年度报告",
                    announcement_date="2026-03-30",
                    category="annual",
                    source="fund_company",
                    file_url="https://fund.example.com/000001.pdf",
                )
            ]
            right = [
                DisclosureDocument(
                    document_id="cninfo-1",
                    symbol="000001",
                    title="测试基金2025年年度报告",
                    announcement_date="2026-03-30",
                    category="annual",
                    source="cninfo",
                    file_url="https://static.cninfo.com.cn/000001.pdf",
                )
            ]

            checks = service.compare_disclosure_sources(left, right)
            rows = store.get_disclosure_source_checks("000001")

            self.assertEqual(len(checks), 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title_match"], 1)


if __name__ == "__main__":
    unittest.main()
