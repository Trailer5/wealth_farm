"""OCR text extraction utilities."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from src.data_src.common import DependencyNotInstalledError
from src.data_src.models import DocumentText, OCRQueueItem


class PaddleOCRTextExtractor:
    """Extract text from queued PDF files with PaddleOCR."""

    extractor_name = "paddleocr"

    def __init__(self, paddleocr_module: Any | None = None, fitz_module: Any | None = None) -> None:
        self._paddleocr = paddleocr_module
        self._fitz = fitz_module

    def extract_text(self, queue_item: OCRQueueItem) -> DocumentText:
        """Run PaddleOCR over rendered PDF pages."""

        try:
            ocr = self._load_paddleocr().PaddleOCR(use_angle_cls=True, lang="ch")
            fitz = self._load_fitz()
            texts: list[str] = []
            page_count = 0
            for image_path in _render_pdf_pages(queue_item.file_path, fitz):
                page_count += 1
                result = ocr.ocr(str(image_path), cls=True)
                texts.extend(_extract_paddle_text(result))
            text_content = "\n".join(text for text in texts if text)
            return DocumentText(
                document_id=queue_item.document_id,
                text_content=text_content,
                extractor=self.extractor_name,
                page_count=page_count,
                status="extracted" if text_content else "empty",
                raw={"file_path": queue_item.file_path, "reason": queue_item.reason},
            )
        except Exception as exc:
            return _failed_text(queue_item, self.extractor_name, exc)

    def _load_paddleocr(self) -> Any:
        if self._paddleocr is not None:
            return self._paddleocr
        try:
            import paddleocr  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DependencyNotInstalledError("PaddleOCR is not installed.") from exc
        self._paddleocr = paddleocr
        return paddleocr

    def _load_fitz(self) -> Any:
        if self._fitz is not None:
            return self._fitz
        try:
            import fitz  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DependencyNotInstalledError("PyMuPDF is not installed.") from exc
        self._fitz = fitz
        return fitz


class TesseractOCRTextExtractor:
    """Extract text from queued PDF files with Tesseract OCR."""

    extractor_name = "tesseract"

    def __init__(
        self,
        pytesseract_module: Any | None = None,
        image_module: Any | None = None,
        fitz_module: Any | None = None,
        lang: str = "chi_sim+eng",
    ) -> None:
        self._pytesseract = pytesseract_module
        self._image = image_module
        self._fitz = fitz_module
        self.lang = lang

    def extract_text(self, queue_item: OCRQueueItem) -> DocumentText:
        """Run Tesseract over rendered PDF pages."""

        try:
            pytesseract = self._load_pytesseract()
            image_module = self._load_image()
            fitz = self._load_fitz()
            texts: list[str] = []
            page_count = 0
            for image_path in _render_pdf_pages(queue_item.file_path, fitz):
                page_count += 1
                image = image_module.open(image_path)
                texts.append(pytesseract.image_to_string(image, lang=self.lang).strip())
            text_content = "\n".join(text for text in texts if text)
            return DocumentText(
                document_id=queue_item.document_id,
                text_content=text_content,
                extractor=self.extractor_name,
                page_count=page_count,
                status="extracted" if text_content else "empty",
                raw={"file_path": queue_item.file_path, "reason": queue_item.reason},
            )
        except Exception as exc:
            return _failed_text(queue_item, self.extractor_name, exc)

    def _load_pytesseract(self) -> Any:
        if self._pytesseract is not None:
            return self._pytesseract
        try:
            import pytesseract  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DependencyNotInstalledError("pytesseract is not installed.") from exc
        self._pytesseract = pytesseract
        return pytesseract

    def _load_image(self) -> Any:
        if self._image is not None:
            return self._image
        try:
            from PIL import Image  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DependencyNotInstalledError("Pillow is not installed.") from exc
        self._image = Image
        return Image

    def _load_fitz(self) -> Any:
        if self._fitz is not None:
            return self._fitz
        try:
            import fitz  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DependencyNotInstalledError("PyMuPDF is not installed.") from exc
        self._fitz = fitz
        return fitz


def _render_pdf_pages(file_path: str, fitz_module: Any) -> list[Path]:
    """Render PDF pages to PNG files in a temporary directory."""

    output_dir = Path(tempfile.mkdtemp(prefix="wealth_farm_ocr_"))
    image_paths: list[Path] = []
    with fitz_module.open(file_path) as document:
        matrix = fitz_module.Matrix(2, 2)
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=matrix)
            image_path = output_dir / f"page_{index:04d}.png"
            pixmap.save(str(image_path))
            image_paths.append(image_path)
    return image_paths


def _extract_paddle_text(result: Any) -> list[str]:
    texts: list[str] = []
    if result is None:
        return texts
    if isinstance(result, str):
        return [result]
    if isinstance(result, tuple) and result and isinstance(result[0], str):
        return [result[0]]
    if isinstance(result, list):
        for item in result:
            texts.extend(_extract_paddle_text(item))
    return texts


def _failed_text(queue_item: OCRQueueItem, extractor: str, exc: Exception) -> DocumentText:
    return DocumentText(
        document_id=queue_item.document_id,
        text_content="",
        extractor=extractor,
        page_count=None,
        status="failed",
        error=str(exc),
        raw={"file_path": queue_item.file_path, "reason": queue_item.reason},
    )
