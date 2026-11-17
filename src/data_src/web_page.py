"""Fetch one web page as text-bearing HTML, identifying as SEC requires on sec.gov hosts.

Google News article links are redirect pages without the story; they are resolved to the
publisher's URL first.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from src.data_src.common import DataSourceError
from src.data_src.google_news.provider import article_id, resolve_article_url
from src.data_src.sec_edgar import SEC_USER_AGENT

_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_BYTES = 8_000_000


def fetch_web_page(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    import requests

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Only http(s) URLs can be fetched: {url}")
    target, resolve_error = url, None
    if article_id(url) is not None:
        try:
            target = resolve_article_url(url, timeout=timeout)
        except DataSourceError as exc:
            resolve_error = str(exc)
    host = (urlsplit(target).hostname or "").lower()
    agent = SEC_USER_AGENT if host == "sec.gov" or host.endswith(".sec.gov") else _BROWSER
    headers = {
        "User-Agent": agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    response = requests.get(target, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        raise DataSourceError(f"HTTP {response.status_code} for {target}")
    content = response.content[:MAX_BYTES]
    return {
        "url": url,
        "final_url": response.url,
        "status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "html": content.decode(response.encoding or "utf-8", errors="replace"),
        "truncated": len(response.content) > MAX_BYTES,
        "resolve_error": resolve_error,
    }
