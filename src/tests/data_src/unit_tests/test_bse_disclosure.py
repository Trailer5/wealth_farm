import tempfile
import unittest
from pathlib import Path

from src.data_src.bse import BseDisclosureDataSource
from src.data_src.models import DisclosureDocument
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeCninfoForBse:
    def search_announcements(self, symbol, start_date, end_date, category="", search_key=""):
        return [
            DisclosureDocument(
                document_id="bse-cninfo-1",
                symbol=symbol,
                title="北交所公司2025年年度报告",
                announcement_date="2026-03-30",
                category=category,
                source="cninfo",
                file_url="https://static.cninfo.com.cn/finalpage/2026-03-30/bse.pdf",
                raw={"symbol": symbol},
            )
        ]


class BseDisclosureDataSourceTest(unittest.TestCase):
    def test_bse_source_delegates_to_cninfo_and_relabels_source(self):
        source = BseDisclosureDataSource(cninfo_source=FakeCninfoForBse())

        documents = source.search_announcements("833171", "20260101", "20260331")

        self.assertEqual(documents[0].source, "bse_cninfo")
        self.assertEqual(documents[0].raw["delegated_source"], "cninfo")
        self.assertEqual(documents[0].symbol, "833171")


class BseDisclosureServiceTest(unittest.TestCase):
    def test_bse_disclosure_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, bse_source=BseDisclosureDataSource(FakeCninfoForBse()))

            result = service.fetch_and_store_bse_disclosure_documents("833171", "20260101", "20260331")
            rows = store.get_disclosure_documents("833171", source="bse_cninfo")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "北交所公司2025年年度报告")


if __name__ == "__main__":
    unittest.main()
