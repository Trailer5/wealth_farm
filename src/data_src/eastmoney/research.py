"""Eastmoney research report metadata source."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from src.data_src.common import DataSourceError, normalize_date_dash, to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DownloadedFile, ResearchReport


class EastmoneyResearchReportDataSource:
    """Fetch third-party research report metadata from Eastmoney."""

    source = "eastmoney_research"
    REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
    PDF_URL_TEMPLATE = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://data.eastmoney.com/",
            },
            retry_count=3,
        )

    def search_stock_reports(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        page_no: int = 1,
        page_size: int = 100,
    ) -> list[ResearchReport]:
        """Fetch stock research report metadata for one A-share symbol."""

        payload = self.http.get_json(
            self.REPORT_LIST_URL,
            {
                "code": symbol.strip().split(".")[0],
                "pageSize": str(page_size),
                "pageNo": str(page_no),
                "beginTime": normalize_date_dash(start_date),
                "endTime": normalize_date_dash(end_date),
                "qType": "0",
                "orgCode": "",
                "industryCode": "*",
                "rating": "*",
                "ratingChange": "*",
                "fields": "",
            },
        )
        rows = payload.get("data") or []
        return [self._report_from_row(row) for row in rows]

    def _report_from_row(self, row: dict[str, Any]) -> ResearchReport:
        report_id = str(row.get("infoCode") or row.get("encodeUrl") or row.get("title") or "")
        info_code = row.get("infoCode")
        pdf_url = self.PDF_URL_TEMPLATE.format(info_code=info_code) if info_code else row.get("pdfUrl")
        return ResearchReport(
            report_id=report_id,
            symbol=str(row.get("stockCode") or "") or None,
            title=str(row.get("title") or row.get("reportName") or ""),
            publish_date=to_date_string(row.get("publishDate")),
            institution=row.get("orgSName") or row.get("orgName"),
            rating=row.get("emRatingName") or row.get("rating"),
            industry=row.get("indvInduName") or row.get("industryName"),
            pdf_url=pdf_url,
            source=self.source,
            raw=dict(row),
        )

    def download_pdf(
        self,
        report: ResearchReport,
        root_dir: str | Path = "data/research_reports",
    ) -> DownloadedFile:
        """Download a research report PDF when explicitly requested.

        Eastmoney report metadata is useful, but live PDF URLs can return a
        small JavaScript page instead of a real PDF. Keep this route explicit
        and reject non-PDF bytes until a more reliable report file source is
        found.
        """

        if not report.pdf_url:
            raise ValueError(f"Research report has no PDF URL: {report.report_id}")
        target_path = self._target_pdf_path(report, Path(root_dir))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.http.download_bytes(report.pdf_url)
        if content[:4] != b"%PDF":
            raise DataSourceError(f"Research report download did not return a PDF: {report.pdf_url}")
        sha256 = hashlib.sha256(content).hexdigest()
        if target_path.exists():
            existing_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
            if existing_hash == sha256:
                return DownloadedFile(
                    document_id=report.report_id,
                    file_path=str(target_path),
                    file_url=report.pdf_url,
                    sha256=sha256,
                    size_bytes=target_path.stat().st_size,
                    source=self.source,
                )
        target_path.write_bytes(content)
        return DownloadedFile(
            document_id=report.report_id,
            file_path=str(target_path),
            file_url=report.pdf_url,
            sha256=sha256,
            size_bytes=len(content),
            source=self.source,
        )

    def _target_pdf_path(self, report: ResearchReport, root_dir: Path) -> Path:
        year = report.publish_date[:4] if report.publish_date else "unknown"
        symbol = report.symbol or "unknown"
        filename = _safe_filename(f"{report.publish_date}-{report.institution or 'unknown'}-{report.title}.pdf")
        return root_dir / self.source / symbol / year / filename


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", value).strip(" ._")
    return cleaned[:180] or "research_report.pdf"
