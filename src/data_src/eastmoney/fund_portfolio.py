"""Quarterly asset allocation and top-ten stock holdings of a fund from Eastmoney F10.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「组合诊断」.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from src.data_src.common import DataSourceError, normalize_a_share_code
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import (
    FundAssetAllocationPeriod,
    FundIndustryAllocationItem,
    FundPortfolioDisclosure,
    FundTopHolding,
)

_HOLDINGS_URL = (
    "https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=10&year={year}&month="
)
_ALLOCATION_URL = "https://fundf10.eastmoney.com/zcpz_{code}.html"
_INDUSTRY_URL = "https://api.fund.eastmoney.com/f10/HYPZ/?fundCode={code}&year={year}"
# CSRC industry codes are section letters; the Hong Kong part uses numeric GICS-like sector codes.
_CSRC_CODE = re.compile(r"[A-S]\d*")
# Eastmoney quote prefixes: 1 Shanghai, 0 Shenzhen/Beijing, 116 Hong Kong, 105-107 US.
_QUOTE_MARKETS = {"1": "sh", "0": "sz", "116": "hk", "105": "us", "106": "us", "107": "us"}
_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class EastmoneyFundPortfolioDataSource:
    source = "eastmoney:fundf10"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
            retry_count=2,
        )

    def get_portfolio(self, symbol: str) -> FundPortfolioDisclosure:
        """Latest two years of quarterly top-ten holdings plus the allocation history."""

        code = normalize_a_share_code(symbol)
        allocation_url = _ALLOCATION_URL.format(code=code)
        latest_url = _HOLDINGS_URL.format(code=code, year="")
        latest = self.http.get_text(latest_url)
        urls = [allocation_url, latest_url]
        pages = [latest]
        current_year = re.search(r"curyear:(\d{4})", latest)
        years = [date.today().year]
        if current_year:
            years = [int(current_year.group(1))]
            previous_url = _HOLDINGS_URL.format(code=code, year=int(current_year.group(1)) - 1)
            urls.append(previous_url)
            pages.append(self.http.get_text(previous_url))
        years.append(years[0] - 1)
        industry_pages = []
        for year in years:
            url = _INDUSTRY_URL.format(code=code, year=year)
            urls.append(url)
            industry_pages.append(self.http.get_text(url))
        allocation_html = self.http.get_text(allocation_url)
        return parse_portfolio(
            code,
            holdings_pages=pages,
            allocation_html=allocation_html,
            industry_pages=industry_pages,
            fetched_on=date.today().isoformat(),
            source=self.source,
            source_urls=tuple(urls),
        )


def parse_portfolio(
    code: str,
    *,
    holdings_pages: list[str],
    allocation_html: str,
    fetched_on: str,
    source: str,
    source_urls: tuple[str, ...],
    industry_pages: list[str] = (),
) -> FundPortfolioDisclosure:
    issues: list[str] = []
    holdings: dict[tuple[str, int], FundTopHolding] = {}
    for page in holdings_pages:
        for item in _parse_holdings(page, issues):
            holdings.setdefault((item.period_end, item.rank), item)
    allocations = _parse_allocations(allocation_html, issues)
    industries: dict[tuple[str, str, str], FundIndustryAllocationItem] = {}
    for page in industry_pages:
        for item in _parse_industries(page, issues):
            industries.setdefault((item.period_end, item.scheme, item.code), item)
    if not allocations and not holdings:
        raise DataSourceError(f"Eastmoney portfolio pages for {code} have neither allocation nor holdings")
    return FundPortfolioDisclosure(
        symbol=code,
        fetched_on=fetched_on,
        allocations=tuple(sorted(allocations, key=lambda item: item.period_end, reverse=True)),
        holdings=tuple(
            sorted(holdings.values(), key=lambda item: (item.period_end, -item.rank), reverse=True)
        ),
        source=source,
        source_urls=source_urls,
        parse_issues=tuple(issues),
        industries=tuple(sorted(industries.values(), key=lambda item: (item.period_end, item.scheme, item.code))),
    )


def _parse_industries(page: str, issues: list[str]) -> list[FundIndustryAllocationItem]:
    try:
        payload = json.loads(page)
    except ValueError:
        issues.append("industry_allocation_not_json")
        return []
    quarters = ((payload or {}).get("Data") or {}).get("QuarterInfos") or []
    result = []
    for quarter in quarters:
        for row in quarter.get("HYPZInfo") or []:
            ratio = _number(str(row.get("ZJZBL") or ""))
            period = str(row.get("FSRQ") or quarter.get("JZRQ") or "")[:10]
            code = str(row.get("HYDM") or "").strip()
            if ratio is None or not _DATE.fullmatch(period) or not code:
                continue
            result.append(
                FundIndustryAllocationItem(
                    period_end=period,
                    code=code,
                    name=str(row.get("HYMC") or code).strip(),
                    scheme="csrc" if _CSRC_CODE.fullmatch(code) else "hk",
                    net_asset_ratio=ratio / 100,
                )
            )
    return result


def _parse_holdings(page: str, issues: list[str]) -> list[FundTopHolding]:
    text = html.unescape(page.replace('\\"', '"'))
    result = []
    for box in text.split("<div class='boxitem")[1:]:
        period = re.search(r"截止至：\s*<font[^>]*>(\d{4}-\d{2}-\d{2})", box)
        if period is None:
            continue
        rows = _ROW.findall(box)
        if not rows:
            continue
        header = [_plain(cell) for cell in _CELL.findall(rows[0])]
        try:
            code_index = header.index("股票代码")
            name_index = header.index("股票名称")
            ratio_index = next(index for index, cell in enumerate(header) if cell.startswith("占净值"))
        except (ValueError, StopIteration):
            issues.append(f"holdings_header_unrecognized:{period.group(1)}")
            continue
        for rank, row in enumerate(rows[1:], start=1):
            cells = _CELL.findall(row)
            if len(cells) <= max(code_index, name_index, ratio_index):
                continue
            stock_code = _plain(cells[code_index])
            if not stock_code:
                continue
            result.append(
                FundTopHolding(
                    period_end=period.group(1),
                    rank=rank,
                    code=stock_code,
                    name=_plain(cells[name_index]),
                    market=_market(cells[code_index], stock_code),
                    net_asset_ratio=_ratio(_plain(cells[ratio_index])),
                )
            )
    return result


def _parse_allocations(page: str, issues: list[str]) -> list[FundAssetAllocationPeriod]:
    match = re.search(r"<table[^>]*tzxq[^>]*>(.*?)</table>", page, re.S)
    if match is None:
        issues.append("allocation_table_missing")
        return []
    result = []
    for row in _ROW.findall(match.group(1)):
        cells = [_plain(cell) for cell in _CELL.findall(row)]
        if len(cells) < 4 or not _DATE.fullmatch(cells[0]):
            continue
        result.append(
            FundAssetAllocationPeriod(
                period_end=cells[0],
                stock_ratio=_ratio(cells[1]),
                bond_ratio=_ratio(cells[2]),
                cash_ratio=_ratio(cells[3]),
                net_assets_100m=_number(cells[4]) if len(cells) > 4 else None,
            )
        )
    return result


def _market(cell_html: str, stock_code: str) -> str:
    quote = re.search(r"unify/r/(\d+)\.", cell_html)
    if quote and quote.group(1) in _QUOTE_MARKETS:
        if quote.group(1) == "0" and stock_code[:1] in ("4", "8", "9"):
            return "bj"
        return _QUOTE_MARKETS[quote.group(1)]
    if re.fullmatch(r"\d{6}", stock_code):
        return "sh" if stock_code[0] in "569" else "bj" if stock_code[0] in "48" else "sz"
    if re.fullmatch(r"\d{5}", stock_code):
        return "hk"
    return "other"


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", "", _TAG.sub("", html.unescape(fragment)))


def _ratio(text: str) -> Decimal | None:
    value = _number(text.rstrip("%")) if text.endswith("%") else None
    return None if value is None else value / 100


def _number(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
