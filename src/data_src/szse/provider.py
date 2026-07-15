"""Shenzhen Stock Exchange official disclosure data source adapter."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from src.data_src.common import normalize_date_dash, to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument


class SzseDisclosureDataSource:
    """Fetch official announcement metadata from Shenzhen Stock Exchange."""

    source = "szse"
    QUERY_URL = "https://www.szse.cn/api/disc/announcement/annList"
    STATIC_BASE_URL = "https://disc.static.szse.cn/download"

    CHANNEL_LISTED_NOTICE = "listedNotice_disc"
    CHANNEL_FIXED_DISCLOSURE = "fixed_disc"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/json",
                "Origin": "https://www.szse.cn",
                "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html",
                "X-Request-Type": "ajax",
                "X-Requested-With": "XMLHttpRequest",
            },
            retry_count=3,
        )

    def search_announcements(
        self,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        channel_code: str = CHANNEL_LISTED_NOTICE,
        search_key: str = "",
        page_no: int = 1,
        page_size: int = 30,
    ) -> list[DisclosureDocument]:
        """Search SZSE announcements by stock code, date range, and channel."""

        payload: dict[str, Any] = {
            "channelCode": [channel_code],
            "pageNum": page_no,
            "pageSize": page_size,
            "seDate": [_normalize_date_or_empty(start_date), _normalize_date_or_empty(end_date)],
        }
        if symbol:
            payload["stock"] = [_normalize_symbol(symbol)]
        if search_key:
            payload["searchKey"] = [search_key]

        response = self.http.post_json(f"{self.QUERY_URL}?random={time.time()}", payload)
        rows = response.get("data") or []
        return [self._document_from_row(row, channel_code) for row in rows]

    def _document_from_row(self, row: dict[str, Any], channel_code: str) -> DisclosureDocument:
        attach_path = str(row.get("attachPath") or "")
        file_url = f"{self.STATIC_BASE_URL}{attach_path}"
        document_id = str(row.get("id") or _stable_document_id(file_url))
        sec_code = _first_value(row.get("secCode"))
        return DisclosureDocument(
            document_id=document_id,
            symbol=sec_code,
            title=str(row.get("title") or ""),
            announcement_date=to_date_string(row.get("publishTime")),
            category=str(row.get("bigCategoryId") or row.get("category") or channel_code),
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
        raise ValueError(f"Unsupported SZSE symbol format: {symbol}")
    return text


def _normalize_date_or_empty(value: str | None) -> str:
    return normalize_date_dash(value) if value else ""


def _first_value(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
