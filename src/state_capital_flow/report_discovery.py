"""Discover and download official CNINFO and SSE fund periodic reports."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
from urllib.parse import urlparse

import requests

from src.data_src.cninfo.provider import UrlLibHttpClient
from src.data_src.common import to_date_string
from src.data_src.models import DownloadedFile


FundReportType = Literal["annual", "semi_annual"]


@dataclass(frozen=True)
class OfficialFundReport:
    document_id: str
    symbol: str
    title: str
    report_type: FundReportType
    report_period: str
    published_at: str
    pdf_url: str
    source: Literal["cninfo:fund_report", "sse:fund_report"] = (
        "cninfo:fund_report"
    )

    def __post_init__(self) -> None:
        for value, name in (
            (self.document_id, "document_id"),
            (self.symbol, "symbol"),
            (self.title, "title"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if not self.symbol.isdigit() or len(self.symbol) != 6:
            raise ValueError("symbol must be an exact six-digit secCode")
        if self.report_type not in {"annual", "semi_annual"}:
            raise ValueError("report_type must be annual or semi_annual")
        report_period = _parse_iso_date(self.report_period, "report_period")
        published_at = _parse_iso_date(self.published_at, "published_at")
        if published_at < report_period:
            raise ValueError("published_at must not precede report_period")
        _require_url(self.pdf_url, "pdf_url")
        if self.source not in {"cninfo:fund_report", "sse:fund_report"}:
            raise ValueError(
                "source must be cninfo:fund_report or sse:fund_report"
            )


class CninfoFundReportDiscovery:
    """Paginate CNINFO's official annual and semi-annual fund reports."""

    source = "cninfo:fund_report"
    QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    PDF_BASE_URL = "https://static.cninfo.com.cn/"
    CATEGORY = "category_ndbg_jjgg;category_bndbg_jjgg;"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient()

    def discover(
        self,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
        *,
        page_size: int = 30,
        max_pages: int = 100,
        max_reports: int = 100,
    ) -> list[OfficialFundReport]:
        symbol_set = _validate_symbols(symbols)
        start = _parse_iso_date(start_date, "start_date")
        end = _parse_iso_date(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must not be earlier than start_date")
        _require_positive_int(page_size, "page_size")
        _require_positive_int(max_pages, "max_pages")
        _require_positive_int(max_reports, "max_reports")
        if not symbol_set:
            return []
        effective_page_size = min(page_size, 30)

        reports_by_id: dict[str, OfficialFundReport] = {}
        for page_number in range(1, max_pages + 1):
            payload = {
                "column": "fund",
                "tabName": "fulltext",
                "category": self.CATEGORY,
                "seDate": f"{start.isoformat()}~{end.isoformat()}",
                "pageNum": page_number,
                "pageSize": effective_page_size,
                "plate": "",
                "stock": "",
                "searchkey": "",
                "secid": "",
                "trade": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            response = self.http.post_json(self.QUERY_URL, payload)
            announcements = response.get("announcements") or []
            if not isinstance(announcements, list):
                raise ValueError("CNINFO announcements must be a list")
            for announcement in announcements:
                report = _report_from_announcement(announcement, symbol_set)
                if report is None or report.document_id in reports_by_id:
                    continue
                reports_by_id[report.document_id] = report
                if len(reports_by_id) >= max_reports:
                    break
            if len(reports_by_id) >= max_reports:
                break

            total = _response_total(response)
            if (
                total is not None
                and page_number * effective_page_size >= total
            ):
                break
            if total is None and len(announcements) < effective_page_size:
                break

        reports = sorted(
            reports_by_id.values(),
            key=lambda item: (
                item.published_at,
                item.symbol,
                item.report_type,
                item.document_id,
            ),
        )
        return reports[:max_reports]

    def download(
        self,
        report: OfficialFundReport,
        root_dir: str | Path,
    ) -> DownloadedFile:
        if report.source != self.source:
            raise ValueError("CNINFO discovery can only download CNINFO reports")
        return _download_report(self.http, report, root_dir)


class SseFundHttpClient:
    """Requests client for the SSE ETF disclosure endpoint."""

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://etf.sse.com.cn/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.get(
            url,
            params=params,
            headers=self.headers,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SSE response must be a JSON object")
        return payload

    def download_bytes(self, url: str) -> bytes:
        response = requests.get(
            url,
            headers=self.headers,
            timeout=60,
        )
        response.raise_for_status()
        return response.content


class SseFundReportDiscovery:
    """Discover official SSE ETF annual and semi-annual reports."""

    source = "sse:fund_report"
    QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
    PDF_BASE_URL = "https://www.sse.com.cn/"
    SQL_ID = "COMMON_PL_JJXX_JJGG_NEW_L"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or SseFundHttpClient()

    def discover(
        self,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
        *,
        page_size: int = 30,
        max_pages: int = 100,
        max_reports: int = 100,
    ) -> list[OfficialFundReport]:
        _validate_symbols(symbols)
        symbol_order = list(dict.fromkeys(symbols))
        start = _parse_iso_date(start_date, "start_date")
        end = _parse_iso_date(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must not be earlier than start_date")
        _require_positive_int(page_size, "page_size")
        _require_positive_int(max_pages, "max_pages")
        _require_positive_int(max_reports, "max_reports")
        if not symbol_order:
            return []
        effective_page_size = min(page_size, 30)

        reports_by_id: dict[str, OfficialFundReport] = {}
        pages_used = 0
        for symbol in symbol_order:
            page_number = 1
            while pages_used < max_pages and len(reports_by_id) < max_reports:
                params = _sse_query_parameters(
                    symbol,
                    start,
                    end,
                    effective_page_size,
                    page_number,
                )
                response = self.http.get_json(self.QUERY_URL, params)
                pages_used += 1
                rows, page_count = _sse_page(response)
                for row in rows:
                    report = _report_from_sse_row(
                        row,
                        symbol,
                        start,
                        end,
                    )
                    if report is None or report.document_id in reports_by_id:
                        continue
                    reports_by_id[report.document_id] = report
                    if len(reports_by_id) >= max_reports:
                        break
                if page_count is not None:
                    if page_number >= page_count:
                        break
                elif len(rows) < effective_page_size:
                    break
                page_number += 1
            if pages_used >= max_pages or len(reports_by_id) >= max_reports:
                break
        return _sorted_reports(reports_by_id.values())[:max_reports]

    def download(
        self,
        report: OfficialFundReport,
        root_dir: str | Path,
    ) -> DownloadedFile:
        if report.source != self.source:
            raise ValueError("SSE discovery can only download SSE reports")
        return _download_report(self.http, report, root_dir)


class CompositeFundReportDiscovery:
    """Combine bounded official discovery sources.

    ``max_reports`` is reduced after each source. Individual discoveries do
    not expose consumed-page counts, so ``max_pages`` is a finite per-source
    upper bound rather than an aggregate count across sources.
    """

    def __init__(self, discoveries: Sequence[Any]) -> None:
        if isinstance(discoveries, (str, bytes)):
            raise TypeError("discoveries must be a sequence")
        self.discoveries = tuple(discoveries)
        if not self.discoveries:
            raise ValueError("discoveries must not be empty")
        self._by_source: dict[str, Any] = {}
        for discovery in self.discoveries:
            source = getattr(discovery, "source", None)
            if not isinstance(source, str) or not source:
                raise ValueError("each discovery must declare a source")
            if source in self._by_source:
                raise ValueError(f"duplicate discovery source: {source}")
            self._by_source[source] = discovery

    def discover(
        self,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
        *,
        page_size: int = 30,
        max_pages: int = 100,
        max_reports: int = 100,
    ) -> list[OfficialFundReport]:
        _require_positive_int(page_size, "page_size")
        _require_positive_int(max_pages, "max_pages")
        _require_positive_int(max_reports, "max_reports")
        reports_by_key: dict[
            tuple[str, str, str],
            OfficialFundReport,
        ] = {}
        for discovery in self.discoveries:
            remaining_reports = max_reports - len(reports_by_key)
            if remaining_reports <= 0:
                break
            reports = discovery.discover(
                symbols,
                start_date,
                end_date,
                page_size=page_size,
                max_pages=max_pages,
                max_reports=remaining_reports,
            )
            for report in reports:
                key = (
                    report.symbol,
                    report.report_type,
                    report.report_period,
                )
                reports_by_key.setdefault(key, report)
                if len(reports_by_key) >= max_reports:
                    break
        return _sorted_reports(reports_by_key.values())[:max_reports]

    def download(
        self,
        report: OfficialFundReport,
        root_dir: str | Path,
    ) -> DownloadedFile:
        if not isinstance(report, OfficialFundReport):
            raise TypeError("report must be OfficialFundReport")
        discovery = self._by_source.get(report.source)
        if discovery is None:
            raise ValueError(f"no discovery registered for source: {report.source}")
        return discovery.download(report, root_dir)


def _sse_query_parameters(
    symbol: str,
    start_date: date,
    end_date: date,
    page_size: int,
    page_number: int,
) -> dict[str, Any]:
    return {
        "isPagination": "true",
        "sqlId": SseFundReportDiscovery.SQL_ID,
        "type": "inParams",
        "SECURITY_CODE": symbol,
        "BULLETIN_TYPE": "",
        "TITLE": "",
        "OTHER_TYPE": "",
        "START_DATE": start_date.isoformat(),
        "END_DATE": end_date.isoformat(),
        "DATE_DESC": "1",
        "pageHelp.pageSize": page_size,
        "pageHelp.pageNo": page_number,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 5,
    }


def _sse_page(response: dict[str, Any]) -> tuple[list[Any], int | None]:
    page_help = response.get("pageHelp")
    rows: Any = None
    page_count: int | None = None
    if isinstance(page_help, dict):
        rows = page_help.get("data")
        raw_page_count = page_help.get("pageCount")
        if raw_page_count is not None and not isinstance(raw_page_count, bool):
            try:
                parsed_page_count = int(raw_page_count)
            except (TypeError, ValueError):
                parsed_page_count = 0
            if parsed_page_count >= 0:
                page_count = parsed_page_count
    if rows is None:
        rows = response.get("result")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("SSE pageHelp.data or result must be a list")
    return rows, page_count


def _report_from_sse_row(
    row: Any,
    fallback_symbol: str,
    start_date: date,
    end_date: date,
) -> OfficialFundReport | None:
    if not isinstance(row, dict):
        return None
    symbol = str(
        row.get("SECURITY_CODE")
        or row.get("securityCode")
        or fallback_symbol
    ).strip()
    if symbol != fallback_symbol:
        return None
    title = re.sub(
        r"<[^>]+>",
        "",
        str(row.get("TITLE") or row.get("title") or ""),
    ).strip()
    if not title or "摘要" in title:
        return None
    report_identity = _report_identity(title)
    if report_identity is None:
        return None
    report_type, report_period = report_identity
    url_path = str(
        row.get("URL")
        or row.get("url")
        or row.get("BULLETIN_URL")
        or ""
    ).strip()
    published_at = to_date_string(
        row.get("SSEDATE")
        or row.get("ADDDATE")
        or row.get("PUBLISH_DATE")
        or row.get("BULLETIN_DATE")
        or row.get("DATE")
        or row.get("addDate")
    )
    if not published_at:
        match = re.search(r"/(\d{4}-\d{2}-\d{2})/", url_path)
        published_at = match.group(1) if match is not None else ""
    if not url_path or not published_at:
        return None
    try:
        published_date = _parse_iso_date(published_at, "published_at")
    except (TypeError, ValueError):
        return None
    if published_date < start_date or published_date > end_date:
        return None
    pdf_url = urllib.parse.urljoin(
        SseFundReportDiscovery.PDF_BASE_URL,
        url_path,
    )
    document_id = _sse_document_id(
        symbol,
        published_at,
        url_path,
    )
    try:
        return OfficialFundReport(
            document_id=document_id,
            symbol=symbol,
            title=title,
            report_type=report_type,
            report_period=report_period,
            published_at=published_at,
            pdf_url=pdf_url,
            source="sse:fund_report",
        )
    except (TypeError, ValueError):
        return None


def _sse_document_id(
    symbol: str,
    published_at: str,
    url_path: str,
) -> str:
    file_name = Path(urllib.parse.urlparse(url_path).path).name
    stem = Path(file_name).stem.strip()
    if stem:
        return stem
    payload = f"{symbol}\x1f{published_at}\x1f{url_path}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sse-fund:{digest}"


def _sorted_reports(
    reports: Iterable[OfficialFundReport],
) -> list[OfficialFundReport]:
    return sorted(
        reports,
        key=lambda item: (
            item.published_at,
            item.symbol,
            item.report_type,
            item.document_id,
        ),
    )


def _download_report(
    http_client: Any,
    report: OfficialFundReport,
    root_dir: str | Path,
) -> DownloadedFile:
    if not isinstance(report, OfficialFundReport):
        raise TypeError("report must be OfficialFundReport")
    root = Path(root_dir)
    file_name = (
        f"{report.published_at}__"
        f"{_safe_file_component(report.document_id)}.pdf"
    )
    target = root / report.symbol / file_name
    target.parent.mkdir(parents=True, exist_ok=True)

    content = http_client.download_bytes(report.pdf_url)
    if not isinstance(content, bytes):
        raise TypeError("download_bytes must return bytes")
    sha256 = hashlib.sha256(content).hexdigest()
    if target.exists():
        existing_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing_hash == sha256:
            return _downloaded_file(report, target, sha256)
    target.write_bytes(content)
    return _downloaded_file(report, target, sha256)


def _report_from_announcement(
    announcement: Any,
    symbols: set[str],
) -> OfficialFundReport | None:
    if not isinstance(announcement, dict):
        return None
    symbol = str(announcement.get("secCode") or "")
    if symbol not in symbols:
        return None
    title = re.sub(
        r"<[^>]+>",
        "",
        str(announcement.get("announcementTitle") or ""),
    ).strip()
    if not title or "摘要" in title:
        return None
    report_identity = _report_identity(title)
    if report_identity is None:
        return None
    report_type, report_period = report_identity
    document_id = str(announcement.get("announcementId") or "").strip()
    adjunct_url = str(announcement.get("adjunctUrl") or "").strip()
    published_at = to_date_string(
        announcement.get("announcementTime")
        or announcement.get("announcementDate")
    )
    if not document_id or not adjunct_url or not published_at:
        return None
    try:
        return OfficialFundReport(
            document_id=document_id,
            symbol=symbol,
            title=title,
            report_type=report_type,
            report_period=report_period,
            published_at=published_at,
            pdf_url=urllib.parse.urljoin(
                CninfoFundReportDiscovery.PDF_BASE_URL,
                adjunct_url,
            ),
        )
    except (TypeError, ValueError):
        return None


def _report_identity(title: str) -> tuple[FundReportType, str] | None:
    annual = re.search(r"(?P<year>\d{4})年年度报告", title)
    if annual is not None:
        return "annual", f"{annual.group('year')}-12-31"
    semi_annual = re.search(
        r"(?P<year>\d{4})年(?:半年度|中期)报告",
        title,
    )
    if semi_annual is not None:
        return "semi_annual", f"{semi_annual.group('year')}-06-30"
    return None


def _response_total(response: dict[str, Any]) -> int | None:
    totals: list[int] = []
    for field_name in ("totalAnnouncement", "totalRecordNum"):
        value = response.get(field_name)
        if value is None or isinstance(value, bool):
            continue
        try:
            total = int(value)
        except (TypeError, ValueError):
            continue
        if total >= 0:
            totals.append(total)
    return max(totals) if totals else None


def _validate_symbols(symbols: Sequence[str]) -> set[str]:
    if isinstance(symbols, (str, bytes)):
        raise TypeError("symbols must be a sequence of secCode strings")
    result: set[str] = set()
    for symbol in symbols:
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isdigit()
        ):
            raise ValueError("symbols must contain exact six-digit secCode strings")
        result.add(symbol)
    return result


def _downloaded_file(
    report: OfficialFundReport,
    target: Path,
    sha256: str,
) -> DownloadedFile:
    return DownloadedFile(
        document_id=report.document_id,
        file_path=str(target),
        file_url=report.pdf_url,
        sha256=sha256,
        size_bytes=target.stat().st_size,
        source=report.source,
    )


def _safe_file_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    if not cleaned:
        raise ValueError("document_id cannot form a file name")
    return cleaned


def _parse_iso_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc
    if value != parsed.isoformat():
        raise ValueError(f"{field_name} must use YYYY-MM-DD")
    return parsed


def _require_url(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


def _require_positive_int(
    value: int,
    field_name: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be int")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
