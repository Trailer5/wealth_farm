import tempfile
import unittest
from pathlib import Path

from src.data_src.service import DataFetchService
from src.data_src.szse import SzseDisclosureDataSource
from src.data_store import SQLiteDataStore


class FakeSzseHttpClient:
    def __init__(self):
        self.last_url = None
        self.last_data = None

    def post_json(self, url, data):
        self.last_url = url
        self.last_data = data
        return {
            "data": [
                {
                    "id": "szse-1",
                    "secCode": ["000001"],
                    "secName": ["平安银行"],
                    "title": "平安银行2025年年度报告",
                    "publishTime": "2026-03-20",
                    "attachPath": "/disc/disk03/finalpage/2026-03-20/000001.PDF",
                    "bigCategoryId": "010301",
                }
            ],
            "announceCount": 1,
        }


class SzseDisclosureDataSourceTest(unittest.TestCase):
    def test_search_announcements_builds_payload_and_documents(self):
        fake = FakeSzseHttpClient()
        source = SzseDisclosureDataSource(fake)

        documents = source.search_announcements("000001", "20260101", "20260331")

        self.assertEqual(fake.last_data["stock"], ["000001"])
        self.assertEqual(fake.last_data["seDate"], ["2026-01-01", "2026-03-31"])
        self.assertEqual(fake.last_data["channelCode"], ["listedNotice_disc"])
        self.assertEqual(documents[0].document_id, "szse-1")
        self.assertEqual(documents[0].source, "szse")
        self.assertEqual(documents[0].file_url, "https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-03-20/000001.PDF")


class SzseDisclosureServiceTest(unittest.TestCase):
    def test_szse_disclosure_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, szse_source=SzseDisclosureDataSource(FakeSzseHttpClient()))

            result = service.fetch_and_store_szse_disclosure_documents("000001", "20260101", "20260331")
            rows = store.get_disclosure_documents("000001", source="szse")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "平安银行2025年年度报告")


if __name__ == "__main__":
    unittest.main()
