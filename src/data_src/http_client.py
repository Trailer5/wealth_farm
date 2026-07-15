"""Small HTTP helpers shared by lightweight provider skeletons."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any


class UrlLibHttpClient:
    """Minimal HTTP client based on the Python standard library."""

    default_headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0",
    }

    def __init__(self, headers: dict[str, str] | None = None, retry_count: int = 2) -> None:
        self.headers = dict(self.default_headers)
        if headers:
            self.headers.update(headers)
        self.retry_count = retry_count

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        full_url = _with_query(url, params)
        return json.loads(self._request_bytes(full_url).decode("utf-8"))

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        full_url = _with_query(url, params)
        content = self._request_bytes(full_url)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gbk", errors="ignore")

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        return json.loads(self._request_bytes(url, data=data).decode("utf-8"))

    def download_bytes(self, url: str) -> bytes:
        return self._request_bytes(url)

    def _request_bytes(self, url: str, data: dict[str, Any] | None = None) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                return self._request_bytes_once(url, data=data)
            except Exception as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    break
                time.sleep(0.5 * (attempt + 1))
        raise last_error if last_error else RuntimeError("HTTP request failed")

    def _request_bytes_once(self, url: str, data: dict[str, Any] | None = None) -> bytes:
        try:
            import requests  # type: ignore

            if data is None:
                response = requests.get(url, headers=self.headers, timeout=20)
            else:
                response = requests.post(url, headers=self.headers, json=data, timeout=20)
            response.raise_for_status()
            return response.content
        except ImportError:
            body = json.dumps(data).encode("utf-8") if data is not None else None
            method = "POST" if data is not None else "GET"
            request = urllib.request.Request(url, data=body, headers=self.headers, method=method)
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()


def _with_query(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode(params)}"
