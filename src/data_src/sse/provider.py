"""Shanghai Stock Exchange official disclosure data source adapter."""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any

from src.data_src.common import normalize_date_dash, to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument


class SseDisclosureDataSource:
    """Fetch official announcement metadata from Shanghai Stock Exchange."""

    source = "sse"
    QUERY_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
    STATIC_BASE_URL = "https://static.sse.com.cn"

    REPORT_TYPE_ALL = "ALL"
    REPORT_TYPE_ANNUAL = "YEARLY"
    REPORT_TYPE_SEMI_ANNUAL = "QUATER2"
    REPORT_TYPE_QUARTERLY = "QUATER1"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": "https://www.sse.com.cn/"})

    def search_announcements(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        report_type: str = REPORT_TYPE_ALL,
        page_no: int = 1,
        page_size: int = 25,
    ) -> list[DisclosureDocument]:
        """Search SSE announcements by stock code, date range, and report type."""

        code = _normalize_symbol(symbol)
        params = {
            "isPagination": "true",
            "securityType": "0101,120100,020100,020200,120200",
            "productId": code,
            "reportType": report_type,
            "pageHelp.pageSize": page_size,
            "pageHelp.pageCount": 50,
            "pageHelp.pageNo": page_no,
            "pageHelp.beginPage": 1,
            "pageHelp.cacheSize": 1,
            "pageHelp.endPage": 5,
            "_": int(time.time() * 1000),
        }
        if start_date:
            params["beginDate"] = normalize_date_dash(start_date)
        if end_date:
            params["endDate"] = normalize_date_dash(end_date)

        referer = f"https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId={code}"
        original_headers = dict(self.http.headers)
        self.http.headers["Referer"] = referer
        try:
            payload = self.http.get_json(self.QUERY_URL, params)
        finally:
            self.http.headers = original_headers

        rows = payload.get("result") or []
        return [self._document_from_row(row, fallback_symbol=code, report_type=report_type) for row in rows]

    def _document_from_row(
        self,
        row: dict[str, Any],
        fallback_symbol: str,
        report_type: str,
    ) -> DisclosureDocument:
        url_path = str(row.get("URL") or row.get("url") or "")
        file_url = urllib.parse.urljoin(self.STATIC_BASE_URL, url_path)
        title = str(row.get("TITLE") or row.get("title") or "")
        document_id = str(row.get("BULLETIN_ID") or row.get("bulletinId") or _stable_document_id(file_url))
        return DisclosureDocument(
            document_id=document_id,
            symbol=str(row.get("SECURITY_CODE") or row.get("securityCode") or fallback_symbol),
            title=title,
            announcement_date=to_date_string(row.get("ADDDATE") or row.get("addDate")),
            category=report_type,
            source=self.source,
            file_url=file_url,
            page_url=None,
            raw=dict(row),
        )


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip()
    if "." in text:
        parts = text.split(".")
        text = parts[0] if parts[0].isdigit() else parts[-1]
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"Unsupported SSE symbol format: {symbol}")
    return text


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
