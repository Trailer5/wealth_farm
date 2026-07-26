import tempfile
import unittest
from pathlib import Path

from src.data_src.models import DocumentTable, DocumentText, DownloadedFile, OCRQueueItem
from src.document_processing import (
    FinancialFieldExtractor,
    PaddleOCRTextExtractor,
    PDFTableExtractor,
    PDFTextExtractor,
    TesseractOCRTextExtractor,
)


class FakePage:
    def __init__(self, text):
        self.text = text

    def get_text(self, mode):
        if mode != "text":
            raise AssertionError("PDF extractor should request text mode")
        return self.text


class FakeDocument:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def __iter__(self):
        return iter(self.pages)

    def __len__(self):
        return len(self.pages)


class FakeFitz:
    class Matrix:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    def open(self, path):
        if not Path(path).exists():
            raise AssertionError("PDF path should exist")
        return FakeDocument([FakePage("第一页"), FakePage("第二页")])


class FakePixmap:
    def save(self, path):
        Path(path).write_bytes(b"fake image")


class FakeOCRPage:
    def get_pixmap(self, matrix=None):
        return FakePixmap()


class FakeOCRDocument(FakeDocument):
    def __init__(self):
        super().__init__([FakeOCRPage()])


class FakeOCRFitz(FakeFitz):
    def open(self, path):
        if not Path(path).exists():
            raise AssertionError("PDF path should exist")
        return FakeOCRDocument()


class FakeTablePage:
    def extract_tables(self):
        return [[["项目", "金额"], ["收入", "100"], [None, None]]]


class FakePdfPlumberDocument:
    pages = [FakeTablePage()]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class FakePdfPlumber:
    def open(self, path):
        if not Path(path).exists():
            raise AssertionError("PDF path should exist")
        return FakePdfPlumberDocument()


class FakePaddleOCRInstance:
    def ocr(self, image_path, cls=True):
        return [[[[0, 0], [1, 1]], ("Paddle正文", 0.99)]]


class FakePaddleOCRModule:
    def PaddleOCR(self, **kwargs):
        return FakePaddleOCRInstance()


class FakePytesseract:
    def image_to_string(self, image, lang=None):
        return "Tesseract正文"


class FakeImageModule:
    def open(self, image_path):
        return object()


class PDFTextExtractorTest(unittest.TestCase):
    def test_extract_text_with_fitz_like_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "test.pdf"
            pdf_path.write_bytes(b"%PDF fake")
            downloaded = DownloadedFile(
                document_id="doc-1",
                file_path=str(pdf_path),
                file_url="https://example.com/test.pdf",
                sha256="x" * 64,
                size_bytes=9,
                source="unit-test",
            )

            result = PDFTextExtractor(FakeFitz()).extract_text(downloaded)

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.page_count, 2)
            self.assertIn("第一页", result.text_content)
            self.assertIn("第二页", result.text_content)


class PDFTableExtractorTest(unittest.TestCase):
    def test_extract_tables_with_pdfplumber_like_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "test.pdf"
            pdf_path.write_bytes(b"%PDF fake")
            downloaded = DownloadedFile(
                document_id="doc-1",
                file_path=str(pdf_path),
                file_url="https://example.com/test.pdf",
                sha256="x" * 64,
                size_bytes=9,
                source="unit-test",
            )

            result = PDFTableExtractor(FakePdfPlumber()).extract_tables(downloaded)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].status, "extracted")
            self.assertEqual(result[0].page_number, 1)
            self.assertEqual(result[0].rows[0], ["项目", "金额"])
            self.assertEqual(result[0].rows[1], ["收入", "100"])


class OCRExtractorTest(unittest.TestCase):
    def test_paddleocr_text_extractor_with_fake_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "test.pdf"
            pdf_path.write_bytes(b"%PDF fake")
            item = OCRQueueItem(
                document_id="doc-1",
                file_path=str(pdf_path),
                reason="empty",
                status="queued",
                attempts=0,
                source="unit-test",
            )

            result = PaddleOCRTextExtractor(FakePaddleOCRModule(), FakeOCRFitz()).extract_text(item)

            self.assertEqual(result.status, "extracted")
            self.assertIn("Paddle正文", result.text_content)

    def test_tesseract_text_extractor_with_fake_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "test.pdf"
            pdf_path.write_bytes(b"%PDF fake")
            item = OCRQueueItem(
                document_id="doc-1",
                file_path=str(pdf_path),
                reason="empty",
                status="queued",
                attempts=0,
                source="unit-test",
            )

            result = TesseractOCRTextExtractor(FakePytesseract(), FakeImageModule(), FakeOCRFitz()).extract_text(item)

            self.assertEqual(result.status, "extracted")
            self.assertIn("Tesseract正文", result.text_content)


class FinancialFieldExtractorTest(unittest.TestCase):
    def test_extract_standard_fields_from_table_rows(self):
        table = DocumentTable(
            document_id="doc-1",
            table_index=0,
            page_number=3,
            rows=[
                ["项目", "本期金额"],
                ["营业收入", "1,000.50万元"],
                ["净利润", "200.25万元"],
                ["资产总计", "3000"],
                ["负债合计", "1200"],
                ["经营活动产生的现金流量净额", "88.8"],
            ],
            extractor="unit-test",
            status="extracted",
        )

        fields = FinancialFieldExtractor().extract_from_tables([table])
        by_name = {field.field_name: field for field in fields}

        self.assertEqual(by_name["revenue"].value, 10005000.0)
        self.assertEqual(by_name["net_profit"].value, 2002500.0)
        self.assertEqual(by_name["total_assets"].page_number, 3)
        self.assertEqual(by_name["operating_cash_flow"].field_label, "经营活动产生的现金流量净额")

    def test_extract_standard_fields_from_text_lines(self):
        text = DocumentText(
            document_id="doc-1",
            text_content="营业收入 12.5亿元\n净利润（2.5）万元\n资产总计 100元",
            extractor="unit-test",
            page_count=1,
            status="extracted",
        )

        fields = FinancialFieldExtractor().extract_from_texts([text])
        by_name = {field.field_name: field for field in fields}

        self.assertEqual(by_name["revenue"].value, 1250000000.0)
        self.assertEqual(by_name["net_profit"].value, -25000.0)
        self.assertEqual(by_name["total_assets"].value, 100.0)
        self.assertEqual(by_name["revenue"].source, "text_keyword_v1")

    def test_extract_prefers_current_period_columns(self):
        table = DocumentTable(
            document_id="doc-1",
            table_index=0,
            page_number=5,
            rows=[
                ["项目", "本期金额", "上期金额"],
                ["营业收入", "100万元", "80万元"],
                ["净利润", "20万元", "15万元"],
                ["项目", "期末余额", "年初余额"],
                ["资产总计", "500万元", "450万元"],
            ],
            extractor="unit-test",
            status="extracted",
        )

        fields = FinancialFieldExtractor().extract_from_tables([table])
        by_name = {field.field_name: field for field in fields}

        self.assertEqual(by_name["revenue"].value, 1000000.0)
        self.assertEqual(by_name["revenue"].column_index, 1)
        self.assertEqual(by_name["net_profit"].value, 200000.0)
        self.assertEqual(by_name["total_assets"].value, 5000000.0)


if __name__ == "__main__":
    unittest.main()
