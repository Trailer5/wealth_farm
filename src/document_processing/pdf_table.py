"""PDF table extraction utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.data_src.common import DependencyNotInstalledError
from src.data_src.models import DocumentTable, DownloadedFile


class PDFTableExtractor:
    """Extract tables from PDF files with pdfplumber."""

    extractor_name = "pdfplumber"

    def __init__(self, pdfplumber_module: Any | None = None) -> None:
        self._pdfplumber = pdfplumber_module

    def extract_tables(self, downloaded_file: DownloadedFile) -> list[DocumentTable]:
        """Extract tables and return storable table objects."""

        try:
            pdfplumber = self._load_pdfplumber()
            file_path = Path(downloaded_file.file_path)
            tables: list[DocumentTable] = []
            with pdfplumber.open(file_path) as document:
                table_index = 0
                for page_number, page in enumerate(document.pages, start=1):
                    for table in page.extract_tables() or []:
                        normalized = _normalize_table(table)
                        if not normalized:
                            continue
                        tables.append(
                            DocumentTable(
                                document_id=downloaded_file.document_id,
                                table_index=table_index,
                                page_number=page_number,
                                rows=normalized,
                                extractor=self.extractor_name,
                                status="extracted",
                                raw={"file_path": str(file_path), "source": downloaded_file.source},
                            )
                        )
                        table_index += 1
            if tables:
                return tables
            return [
                DocumentTable(
                    document_id=downloaded_file.document_id,
                    table_index=0,
                    page_number=None,
                    rows=[],
                    extractor=self.extractor_name,
                    status="empty",
                    raw={"file_path": downloaded_file.file_path, "source": downloaded_file.source},
                )
            ]
        except Exception as exc:
            return [
                DocumentTable(
                    document_id=downloaded_file.document_id,
                    table_index=0,
                    page_number=None,
                    rows=[],
                    extractor=self.extractor_name,
                    status="failed",
                    error=str(exc),
                    raw={"file_path": downloaded_file.file_path, "source": downloaded_file.source},
                )
            ]

    def _load_pdfplumber(self) -> Any:
        if self._pdfplumber is not None:
            return self._pdfplumber
        try:
            import pdfplumber  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise DependencyNotInstalledError(
                "pdfplumber is not installed. Add `pdfplumber` to dependencies and install it."
            ) from exc
        self._pdfplumber = pdfplumber
        return pdfplumber


def _normalize_table(table: list[list[Any]]) -> list[list[str | None]]:
    normalized: list[list[str | None]] = []
    for row in table:
        normalized_row = [None if cell is None else str(cell).strip() for cell in row]
        if any(cell for cell in normalized_row):
            normalized.append(normalized_row)
    return normalized
