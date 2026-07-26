import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.data_src.cninfo import CninfoDataSource
from src.data_src.models import DocumentTable, DocumentText, OCRQueueItem
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore


class FakeCninfoHttpClient:
    def __init__(self):
        self.post_payloads = []
        self.download_count = 0

    def get_json(self, url):
        return {
            "stockList": [
                {
                    "code": "000001",
                    "orgId": "gssz0000001",
                    "zwjc": "平安银行",
                    "column": "szse",
                    "plate": "sz",
                }
            ]
        }

    def post_json(self, url, data):
        self.post_payloads.append(data)
        return {
            "announcements": [
                {
                    "announcementId": "1212345678",
                    "secCode": "000001",
                    "secName": "平安银行",
                    "announcementTitle": "<em>2025</em>年年度报告",
                    "announcementTime": "2026-03-20",
                    "adjunctUrl": "finalpage/2026-03-20/1212345678.PDF",
                    "category": "category_ndbg_szsh;",
                }
            ],
            "totalRecordNum": 1,
        }

    def download_bytes(self, url):
        self.download_count += 1
        return b"%PDF-1.4 fake pdf"


class FakePDFTextExtractor:
    def extract_text(self, downloaded_file):
        return DocumentText(
            document_id=downloaded_file.document_id,
            text_content="这是PDF正文\n营业收入 100万元",
            extractor="fake",
            page_count=1,
            status="extracted",
            raw={"file_path": downloaded_file.file_path},
        )


class FakeEmptyPDFTextExtractor:
    def extract_text(self, downloaded_file):
        return DocumentText(
            document_id=downloaded_file.document_id,
            text_content="",
            extractor="fake-empty",
            page_count=1,
            status="empty",
            raw={"file_path": downloaded_file.file_path},
        )


class FakePDFTableExtractor:
    def extract_tables(self, downloaded_file):
        return [
            DocumentTable(
                document_id=downloaded_file.document_id,
                table_index=0,
                page_number=1,
                rows=[["项目", "金额"], ["营业收入", "100"], ["净利润", "20"]],
                extractor="fake",
                status="extracted",
                raw={"file_path": downloaded_file.file_path},
            )
        ]


class FakeOCRExtractor:
    def __init__(self, status="extracted"):
        self.status = status

    def extract_text(self, queue_item):
        return DocumentText(
            document_id=queue_item.document_id,
            text_content="OCR正文" if self.status == "extracted" else "",
            extractor="fake-ocr",
            page_count=1 if self.status == "extracted" else None,
            status=self.status,
            error=None if self.status == "extracted" else "ocr failed",
            raw={"file_path": queue_item.file_path},
        )


class CninfoDataSourceTest(unittest.TestCase):
    def test_search_announcements_resolves_org_id_and_normalizes_documents(self):
        fake = FakeCninfoHttpClient()
        source = CninfoDataSource(fake)

        documents = source.search_announcements(
            symbol="000001",
            start_date="20260101",
            end_date="20260331",
            category=CninfoDataSource.CATEGORY_ANNUAL_REPORT,
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(fake.post_payloads[0]["stock"], "000001,gssz0000001")
        self.assertEqual(fake.post_payloads[0]["seDate"], "2026-01-01~2026-03-31")
        self.assertEqual(documents[0].title, "2025年年度报告")
        self.assertEqual(documents[0].file_url, "https://static.cninfo.com.cn/finalpage/2026-03-20/1212345678.PDF")

    def test_download_pdf_writes_file_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = CninfoDataSource(FakeCninfoHttpClient())
            document = source.search_announcements("000001", "20260101", "20260331")[0]

            downloaded = source.download_pdf(document, root_dir=temp_dir)

            self.assertTrue(Path(downloaded.file_path).exists())
            self.assertEqual(downloaded.size_bytes, len(b"%PDF-1.4 fake pdf"))
            self.assertEqual(len(downloaded.sha256), 64)


class DisclosureStoreServiceTest(unittest.TestCase):
    def test_disclosure_documents_and_files_are_stored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                cninfo_source=CninfoDataSource(FakeCninfoHttpClient()),
                pdf_text_extractor=FakePDFTextExtractor(),
                pdf_table_extractor=FakePDFTableExtractor(),
            )

            result = service.fetch_and_store_disclosure_documents(
                "000001",
                "20260101",
                "20260331",
                category=CninfoDataSource.CATEGORY_ANNUAL_REPORT,
            )
            documents = store.get_disclosure_documents("000001")
            files = service.download_and_store_disclosure_files(
                service.fetch_disclosure_documents("000001", "20260101", "20260331"),
                root_dir=str(Path(temp_dir) / "disclosures"),
            )

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(documents[0]["title"], "2025年年度报告")
            self.assertEqual(files[0].status, "downloaded")

            texts = service.extract_and_store_document_texts(files)
            stored_text = store.get_document_text(files[0].document_id)

            self.assertEqual(texts[0].status, "extracted")
            self.assertIn("这是PDF正文", stored_text["text_content"])
            self.assertEqual(stored_text["page_count"], 1)

            tables = service.extract_and_store_document_tables(files)
            stored_tables = store.get_document_tables(files[0].document_id)

            self.assertEqual(tables[0].status, "extracted")
            self.assertEqual(stored_tables[0]["page_number"], 1)
            self.assertIn("营业收入", stored_tables[0]["rows_json"])

            fields = service.extract_and_store_financial_fields(tables)
            stored_fields = store.get_financial_fields(files[0].document_id)

            self.assertEqual(fields[0].field_name, "revenue")
            self.assertTrue(any(field["field_name"] == "net_profit" for field in stored_fields))

            text_fields = service.extract_and_store_financial_fields_from_texts(texts)
            stored_fields = store.get_financial_fields(files[0].document_id)

            self.assertEqual(text_fields[0].source, "text_keyword_v1")
            self.assertTrue(any(field["source"] == "text_keyword_v1" for field in stored_fields))

    def test_empty_pdf_text_is_queued_for_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                cninfo_source=CninfoDataSource(FakeCninfoHttpClient()),
                pdf_text_extractor=FakeEmptyPDFTextExtractor(),
            )
            files = service.download_and_store_disclosure_files(
                service.fetch_disclosure_documents("000001", "20260101", "20260331"),
                root_dir=str(Path(temp_dir) / "disclosures"),
            )

            texts = service.extract_and_store_document_texts(files)
            queue = store.get_ocr_queue_items(status="queued")

            self.assertEqual(texts[0].status, "empty")
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0]["document_id"], files[0].document_id)
            self.assertEqual(queue[0]["reason"], "empty")
            self.assertEqual(queue[0]["attempts"], 0)

    def test_process_ocr_queue_marks_done(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                cninfo_source=CninfoDataSource(FakeCninfoHttpClient()),
                pdf_text_extractor=FakeEmptyPDFTextExtractor(),
                ocr_extractors={"fake": FakeOCRExtractor()},
            )
            files = service.download_and_store_disclosure_files(
                service.fetch_disclosure_documents("000001", "20260101", "20260331"),
                root_dir=str(Path(temp_dir) / "disclosures"),
            )
            service.extract_and_store_document_texts(files)

            now = datetime(2026, 1, 5, 10, 0, 0)
            results = service.process_ocr_queue(engine="fake", now=now)
            queue = store.get_ocr_queue_items()
            stored_text = store.get_document_text(files[0].document_id)

            self.assertEqual(results[0].status, "extracted")
            self.assertEqual(queue[0]["status"], "done")
            self.assertEqual(queue[0]["attempts"], 1)
            self.assertEqual(queue[0]["completed_at"], "2026-01-05 10:00:00")
            self.assertEqual(stored_text["text_content"], "OCR正文")

    def test_process_ocr_queue_requeues_failed_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                cninfo_source=CninfoDataSource(FakeCninfoHttpClient()),
                pdf_text_extractor=FakeEmptyPDFTextExtractor(),
                ocr_extractors={"fake": FakeOCRExtractor(status="failed")},
            )
            files = service.download_and_store_disclosure_files(
                service.fetch_disclosure_documents("000001", "20260101", "20260331"),
                root_dir=str(Path(temp_dir) / "disclosures"),
            )
            service.extract_and_store_document_texts(files)

            results = service.process_ocr_queue(
                engine="fake",
                retry_delay_minutes=30,
                now=datetime(2026, 1, 5, 10, 0, 0),
            )
            queue = store.get_ocr_queue_items()

            self.assertEqual(results[0].status, "failed")
            self.assertEqual(queue[0]["status"], "queued")
            self.assertEqual(queue[0]["attempts"], 1)
            self.assertEqual(queue[0]["next_attempt_at"], "2026-01-05 10:30:00")

    def test_process_ocr_queue_marks_review_required_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                ocr_extractors={"fake": FakeOCRExtractor(status="failed")},
            )
            store.initialize()
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF scan")
            store.upsert_ocr_queue_items(
                [
                    OCRQueueItem(
                        document_id="doc-review",
                        file_path=str(pdf_path),
                        reason="failed",
                        status="queued",
                        attempts=2,
                        max_attempts=3,
                        source="unit-test",
                    )
                ]
            )

            results = service.process_ocr_queue(engine="fake", now=datetime(2026, 1, 5, 10, 0, 0))
            queue = store.get_ocr_queue_items()

            self.assertEqual(results[0].status, "failed")
            self.assertEqual(queue[0]["status"], "review_required")
            self.assertEqual(queue[0]["attempts"], 3)

    def test_financial_report_helper_uses_cninfo_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_http = FakeCninfoHttpClient()
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                cninfo_source=CninfoDataSource(fake_http),
            )

            result = service.fetch_and_store_financial_report_documents(
                "000001",
                "20260101",
                "20260331",
                report_type="annual",
            )

            self.assertEqual(result.stored_count, 1)
            self.assertEqual(fake_http.post_payloads[0]["category"], CninfoDataSource.CATEGORY_ANNUAL_REPORT)


if __name__ == "__main__":
    unittest.main()
