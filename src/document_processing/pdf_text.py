"""PDF text extraction utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.data_src.common import DependencyNotInstalledError
from src.data_src.models import DocumentText, DownloadedFile


class PDFTextExtractor:
    """Extract text from PDF files with PyMuPDF."""

    extractor_name = "pymupdf"

    def __init__(self, fitz_module: Any | None = None) -> None:
        self._fitz = fitz_module

    def extract_text(self, downloaded_file: DownloadedFile) -> DocumentText:
        """Extract text and return a storable result object."""

        try:
            fitz = self._load_fitz()
            file_path = Path(downloaded_file.file_path)
            with fitz.open(file_path) as document:
                pages = [page.get_text("text") for page in document]
                text_content = "\n\n".join(page.strip() for page in pages if page and page.strip())
                status = "extracted" if text_content else "empty"
                return DocumentText(
                    document_id=downloaded_file.document_id,
                    text_content=text_content,
                    extractor=self.extractor_name,
                    page_count=len(document),
                    status=status,
                    raw={"file_path": str(file_path), "source": downloaded_file.source},
                )
        except Exception as exc:
            return DocumentText(
                document_id=downloaded_file.document_id,
                text_content="",
                extractor=self.extractor_name,
                page_count=None,
                status="failed",
                error=str(exc),
                raw={"file_path": downloaded_file.file_path, "source": downloaded_file.source},
            )

    def _load_fitz(self) -> Any:
        if self._fitz is not None:
            return self._fitz
        try:
            import fitz  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise DependencyNotInstalledError(
                "PyMuPDF is not installed. Add `PyMuPDF` to dependencies and install it."
            ) from exc
        self._fitz = fitz
        return fitz
