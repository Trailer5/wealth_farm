import tempfile
import unittest
from pathlib import Path

from src.data_src.cs_fund import CsFundDisclosureDataSource
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeCsFundHttpClient:
    def __init__(self, fail_json=False):
        self.last_url = None
        self.fail_json = fail_json

    def get_json(self, url, params=None):
        self.last_url = url
        if self.fail_json:
            raise RuntimeError("json unavailable")
        return {
            "data": {
                "data": [
                    {
                        "seccode": "000001",
                        "secname": "测试基金",
                        "f001d": "2026-03-30T00:00:00.000+00:00",
                        "f002v": "测试基金2025年年度报告",
                        "f003v": "https://newxinpi.cs.com.cn/fund/disclosure/2026/notice.pdf",
                    }
                ]
            }
        }

    def get_text(self, url, params=None):
        self.last_url = url
        return """
        <table>
          <tr>
            <td>000001</td>
            <td><a href="/fund/disclosure/2026/notice.pdf">测试基金2025年年度报告</a></td>
            <td>2026-03-30</td>
          </tr>
        </table>
        """


class CsFundDisclosureDataSourceTest(unittest.TestCase):
    def test_search_announcements_uses_dynamic_json(self):
        fake = FakeCsFundHttpClient()
        source = CsFundDisclosureDataSource(fake)

        documents = source.search_announcements(fund_code="000001", report_type="annual")

        self.assertIn("/jijin/v1/niandu/1.json", fake.last_url)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].symbol, "000001")
        self.assertEqual(documents[0].title, "测试基金2025年年度报告")
        self.assertEqual(documents[0].announcement_date, "2026-03-30")
        self.assertEqual(documents[0].file_url, "https://newxinpi.cs.com.cn/fund/disclosure/2026/notice.pdf")

    def test_search_announcements_falls_back_to_page_links(self):
        fake = FakeCsFundHttpClient(fail_json=True)
        source = CsFundDisclosureDataSource(fake)

        documents = source.search_announcements(fund_code="000001", report_type="annual")

        self.assertIn("L2_info_niandu.html", fake.last_url)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].symbol, "000001")


class CsFundDisclosureServiceTest(unittest.TestCase):
    def test_cs_fund_disclosure_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, cs_fund_source=CsFundDisclosureDataSource(FakeCsFundHttpClient()))

            result = service.fetch_and_store_cs_fund_disclosure_documents("000001", report_type="annual")
            rows = store.get_disclosure_documents("000001", source="cs_fund_disclosure")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "测试基金2025年年度报告")


if __name__ == "__main__":
    unittest.main()
