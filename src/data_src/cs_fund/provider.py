"""China Securities Journal fund disclosure page adapter."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from pathlib import Path
from typing import Any

from src.data_src.common import to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument, DownloadedFile


class CsFundDisclosureDataSource:
    """Fetch fund disclosure metadata from newxinpi.cs.com.cn pages.

    No stable public JSON API has been confirmed for this platform. This adapter
    parses links from public pages and is intentionally conservative.
    """

    source = "cs_fund_disclosure"
    BASE_URL = "https://newxinpi.cs.com.cn/fund/"
    PAGE_BY_TYPE = {
        "latest": "L2_info_new.html",
        "annual": "L2_info_niandu.html",
        "semi_annual": "L2_info_banniandu.html",
        "q1": "L2_info_yijidu.html",
        "q2": "L2_info_erjidu.html",
        "q3": "L2_info_sanjidu.html",
        "q4": "L2_info_sijidu.html",
        "other": "L2_info_qita.html",
    }
    JSON_PATH_BY_TYPE = {
        "latest": "new",
        "annual": "niandu",
        "semi_annual": "banniandu",
        "q1": "yijidu",
        "q2": "erjidu",
        "q3": "sanjidu",
        "q4": "sijidu",
        "other": "qita",
    }

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": self.BASE_URL})

    def search_announcements(
        self,
        fund_code: str | None = None,
        report_type: str = "latest",
        page_no: int = 1,
    ) -> list[DisclosureDocument]:
        """Search fund disclosure links from a public page."""

        documents = self._search_json_announcements(fund_code, report_type, page_no)
        if documents:
            return documents

        page_name = self.PAGE_BY_TYPE.get(report_type, report_type)
        page_url = urllib.parse.urljoin(self.BASE_URL, page_name)
        html = self.http.get_text(page_url)
        documents = self._documents_from_html(html, page_url, report_type)
        if fund_code:
            documents = [document for document in documents if document.symbol == fund_code]
        return documents

    def download_pdf(
        self,
        document: DisclosureDocument,
        root_dir: str | Path = "data/disclosures",
    ) -> DownloadedFile:
        """Download a fund disclosure PDF with source-appropriate headers."""

        root = Path(root_dir)
        year = (
            document.announcement_date[:4]
            if document.announcement_date
            else "unknown"
        )
        filename = _safe_filename(
            f"{document.document_id}-{document.title}.pdf"
        )
        target_path = (
            root / self.source / document.symbol / year / filename
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.http.download_bytes(document.file_url)
        sha256 = hashlib.sha256(content).hexdigest()
        if target_path.exists():
            existing_hash = hashlib.sha256(
                target_path.read_bytes()
            ).hexdigest()
            if existing_hash == sha256:
                return DownloadedFile(
                    document_id=document.document_id,
                    file_path=str(target_path),
                    file_url=document.file_url,
                    sha256=sha256,
                    size_bytes=target_path.stat().st_size,
                    source=self.source,
                )
        target_path.write_bytes(content)
        return DownloadedFile(
            document_id=document.document_id,
            file_path=str(target_path),
            file_url=document.file_url,
            sha256=sha256,
            size_bytes=len(content),
            source=self.source,
        )

    def _search_json_announcements(
        self,
        fund_code: str | None,
        report_type: str,
        page_no: int,
    ) -> list[DisclosureDocument]:
        path_key = self.JSON_PATH_BY_TYPE.get(report_type)
        if not path_key:
            return []
        json_url = urllib.parse.urljoin(self.BASE_URL, f"/jijin/v1/{path_key}/{page_no}.json")
        try:
            payload = self.http.get_json(json_url)
        except Exception:
            return []
        rows = ((payload.get("data") or {}).get("data") or [])
        documents = [self._document_from_json(row, json_url, report_type) for row in rows]
        if fund_code:
            documents = [document for document in documents if document.symbol == fund_code]
        return documents

    def _document_from_json(self, row: dict[str, Any], page_url: str, report_type: str) -> DisclosureDocument:
        file_url = str(row.get("f003v") or "")
        title = str(row.get("f002v") or "")
        symbol = str(row.get("seccode") or row.get("f009v") or "")
        document_id = _stable_document_id(file_url or title)
        return DisclosureDocument(
            document_id=document_id,
            symbol=symbol,
            title=title,
            announcement_date=to_date_string(row.get("f001d")),
            category=report_type,
            source=self.source,
            file_url=file_url,
            page_url=page_url,
            raw=dict(row),
        )

    def _documents_from_html(self, html: str, page_url: str, report_type: str) -> list[DisclosureDocument]:
        documents: list[DisclosureDocument] = []
        for match in re.finditer(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', html, re.I | re.S):
            title = _strip_html(match.group("title"))
            href = match.group("href").strip()
            if not title or not href or href in {"#", "javascript:void(0)"}:
                continue
            file_url = urllib.parse.urljoin(page_url, href)
            surrounding = html[max(0, match.start() - 120) : min(len(html), match.end() + 120)]
            symbol = _find_fund_code(surrounding)
            announcement_date = _find_date(surrounding)
            documents.append(
                DisclosureDocument(
                    document_id=_stable_document_id(file_url or title),
                    symbol=symbol,
                    title=title,
                    announcement_date=announcement_date,
                    category=report_type,
                    source=self.source,
                    file_url=file_url,
                    page_url=page_url,
                    raw={"href": href, "surrounding": surrounding},
                )
            )
        return documents


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", text).strip()


def _find_fund_code(value: str) -> str:
    match = re.search(r"\b\d{6}\b", value)
    return match.group(0) if match else ""


def _find_date(value: str) -> str:
    match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value)
    if not match:
        return ""
    return to_date_string(match.group(0).replace("/", "-"))


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_filename(value: str) -> str:
    cleaned = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        value,
    ).strip(" ._")
    return cleaned[:180] or "document.pdf"
