"""Federal Register API: US rules, proposed rules and notices (export controls, sanctions, tariffs).

Design notes: docs/modules/2-世界状态/事件情报与观测模块.md「来源」.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import NewsItem

DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
_FIELDS = ("title", "type", "abstract", "publication_date", "html_url", "agencies", "document_number", "action")


class FederalRegisterDataSource:
    source = "federal_register"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()

    def documents(
        self,
        query: str | None = None,
        *,
        agency: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        """``agency`` is a Federal Register slug, e.g. industry-and-security-bureau (BIS)."""

        params: list[tuple[str, str]] = [("order", "newest"), ("per_page", str(min(max(limit, 1), 1000)))]
        params.extend(("fields[]", item) for item in _FIELDS)
        if query:
            params.append(("conditions[term]", query))
        if agency:
            params.append(("conditions[agencies][]", agency))
        if since:
            params.append(("conditions[publication_date][gte]", since[:10]))
        if until:
            params.append(("conditions[publication_date][lte]", until[:10]))
        return parse_documents(self.http.get_json(f"{DOCUMENTS_URL}?{urlencode(params)}"))


def parse_documents(payload: dict[str, Any]) -> list[NewsItem]:
    items = []
    for row in payload.get("results") or ():
        url, day, title = row.get("html_url"), str(row.get("publication_date") or "")[:10], row.get("title")
        if not url or not day or not title:
            continue
        agencies = "、".join(item.get("name") or item.get("raw_name") or "" for item in row.get("agencies") or ())
        items.append(
            NewsItem(
                source="federal_register",
                kind="rule",
                title=f"[{row.get('type') or ''}] {title}".strip(),
                url=str(url),
                published_on=day,
                publisher=agencies or "Federal Register",
                summary=row.get("abstract"),
                language="en",
                raw={"document_number": row.get("document_number"), "action": row.get("action")},
            )
        )
    return items
