"""Google News RSS search: global headlines in English or Chinese, filtered by publication date.

Links point at news.google.com redirect pages; the publisher and publication time come from the
feed. ``resolve_article_url`` turns such a link into the publisher's URL (the redirect page is
script-only). Design notes: docs/modules/2-世界状态/事件情报与观测模块.md「来源」.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from src.data_src.common import DataSourceError
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import NewsItem

SEARCH_URL = "https://news.google.com/rss/search"
ARTICLE_HOST = "news.google.com"
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
EDITIONS = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
}
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


class GoogleNewsDataSource:
    source = "google_news"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers=_HEADERS)

    def search(
        self,
        query: str,
        *,
        since: str | None = None,
        until: str | None = None,
        language: str = "en",
    ) -> list[NewsItem]:
        edition = EDITIONS.get(language)
        if edition is None:
            raise ValueError(f"language must be one of {sorted(EDITIONS)}")
        text = self.http.get_text(SEARCH_URL, {"q": search_query(query, since, until), **edition})
        return parse_rss(text, language=language, since=since, until=until)


def search_query(query: str, since: str | None, until: str | None) -> str:
    """Google's after:/before: operators; before: is exclusive, so it gets the day after ``until``."""

    parts = [query.strip()]
    if since:
        parts.append(f"after:{date.fromisoformat(since[:10]).isoformat()}")
    if until:
        parts.append(f"before:{(date.fromisoformat(until[:10]) + timedelta(days=1)).isoformat()}")
    return " ".join(parts)


def parse_rss(
    text: str,
    *,
    language: str,
    since: str | None = None,
    until: str | None = None,
) -> list[NewsItem]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DataSourceError(f"Unexpected Google News payload: {exc}") from exc
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = node.findtext("pubDate")
        if not title or not link or not published:
            continue
        try:
            moment = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            continue
        day = moment.date().isoformat()
        # The feed's own date filter is approximate; enforce the window on the publication date.
        if (since and day < since[:10]) or (until and day > until[:10]):
            continue
        source_node = node.find("source")
        publisher = (source_node.text or "").strip() if source_node is not None else None
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].rstrip()
        items.append(
            NewsItem(
                source="google_news",
                kind="news",
                title=title,
                url=link,
                published_on=day,
                published_at=moment.isoformat(),
                publisher=publisher or None,
                language=language,
                raw={"publisher_url": source_node.get("url") if source_node is not None else None},
            )
        )
    return items


def article_id(url: str) -> str | None:
    parts = urlsplit(url)
    segments = parts.path.rstrip("/").split("/")
    if (parts.hostname or "").lower() != ARTICLE_HOST or "articles" not in segments[:-1]:
        return None
    return segments[-1] or None


def resolve_article_url(url: str, *, session: Any = None, timeout: float = 30.0) -> str:
    """Publisher URL behind a Google News article link.

    The article page carries a signature and timestamp; Google's own batchexecute call
    (``garturlreq``) exchanges them for the original link, as the page's script does.
    """

    if session is None:
        import requests as session
    item_id = article_id(url)
    if item_id is None:
        raise ValueError(f"Not a Google News article link: {url}")
    page = session.get(f"https://{ARTICLE_HOST}/articles/{item_id}", headers=_HEADERS, timeout=timeout)
    signature = re.search(r'data-n-a-sg="([^"]+)"', page.text)
    timestamp = re.search(r'data-n-a-ts="([^"]+)"', page.text)
    if signature is None or timestamp is None:
        raise DataSourceError(f"Google News article page has no redirect signature: {url}")
    request = [
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        item_id,
        int(timestamp.group(1)),
        signature.group(1),
    ]
    payload = [[["Fbv4je", json.dumps(request, separators=(",", ":")), None, "generic"]]]
    response = session.post(
        _BATCH_URL,
        headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        data={"f.req": json.dumps(payload, separators=(",", ":"))},
        timeout=timeout,
    )
    try:
        envelope = json.loads(response.text.split("\n\n", 1)[1])
        resolved = json.loads(envelope[0][2])[1]
    except (IndexError, TypeError, ValueError) as exc:
        raise DataSourceError(f"Unexpected Google News redirect payload for {url}") from exc
    if not isinstance(resolved, str) or not resolved.startswith(("http://", "https://")):
        raise DataSourceError(f"Google News redirect gave no link for {url}")
    return resolved
