"""Fund company official website disclosure page adapter."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any

from src.data_src.common import to_date_string
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument


class FundCompanyDisclosureDataSource:
    """Parse disclosure links from fund company official website pages.

    Fund company websites do not share one public API. This adapter accepts
    configured public pages and extracts explicit announcement/PDF links only.
    """

    source = "fund_company"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()

    def search_announcements(
        self,
        page_urls: list[str],
        fund_code: str | None = None,
        keyword: str = "",
        category: str = "fund_company",
    ) -> list[DisclosureDocument]:
        """Parse fund company pages and return disclosure metadata."""

        documents: list[DisclosureDocument] = []
        for page_url in page_urls:
            html = self.http.get_text(page_url)
            documents.extend(self._documents_from_html(html, page_url, fund_code, keyword, category))
        return documents

    def _documents_from_html(
        self,
        html: str,
        page_url: str,
        fund_code: str | None,
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
            surrounding = html[max(0, match.start() - 160) : min(len(html), match.end() + 160)]
            symbol = _find_fund_code(surrounding)
            if fund_code and not _matches_fund_code(fund_code, href, title, symbol):
                continue
            file_url = urllib.parse.urljoin(page_url, href)
            documents.append(
                DisclosureDocument(
                    document_id=_stable_document_id(file_url),
                    symbol=symbol,
                    title=title,
                    announcement_date=_find_date(surrounding),
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


def _find_fund_code(value: str) -> str:
    match = re.search(r"\b\d{6}\b", value)
    return match.group(0) if match else ""


def _matches_fund_code(fund_code: str, href: str, title: str, symbol: str) -> bool:
    if fund_code in href or fund_code in title:
        return True
    direct_codes = set(re.findall(r"\b\d{6}\b", f"{href} {title}"))
    if direct_codes and fund_code not in direct_codes:
        return False
    return fund_code == symbol


def _find_date(value: str) -> str:
    match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value)
    if not match:
        return ""
    return to_date_string(match.group(0).replace("/", "-"))


def _stable_document_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
