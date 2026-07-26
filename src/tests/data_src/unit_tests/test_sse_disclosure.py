import tempfile
import unittest
from pathlib import Path

from src.data_src.service import DataFetchService
from src.data_src.sse import SseDisclosureDataSource
from src.data_store import SQLiteDataStore


class FakeSseHttpClient:
    def __init__(self):
        self.last_url = None
        self.last_params = None
        self.headers = {}

    def get_json(self, url, params=None):
        self.last_url = url
        self.last_params = params
        return {
            "result": [
                {
                    "BULLETIN_ID": "sse-1",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME": "贵州茅台",
                    "TITLE": "贵州茅台2025年年度报告",
                    "ADDDATE": "2026-03-30",
                    "URL": "/disclosure/listedinfo/announcement/c/new/2026-03-30/600519_20260330.pdf",
                }
            ]
        }


class SseDisclosureDataSourceTest(unittest.TestCase):
    def test_search_announcements_builds_query_and_documents(self):
        fake = FakeSseHttpClient()
        source = SseDisclosureDataSource(fake)

        documents = source.search_announcements("600519", "20260101", "20260331", report_type="YEARLY")

        self.assertEqual(fake.last_params["productId"], "600519")
        self.assertEqual(fake.last_params["reportType"], "YEARLY")
        self.assertEqual(fake.last_params["beginDate"], "2026-01-01")
        self.assertEqual(documents[0].document_id, "sse-1")
        self.assertEqual(documents[0].source, "sse")
        self.assertEqual(documents[0].file_url, "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-03-30/600519_20260330.pdf")


class SseDisclosureServiceTest(unittest.TestCase):
    def test_sse_disclosure_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, sse_source=SseDisclosureDataSource(FakeSseHttpClient()))

            result = service.fetch_and_store_sse_disclosure_documents(
                "600519",
                "20260101",
                "20260331",
                report_type="YEARLY",
            )
            rows = store.get_disclosure_documents("600519", source="sse")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "贵州茅台2025年年度报告")


if __name__ == "__main__":
    unittest.main()
