"""China Foreign Exchange Trade System (chinamoney.com.cn) public rate history."""

from __future__ import annotations

import urllib.parse
from datetime import date, timedelta
from typing import Any

from ..common import DataSourceError
from ..http_client import UrlLibHttpClient

BASE_URL = "https://www.chinamoney.com.cn"
_LPR_PATH = "/ags/ms/cm-u-bk-currency/LprHis"
_SHIBOR_PATH = "/ags/ms/cm-u-bk-shibor/ShiborHis"
# The history endpoints reject ranges longer than one year.
_MAX_WINDOW_DAYS = 360


class ChinaMoneyDataSource:
    """Official LPR and Shibor fixings, one record per publication date."""

    source = "chinamoney"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.chinamoney.com.cn/",
            },
        )

    def get_lpr_history(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._history(
            _LPR_PATH,
            start_date,
            end_date,
            start_key="strStartDate",
            end_key="strEndDate",
        )

    def get_shibor_history(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        return self._history(
            _SHIBOR_PATH,
            start_date,
            end_date,
            start_key="startDate",
            end_key="endDate",
        )

    def _history(
        self,
        path: str,
        start_date: str,
        end_date: str,
        *,
        start_key: str,
        end_key: str,
    ) -> list[dict[str, Any]]:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        records: dict[str, dict[str, Any]] = {}
        window_start = start
        while window_start <= end:
            window_end = min(end, window_start + timedelta(days=_MAX_WINDOW_DAYS))
            query = urllib.parse.urlencode(
                {
                    "lang": "CN",
                    start_key: window_start.isoformat(),
                    end_key: window_end.isoformat(),
                }
            )
            payload = self.http.post_json(f"{BASE_URL}{path}?{query}", {})
            if not isinstance(payload, dict) or "records" not in payload:
                raise DataSourceError(f"chinamoney {path} returned no records field")
            for record in payload.get("records") or []:
                day = str(record.get("showDateCN") or "")[:10]
                if day:
                    records[day] = dict(record)
            window_start = window_end + timedelta(days=1)
        return [records[day] for day in sorted(records)]
