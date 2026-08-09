"""Current official macro releases from China National Bureau of Statistics."""

from __future__ import annotations

import html
import re
from typing import Protocol

from ..common import DataSourceError
from ..http_client import UrlLibHttpClient


NBS_DATA_RELEASE_URL = "https://www.stats.gov.cn/sj/zxfb/"


class TextHttpClient(Protocol):
    def get_text(
        self,
        url: str,
        params: dict[str, object] | None = None,
    ) -> str: ...


class NBSMacroDataSource:
    """Parse the latest CPI release from the official release page."""

    source = "nbs"

    def __init__(self, http_client: TextHttpClient | None = None) -> None:
        self.http_client = http_client or UrlLibHttpClient()

    def get_latest_cpi_yoy(self) -> dict[str, object]:
        text = _visible_text(
            self.http_client.get_text(NBS_DATA_RELEASE_URL)
        )
        matches = list(
            re.finditer(
                r"(?P<year>\d{4})年(?P<month>\d{1,2})月份"
                r"居民消费价格同比"
                r"(?P<direction>上涨|下降|持平)"
                r"(?:(?P<value>\d+(?:\.\d+)?)%)?",
                text,
            )
        )
        if not matches:
            raise DataSourceError(
                "NBS release page has no recognizable CPI YoY release"
            )
        observations = []
        for match in matches:
            direction = match.group("direction")
            raw_value = match.group("value")
            if direction != "持平" and raw_value is None:
                continue
            value = 0.0 if direction == "持平" else float(raw_value)
            if direction == "下降":
                value = -value
            nearby = text[match.end() : match.end() + 500]
            publication = re.search(
                r"(?P<date>\d{4}-\d{2}-\d{2})",
                nearby,
            )
            if publication is None:
                continue
            year = int(match.group("year"))
            month = int(match.group("month"))
            observations.append(
                {
                    "reference_period": f"{year:04d}-{month:02d}",
                    "available_at": publication.group("date"),
                    "cpi_yoy_percent": value,
                    "source": f"{self.source}:data_release_page",
                    "source_url": NBS_DATA_RELEASE_URL,
                    "historical_backtest_eligible": False,
                    "limitation": (
                        "Latest official release for current description "
                        "only; this page is not a point-in-time archive."
                    ),
                }
            )
        if not observations:
            raise DataSourceError(
                "NBS CPI releases have no publication date or value"
            )
        return max(
            observations,
            key=lambda item: str(item["available_at"]),
        )


def _visible_text(document: str) -> str:
    without_scripts = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()
