import tempfile
import unittest
from pathlib import Path

from src.data_src.csrc import CsrcPublicInfoDataSource
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeCsrcHttpClient:
    def __init__(self):
        self.last_url = None

    def get_text(self, url, params=None):
        self.last_url = url
        return """
        <div>
          <span>2026-03-26</span>
          <a href="/csrc/c101953/c7547359/files/rule.pdf">上市公司信息披露管理办法.pdf</a>
          <a href="/csrc/c100028/c7439568/content.shtml">证监会发布公告电子化规范</a>
        </div>
        """


class CsrcPublicInfoDataSourceTest(unittest.TestCase):
    def test_search_public_documents_parses_links(self):
        fake = FakeCsrcHttpClient()
        source = CsrcPublicInfoDataSource(fake)

        documents = source.search_public_documents(
            ["https://www.csrc.gov.cn/csrc/c101953/c7547359/content.shtml"],
            keyword="信息披露",
            category="rule",
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].symbol, "CSRC")
        self.assertEqual(documents[0].source, "csrc_public")
        self.assertEqual(documents[0].announcement_date, "2026-03-26")
        self.assertEqual(documents[0].file_url, "https://www.csrc.gov.cn/csrc/c101953/c7547359/files/rule.pdf")


class CsrcPublicInfoServiceTest(unittest.TestCase):
    def test_csrc_public_documents_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(store=store, csrc_source=CsrcPublicInfoDataSource(FakeCsrcHttpClient()))

            result = service.fetch_and_store_csrc_public_documents(
                ["https://www.csrc.gov.cn/csrc/c101953/c7547359/content.shtml"],
                keyword="信息披露",
                category="rule",
            )
            rows = store.get_disclosure_documents("CSRC", source="csrc_public")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(rows[0]["title"], "上市公司信息披露管理办法.pdf")


if __name__ == "__main__":
    unittest.main()
