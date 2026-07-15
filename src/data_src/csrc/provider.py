"""CSRC public information page adapter."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any

from src.data_src.common import to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument


class CsrcPublicInfoDataSource:
    """Fetch CSRC public information metadata from configured public pages.

    No stable public JSON API has been confirmed. This adapter only parses
    explicit links from public CSRC pages and keeps the scope conservative.
    """

    source = "csrc_public"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"Referer": "https://www.csrc.gov.cn/"})

    def search_public_documents(
        self,
        page_urls: list[str],
        keyword: str = "",
        category: str = "csrc_public",
    ) -> list[DisclosureDocument]:
        """Parse public CSRC pages and return linked document metadata."""

        documents: list[DisclosureDocument] = []
        for page_url in page_urls:
            html = self.http.get_text(page_url)
            documents.extend(self._documents_from_html(html, page_url, keyword, category))
        return documents

    def _documents_from_html(
        self,
        html: str,
        page_url: str,
        keyword: str,
        category: str,
    ) -> list[DisclosureDocument]:
        documents: list[DisclosureDocument] = []
        for match in re.finditer(r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', html, re.I | re.S):
            href = match.group("href").strip()
            title = _strip_html(match.group("title"))
            if not href or not title or href.startswith("javascript:"):
                continue
            if keyword and keyword not in title:
                continue
            file_url = urllib.parse.urljoin(page_url, href)
            surrounding = html[max(0, match.start() - 120) : min(len(html), match.end() + 120)]
            announcement_date = _find_date(surrounding)
            documents.append(
                DisclosureDocument(
                    document_id=_stable_document_id(file_url),
                    symbol="CSRC",
                    title=title,
                    announcement_date=announcement_date,
                    category=category,
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


def _find_date(value: str) -> str:
    match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value)
    if not match:
        return ""
    return to_date_string(match.group(0).replace("/", "-"))


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
