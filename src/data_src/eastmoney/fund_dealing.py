"""Open-fund dealing rules, fees, managers and benchmark from Eastmoney F10 pages.

Design notes: docs/modules/3-分析工具/基金与ETF研究数据模块.md「场外基金时点与仓位事实」.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from io import StringIO
from typing import Any

from src.data_src.common import DataSourceError, normalize_a_share_code
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import (
    FundManagerTerm,
    FundPurchaseFeeTier,
    FundRedemptionFeeTier,
    OpenFundDealingInfo,
)

_FEE_URL = "https://fundf10.eastmoney.com/jjfl_{code}.html"
_MANAGER_URL = "https://fundf10.eastmoney.com/jjjl_{code}.html"
_OVERVIEW_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
_BOUND = re.compile(r"(大于等于|大于|小于等于|小于|等于)?(\d+(?:\.\d+)?)(万元|元|天|日|个月|月|年)(以上|以下|以内)?")
_UNIT_DAYS = {"天": 1, "日": 1, "个月": 30, "月": 30, "年": 365}


class EastmoneyFundDealingDataSource:
    source = "eastmoney:fundf10"

    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(
            headers={"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
            retry_count=2,
        )

    def get_dealing_info(self, symbol: str) -> OpenFundDealingInfo:
        code = normalize_a_share_code(symbol)
        urls = tuple(template.format(code=code) for template in (_FEE_URL, _MANAGER_URL, _OVERVIEW_URL))
        fee_html, manager_html, overview_html = (self.http.get_text(url) for url in urls)
        sections = _titled_tables(fee_html)
        if not sections:
            raise DataSourceError(f"Eastmoney fee page for {code} has no tables")
        return parse_dealing_info(
            code,
            sections=sections,
            manager_rows=_manager_rows(manager_html),
            overview=_overview_pairs(overview_html),
            fetched_on=date.today().isoformat(),
            source=self.source,
            source_urls=urls,
        )


def parse_dealing_info(
    code: str,
    *,
    sections: dict[str, list[list[str]]],
    manager_rows: list[dict[str, str]],
    overview: dict[str, str],
    fetched_on: str,
    source: str,
    source_urls: tuple[str, ...],
) -> OpenFundDealingInfo:
    issues: list[str] = []
    pairs: dict[str, str] = {}
    for title in ("交易状态", "申购与赎回金额", "交易确认日", "运作费用"):
        for row in sections.get(title, []):
            pairs.update(dict(zip(row[0::2], row[1::2])))
    purchase_table = next((rows for title, rows in sections.items() if title.startswith("申购费率")), [])
    redemption_table = sections.get("赎回费率", [])
    purchase_fees = []
    for row in purchase_table[1:]:
        if len(row) < 2:
            continue
        low, high = _amount_bounds(row[0])
        original, _, discounted = row[1].partition("|")
        fixed = re.search(r"每笔(\d+(?:\.\d+)?)元", row[1])
        purchase_fees.append(
            FundPurchaseFeeTier(
                amount_text=row[0],
                min_amount=low,
                max_amount=high,
                rate=_percent(original),
                discounted_rate=_percent(discounted) if discounted else _percent(original),
                fixed_fee=float(fixed.group(1)) if fixed else None,
            )
        )
    redemption_fees = []
    for row in redemption_table[1:]:
        if len(row) < 2:
            continue
        rate = _percent(row[1])
        bounds = _day_bounds(row[0])
        if rate is None or bounds is None:
            issues.append(f"redemption_tier_unparsed:{row[0]}={row[1]}")
            continue
        redemption_fees.append(FundRedemptionFeeTier(row[0], bounds[0], bounds[1], rate))
    if not redemption_fees:
        issues.append("redemption_fee_table_missing")
    terms = tuple(
        FundManagerTerm(
            start_date=row["起始期"],
            end_date=None if row["截止期"] in ("至今", "", "--") else row["截止期"],
            managers=tuple(row["基金经理"].split()),
            reported_return=row.get("任职回报") or None,
        )
        for row in manager_rows
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("起始期", ""))
    )
    if not terms:
        issues.append("manager_history_missing")
    min_purchase = _PLAIN_NUMBER.search(pairs.get("申购起点", ""))
    return OpenFundDealingInfo(
        symbol=code,
        fetched_on=fetched_on,
        purchase_status=pairs.get("申购状态"),
        redemption_status=pairs.get("赎回状态"),
        min_purchase_amount=float(min_purchase.group(1)) if min_purchase else None,
        daily_purchase_limit=pairs.get("日累计申购限额"),
        purchase_confirm_days=_t_plus(pairs.get("买入确认日")),
        redemption_confirm_days=_t_plus(pairs.get("卖出确认日")),
        management_fee_rate=_percent(pairs.get("管理费率", "")),
        custody_fee_rate=_percent(pairs.get("托管费率", "")),
        sales_service_fee_rate=_percent(pairs.get("销售服务费率", "")),
        purchase_fees=tuple(purchase_fees),
        redemption_fees=tuple(redemption_fees),
        manager_terms=terms,
        benchmark_text=overview.get("业绩比较基准") or None,
        tracking_target=_meaningful(overview.get("跟踪标的")),
        fund_type=overview.get("基金类型") or None,
        source=source,
        source_urls=source_urls,
        parse_issues=tuple(issues),
    )


_PLAIN_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _titled_tables(html: str) -> dict[str, list[list[str]]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    sections: dict[str, list[list[str]]] = {}
    for title in soup.find_all("h4", class_="t"):
        name = re.sub(r"\s+", "", title.get_text(strip=True))
        table = title.find_next("table")
        if table is None or name in sections:
            continue
        sections[name] = [
            [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            for tr in table.find_all("tr")
        ]
    return sections


def _manager_rows(html: str) -> list[dict[str, str]]:
    import pandas as pd

    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return []
    for table in tables:
        columns = [str(column) for column in table.columns]
        if "起始期" in columns and "基金经理" in columns:
            return [
                {key: _clean(str(value)) for key, value in zip(columns, row)}
                for row in table.itertuples(index=False)
            ]
    return []


def _overview_pairs(html: str) -> dict[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    pairs = {}
    for th in soup.find_all("th"):
        td = th.find_next_sibling("td")
        if td is not None:
            pairs[_clean(th.get_text(strip=True))] = _clean(td.get_text(" ", strip=True))
    return pairs


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _meaningful(value: str | None) -> str | None:
    if not value or value in ("该基金无跟踪标的", "--"):
        return None
    return value


def _percent(text: str) -> Decimal | None:
    match = _PERCENT.search(text or "")
    return Decimal(match.group(1)) / Decimal("100") if match else None


def _t_plus(text: str | None) -> int | None:
    match = re.fullmatch(r"T\+(\d+)", (text or "").strip())
    return int(match.group(1)) if match else None


def _amount_bounds(text: str) -> tuple[float | None, float | None]:
    low = high = None
    for comparator, number, unit, suffix in _BOUND.findall(_normalize(text)):
        value = float(number) * (10000 if unit == "万元" else 1)
        if comparator.startswith("大于") or suffix == "以上":
            low = value
        elif comparator.startswith("小于") or suffix in ("以下", "以内"):
            high = value
    return low, high


def _day_bounds(text: str) -> tuple[int, int | None] | None:
    low, high, matched = 0, None, False
    for comparator, number, unit, suffix in _BOUND.findall(_normalize(text)):
        if unit not in _UNIT_DAYS:
            continue
        matched = True
        days = int(round(float(number) * _UNIT_DAYS[unit]))
        if comparator == "大于等于" or (not comparator and suffix == "以上"):
            low = days
        elif comparator == "大于":
            low = days + 1
        elif comparator == "小于" or (not comparator and suffix in ("以下", "以内")):
            high = days
        elif comparator == "小于等于":
            high = days + 1
    return (low, high) if matched else None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("，", ",").replace("≥", "大于等于").replace("<", "小于")
