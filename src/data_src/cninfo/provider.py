"""CNINFO official disclosure data source adapter."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.data_src.common import DataSourceError, normalize_date_dash, to_date_string
from src.data_src.models import DisclosureDocument, DownloadedFile


class UrlLibHttpClient:
    """Minimal HTTP client based on the Python standard library."""

    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.cninfo.com.cn",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }

    def get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(url, data=encoded, headers=self.headers, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def download_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()


class CninfoDataSource:
    """Fetch official announcements and PDFs from CNINFO."""

    source = "cninfo"

    ANNOUNCEMENT_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    STOCK_LIST_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
    STATIC_BASE_URL = "https://static.cninfo.com.cn/"

    CATEGORY_ANNUAL_REPORT = "category_ndbg_szsh;"
    CATEGORY_SEMI_ANNUAL_REPORT = "category_bndbg_szsh;"
    CATEGORY_QUARTERLY_REPORT = "category_yjdbg_szsh;"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()
        self._stock_map: dict[str, dict[str, Any]] | None = None

    def search_securities(self, keyword: str) -> list[dict[str, Any]]:
        """Search securities from CNINFO's public stock mapping file."""

        keyword = keyword.strip()
        if not keyword:
            return []
        stock_map = self._load_stock_map()
        return [
            item
            for item in stock_map.values()
            if keyword in str(item.get("code", "")) or keyword in str(item.get("zwjc", ""))
        ]

    def search_announcements(
        self,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        category: str = "",
        search_key: str = "",
        page_num: int = 1,
        page_size: int = 30,
    ) -> list[DisclosureDocument]:
        """Search official announcements from CNINFO."""

        resolved = self.resolve_security(symbol) if symbol else None
        stock_param = self._stock_param(resolved) if resolved else ""
        date_range = self._date_range(start_date, end_date)
        payload = {
            "pageNum": page_num,
            "pageSize": min(page_size, 30),
            "tabName": "fulltext",
            "column": resolved.get("column", "szse") if resolved else "szse",
            "plate": resolved.get("plate", "") if resolved else "",
            "stock": stock_param,
            "searchkey": search_key,
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": date_range,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        try:
            response = self.http.post_json(self.ANNOUNCEMENT_QUERY_URL, payload)
        except Exception as exc:  # pragma: no cover - network failure
            raise DataSourceError("CNINFO announcement query failed") from exc

        announcements = response.get("announcements") or []
        return [self._document_from_announcement(item, category=category) for item in announcements]

    def resolve_security(self, symbol: str | None) -> dict[str, Any]:
        """Resolve a security to CNINFO code/orgId metadata."""

        if not symbol:
            raise ValueError("symbol must not be empty")
        code = _normalize_symbol(symbol)
        stock_map = self._load_stock_map()
        if code in stock_map:
            return stock_map[code]
        raise DataSourceError(f"CNINFO security not found: {symbol}")

    def download_pdf(
        self,
        document: DisclosureDocument,
        root_dir: str | Path = "data/disclosures",
    ) -> DownloadedFile:
        """Download a disclosure PDF and return file metadata."""

        target_path = self._target_pdf_path(document, Path(root_dir))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = self.http.download_bytes(document.file_url)
        sha256 = hashlib.sha256(content).hexdigest()
        if target_path.exists():
            existing_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
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

    def _load_stock_map(self) -> dict[str, dict[str, Any]]:
        if self._stock_map is not None:
            return self._stock_map
        try:
            data = self.http.get_json(self.STOCK_LIST_URL)
        except Exception as exc:  # pragma: no cover - network failure
            raise DataSourceError("CNINFO stock mapping query failed") from exc
        stock_list = data.get("stockList") or []
        self._stock_map = {str(item.get("code")): dict(item) for item in stock_list if item.get("code")}
        return self._stock_map

    def _document_from_announcement(self, item: dict[str, Any], category: str) -> DisclosureDocument:
        adjunct_url = str(item.get("adjunctUrl", ""))
        file_url = urllib.parse.urljoin(self.STATIC_BASE_URL, adjunct_url)
        document_id = str(item.get("announcementId") or _stable_document_id(file_url))
        return DisclosureDocument(
            document_id=document_id,
            symbol=str(item.get("secCode", "")),
            title=_strip_html(str(item.get("announcementTitle", ""))),
            announcement_date=to_date_string(item.get("announcementTime") or item.get("announcementDate")),
            category=category or item.get("category"),
            source=self.source,
            file_url=file_url,
            page_url=None,
            raw=dict(item),
        )

    def _stock_param(self, resolved: dict[str, Any]) -> str:
        code = str(resolved.get("code", ""))
        org_id = str(resolved.get("orgId", ""))
        if not code or not org_id:
            raise DataSourceError(f"CNINFO stock mapping missing code/orgId: {resolved}")
        return f"{code},{org_id}"

    def _date_range(self, start_date: str | None, end_date: str | None) -> str:
        if not start_date and not end_date:
            return ""
        if not start_date or not end_date:
            raise ValueError("start_date and end_date must be provided together")
        return f"{normalize_date_dash(start_date)}~{normalize_date_dash(end_date)}"

    def _target_pdf_path(self, document: DisclosureDocument, root_dir: Path) -> Path:
        year = document.announcement_date[:4] if document.announcement_date else "unknown"
        filename = _safe_filename(f"{document.document_id}-{document.title}.pdf")
        return root_dir / self.source / document.symbol / year / filename


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip()
    if "." in text:
        parts = text.split(".")
        text = parts[0] if parts[0].isdigit() else parts[-1]
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"Unsupported CNINFO symbol format: {symbol}")
    return text


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", value).strip(" ._")
    return cleaned[:180] or "document.pdf"


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
