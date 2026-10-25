"""Structured extraction for open-fund official disclosure documents."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from src.data_src.models import (
    DisclosureDocument,
    DocumentTable,
    DocumentText,
)
from src.fund_research.models import (
    DisclosureExtractionStatus,
    OpenFundAssetAllocation,
    OpenFundHolding,
    OpenFundIndustryAllocation,
    OpenFundManagerTenure,
    OpenFundPerformanceComparison,
    OpenFundPeriodicSnapshot,
    OpenFundProfileSnapshot,
    OpenFundShareClassSnapshot,
    OpenFundShareFlow,
)


OPEN_FUND_DISCLOSURE_PARSER_VERSION = "open_fund_disclosure_v2"

_ASSET_ALLOCATION_CATEGORIES = {
    "权益投资",
    "股票",
    "基金投资",
    "固定收益投资",
    "债券",
    "资产支持证券",
    "贵金属投资",
    "金融衍生品投资",
    "买入返售金融资产",
    "银行存款和结算备付金合计",
    "其他资产",
    "合计",
}
_HONG_KONG_INDUSTRIES = {
    "原材料",
    "非日常生活消费品",
    "日常消费品",
    "能源",
    "金融",
    "医疗保健",
    "工业",
    "信息技术",
    "通信服务",
    "公用事业",
    "房地产",
}


class OpenFundDisclosureParser:
    """Extract product-profile and periodic-report snapshots."""

    parser_version = OPEN_FUND_DISCLOSURE_PARSER_VERSION

    def parse_profile(
        self,
        *,
        fund_asset_id: str,
        document: DisclosureDocument,
        text: DocumentText,
        tables: Iterable[DocumentTable],
    ) -> OpenFundProfileSnapshot:
        usable_tables = _usable_tables(tables)
        rows = tuple(_iter_rows(usable_tables))
        manager_name, manager_page = _lookup_value(
            rows,
            "基金管理人",
        )
        custodian_name, custodian_page = _lookup_value(
            rows,
            "基金托管人",
        )
        fund_name, _ = _lookup_value(rows, "基金简称")
        share_class_name, share_class_page = _lookup_value(
            rows,
            "下属基金简称",
        )
        share_class_code, _ = _lookup_value(
            rows,
            "下属基金交易代码",
        )
        if not share_class_code:
            share_class_code, _ = _lookup_value(rows, "基金代码")
        benchmark_text, benchmark_page = _lookup_value(
            rows,
            "业绩比较基准",
        )
        management_fee, management_fee_page = _lookup_rate(
            rows,
            "管理费",
        )
        custody_fee, custody_fee_page = _lookup_rate(
            rows,
            "托管费",
        )
        sales_service_fee, sales_service_fee_page = _lookup_rate(
            rows,
            "销售服务费",
        )
        if sales_service_fee is None and share_class_code:
            fee_schedule_page = _fee_schedule_page(rows)
            if fee_schedule_page is not None:
                sales_service_fee = Decimal("0")
                sales_service_fee_page = fee_schedule_page
        managers = _profile_managers(rows)
        effective_date = (
            _extract_chinese_date(text.text_content, "编制日期")
            or document.announcement_date
        )
        required = {
            "fund_name": fund_name,
            "share_class_name": share_class_name,
            "share_class_code": share_class_code,
            "manager_name": manager_name,
            "custodian_name": custodian_name,
            "benchmark_text": benchmark_text,
            "management_fee_rate": management_fee,
            "custody_fee_rate": custody_fee,
        }
        status, errors = _extraction_state(
            required,
            text,
            usable_tables,
        )
        page_numbers = {
            key: page
            for key, page in {
                "manager_name": manager_page,
                "custodian_name": custodian_page,
                "share_class": share_class_page,
                "fund_managers": (
                    managers[0].page_number if managers else None
                ),
                "benchmark_text": benchmark_page,
                "management_fee_rate": management_fee_page,
                "custody_fee_rate": custody_fee_page,
                "sales_service_fee_rate": sales_service_fee_page,
            }.items()
            if page is not None
        }
        return OpenFundProfileSnapshot(
            fund_asset_id=fund_asset_id,
            effective_date=effective_date,
            announcement_date=document.announcement_date,
            fund_name=fund_name,
            share_class_name=share_class_name,
            share_class_code=share_class_code,
            manager_name=manager_name,
            custodian_name=custodian_name,
            fund_managers=managers,
            benchmark_text=benchmark_text,
            management_fee_rate=management_fee,
            custody_fee_rate=custody_fee,
            sales_service_fee_rate=sales_service_fee,
            source_document_id=document.document_id,
            source_title=document.title,
            source_url=document.file_url,
            source=document.source,
            parser_version=self.parser_version,
            extraction_status=status,
            page_numbers=page_numbers,
            extraction_errors=errors,
            raw={
                "document_text_status": text.status,
                "table_count": len(usable_tables),
            },
        )

    def parse_periodic(
        self,
        *,
        fund_asset_id: str,
        document: DisclosureDocument,
        text: DocumentText,
        tables: Iterable[DocumentTable],
    ) -> OpenFundPeriodicSnapshot:
        usable_tables = _usable_tables(tables)
        rows = tuple(_iter_rows(usable_tables))
        period_start, period_end = _report_period(
            text.text_content,
            document.title,
        )
        share_classes = _share_classes(rows)
        share_flows = _share_flows(rows, share_classes)
        asset_allocations = _asset_allocations(usable_tables)
        industry_allocations = _industry_allocations(
            usable_tables
        )
        holdings = _top_holdings(usable_tables)
        performance_comparisons = _performance_comparisons(
            usable_tables,
            share_classes,
        )
        required = {
            "share_classes": share_classes,
            "share_flows": share_flows,
            "asset_allocations": asset_allocations,
            "top_holdings": holdings,
        }
        status, errors = _extraction_state(
            required,
            text,
            usable_tables,
        )
        return OpenFundPeriodicSnapshot(
            fund_asset_id=fund_asset_id,
            report_period_start=period_start,
            report_period_end=period_end,
            announcement_date=document.announcement_date,
            share_classes=share_classes,
            share_flows=share_flows,
            asset_allocations=asset_allocations,
            industry_allocations=industry_allocations,
            top_holdings=holdings,
            performance_comparisons=performance_comparisons,
            source_document_id=document.document_id,
            source_title=document.title,
            source_url=document.file_url,
            source=document.source,
            parser_version=self.parser_version,
            extraction_status=status,
            extraction_errors=errors,
            raw={
                "document_text_status": text.status,
                "table_count": len(usable_tables),
            },
        )


def _usable_tables(
    tables: Iterable[DocumentTable],
) -> tuple[DocumentTable, ...]:
    return tuple(
        sorted(
            (
                table
                for table in tables
                if table.status == "extracted" and table.rows
            ),
            key=lambda item: (
                item.page_number or 0,
                item.table_index,
            ),
        )
    )


def _iter_rows(
    tables: Iterable[DocumentTable],
):
    for table in tables:
        page_number = table.page_number or 1
        for row in table.rows:
            yield page_number, row


def _compact(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).strip()


def _spaced(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _nonempty_after(
    row: list[str | None],
    index: int,
) -> list[str]:
    return [
        _compact(value)
        for value in row[index + 1 :]
        if _compact(value)
    ]


def _lookup_value(
    rows,
    label: str,
) -> tuple[str, int | None]:
    for page_number, row in rows:
        for index, cell in enumerate(row):
            if _compact(cell) != label:
                continue
            values = _nonempty_after(row, index)
            if values:
                return values[0], page_number
    return "", None


def _lookup_rate(
    rows,
    label: str,
) -> tuple[Decimal | None, int | None]:
    for page_number, row in rows:
        cells = [_compact(cell) for cell in row]
        for index, cell in enumerate(cells):
            if cell != label:
                continue
            for value in cells[index + 1 :]:
                rate = _percentage_ratio(value)
                if rate is not None:
                    return rate, page_number
    return None, None


def _fee_schedule_page(rows) -> int | None:
    for page_number, row in rows:
        cells = {_compact(cell) for cell in row if _compact(cell)}
        if "费用类别" in cells or "收费方式/年费率或金额" in cells:
            return page_number
    return None


def _profile_managers(rows) -> tuple[OpenFundManagerTenure, ...]:
    managers = []
    for page_number, row in rows:
        cells = [_compact(cell) for cell in row]
        for index, cell in enumerate(cells):
            if cell != "基金经理":
                continue
            name = next(
                (
                    value
                    for value in cells[index + 1 :]
                    if value
                    and "开始担任本基金" not in value
                    and "证券从业日期" not in value
                ),
                "",
            )
            start_date = None
            for key_index, value in enumerate(cells):
                if "开始担任本基金" not in value:
                    continue
                for candidate in cells[key_index + 1 :]:
                    start_date = _parse_chinese_date(candidate)
                    if start_date is not None:
                        break
            if name and start_date:
                managers.append(
                    OpenFundManagerTenure(
                        name=name,
                        start_date=start_date,
                        page_number=page_number,
                    )
                )
    return tuple(managers)


def _share_classes(rows) -> tuple[OpenFundShareClassSnapshot, ...]:
    names, _ = _row_values(rows, "下属分级基金的基金简称")
    if not names:
        names, _ = _row_values(rows, "下属基金的基金简称")
    codes, _ = _row_values(rows, "下属分级基金的交易代码")
    if not codes:
        codes, _ = _row_values(rows, "下属基金的交易代码")
    ending_shares, shares_page = _paired_decimals(
        rows,
        "报告期末下属分级基金的份额总额",
    )
    if not ending_shares:
        ending_shares, shares_page = _paired_decimals(
            rows,
            "报告期末下属基金的份额总额",
        )
    unit_navs, _ = _paired_decimals(
        rows,
        "期末基金份额净值",
    )
    net_assets, _ = _paired_decimals(
        rows,
        "期末基金资产净值",
    )
    count = min(len(names), len(codes), len(ending_shares))
    result = []
    for index in range(count):
        unit_nav = (
            unit_navs[index]
            if index < len(unit_navs)
            else None
        )
        net_asset_value = (
            net_assets[index]
            if index < len(net_assets)
            else None
        )
        if net_asset_value is None and unit_nav is not None:
            net_asset_value = ending_shares[index] * unit_nav
        result.append(
            OpenFundShareClassSnapshot(
                share_class_name=names[index],
                share_class_code=codes[index],
                ending_shares=ending_shares[index],
                unit_nav=unit_nav,
                net_asset_value=net_asset_value,
                page_number=shares_page or 1,
            )
        )
    return tuple(result)


def _share_flows(
    rows,
    share_classes: tuple[OpenFundShareClassSnapshot, ...],
) -> tuple[OpenFundShareFlow, ...]:
    opening, page_number = _paired_decimals(
        rows,
        "报告期期初基金份额总额",
    )
    subscriptions, _ = _paired_decimals(
        rows,
        "报告期期间基金总申购份额",
    )
    redemptions, _ = _paired_decimals(
        rows,
        "报告期期间基金总赎回份额",
    )
    ending, _ = _paired_decimals(
        rows,
        "报告期期末基金份额总额",
    )
    count = min(
        len(share_classes),
        len(opening),
        len(subscriptions),
        len(redemptions),
        len(ending),
    )
    return tuple(
        OpenFundShareFlow(
            share_class_name=share_classes[index].share_class_name,
            share_class_code=share_classes[index].share_class_code,
            opening_shares=opening[index],
            subscriptions=subscriptions[index],
            redemptions=redemptions[index],
            ending_shares=ending[index],
            page_number=page_number or 1,
        )
        for index in range(count)
    )


def _row_values(rows, label: str) -> tuple[list[str], int | None]:
    for page_number, row in rows:
        for index, cell in enumerate(row):
            if _compact(cell) == label:
                return _nonempty_after(row, index), page_number
    return [], None


def _paired_decimals(
    rows,
    label: str,
) -> tuple[list[Decimal], int | None]:
    for page_number, row in rows:
        label_index = next(
            (
                index
                for index, cell in enumerate(row)
                if label in _compact(cell)
            ),
            None,
        )
        if label_index is None:
            continue
        raw_values = [
            _spaced(value)
            for value in row[label_index + 1 :]
            if _spaced(value) and _spaced(value) != "-"
        ]
        if len(raw_values) == 2:
            split = re.match(r"^(\d)\s+(.+)$", raw_values[1])
            if split and "." in raw_values[0]:
                raw_values = [
                    f"{raw_values[0]}{split.group(1)}",
                    split.group(2),
                ]
        values = [
            value
            for value in (_decimal(item) for item in raw_values)
            if value is not None
        ]
        if values:
            return values, page_number
    return [], None


def _asset_allocations(
    tables: tuple[DocumentTable, ...],
) -> tuple[OpenFundAssetAllocation, ...]:
    result = []
    seen = set()
    for table in tables:
        for row in table.rows:
            if len(row) < 4:
                continue
            category = _compact(row[1]).removeprefix("其中：")
            if category not in _ASSET_ALLOCATION_CATEGORIES:
                continue
            amount = _decimal(row[2])
            ratio = _percentage_ratio(row[3])
            if amount is None or ratio is None:
                continue
            key = (category, amount, ratio)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                OpenFundAssetAllocation(
                    category=category,
                    amount=amount,
                    ratio=ratio,
                    basis="total_assets",
                    page_number=table.page_number or 1,
                )
            )
    return tuple(result)


def _industry_allocations(
    tables: tuple[DocumentTable, ...],
) -> tuple[OpenFundIndustryAllocation, ...]:
    domestic_groups = []
    hong_kong = []
    for table in tables:
        domestic_items = []
        domestic_total = Decimal("0")
        for row in table.rows:
            if len(row) >= 4:
                code = _compact(row[0])
                category = _compact(row[1])
                if code in set("ABCDEFGHIJKLMNOPQRS"):
                    amount = _decimal(row[2])
                    ratio = _percentage_ratio(row[3])
                    if amount is not None and ratio is not None:
                        domestic_items.append(
                            (
                                category,
                                amount,
                                ratio,
                                table.page_number or 1,
                            )
                        )
                elif category == "合计":
                    domestic_total = (
                        _percentage_ratio(row[3])
                        or domestic_total
                    )
            if len(row) >= 3:
                category = _compact(row[0])
                if category not in _HONG_KONG_INDUSTRIES:
                    continue
                amount = _decimal(row[1])
                ratio = _percentage_ratio(row[2])
                if amount is None or ratio is None:
                    continue
                hong_kong.append(
                    OpenFundIndustryAllocation(
                        category=category,
                        fair_value=amount,
                        net_asset_ratio=ratio,
                        market_scope="hong_kong",
                        portfolio_scope="all",
                        page_number=table.page_number or 1,
                    )
                )
        if domestic_items:
            domestic_groups.append(
                (domestic_items, domestic_total)
            )
    largest_total = max(
        (total for _, total in domestic_groups),
        default=Decimal("0"),
    )
    domestic = []
    multiple_groups = len(domestic_groups) > 1
    for items, total in domestic_groups:
        if multiple_groups:
            portfolio_scope = (
                "index" if total == largest_total else "active"
            )
        else:
            portfolio_scope = "all"
        domestic.extend(
            OpenFundIndustryAllocation(
                category=category,
                fair_value=amount,
                net_asset_ratio=ratio,
                market_scope="domestic",
                portfolio_scope=portfolio_scope,
                page_number=page_number,
            )
            for category, amount, ratio, page_number in items
        )
    return tuple(domestic + hong_kong)


def _top_holdings(
    tables: tuple[DocumentTable, ...],
) -> tuple[OpenFundHolding, ...]:
    for table in tables:
        header = "".join(
            _compact(cell)
            for row in table.rows[:4]
            for cell in row
        )
        if "股票代码" not in header or "股票名称" not in header:
            continue
        holdings = []
        for row in table.rows:
            if len(row) < 6:
                continue
            rank_text = _compact(row[0])
            if not rank_text.isdigit():
                continue
            rank = int(rank_text)
            if rank > 10:
                continue
            quantity = _decimal(row[3])
            fair_value = _decimal(row[4])
            ratio = _percentage_ratio(row[5])
            if (
                quantity is None
                or fair_value is None
                or ratio is None
            ):
                continue
            holdings.append(
                OpenFundHolding(
                    rank=rank,
                    security_code=_compact(row[1]),
                    security_name=_compact(row[2]),
                    quantity=quantity,
                    fair_value=fair_value,
                    net_asset_ratio=ratio,
                    page_number=table.page_number or 1,
                )
            )
        if len(holdings) >= 10:
            return tuple(sorted(holdings, key=lambda item: item.rank))
    return ()


def _performance_comparisons(
    tables: tuple[DocumentTable, ...],
    share_classes: tuple[OpenFundShareClassSnapshot, ...],
) -> tuple[OpenFundPerformanceComparison, ...]:
    result = []
    seen = set()
    current_columns: tuple[int, int, int | None] | None = None
    current_share_class = None
    performance_group_index = 0
    for table in tables:
        layout = _performance_table_layout(table.rows)
        if layout is not None:
            data_start, fund_column, benchmark_column, difference_column = (
                layout
            )
            current_columns = (
                fund_column,
                benchmark_column,
                difference_column,
            )
            table_text = "".join(
                _compact(cell)
                for row in table.rows
                for cell in row
            )
            current_share_class = next(
                (
                    item
                    for item in share_classes
                    if (
                        item.share_class_name in table_text
                        or item.share_class_code in table_text
                    )
                ),
                None,
            )
            if current_share_class is None and share_classes:
                current_share_class = share_classes[
                    min(performance_group_index, len(share_classes) - 1)
                ]
            performance_group_index += 1
            data_rows = table.rows[data_start:]
        elif (
            current_columns is not None
            and table.rows
            and _is_performance_period_label(table.rows[0][0])
        ):
            fund_column, benchmark_column, difference_column = current_columns
            data_rows = table.rows
        else:
            current_columns = None
            current_share_class = None
            continue
        for row in data_rows:
            if not row:
                continue
            period_label = _compact(row[0])
            if not _is_performance_period_label(period_label):
                continue
            if max(fund_column, benchmark_column) >= len(row):
                continue
            fund_return = _signed_percentage_ratio(row[fund_column])
            benchmark_return = _signed_percentage_ratio(
                row[benchmark_column]
            )
            if fund_return is None or benchmark_return is None:
                continue
            difference = None
            if (
                difference_column is not None
                and difference_column < len(row)
            ):
                difference = _signed_percentage_ratio(
                    row[difference_column]
                )
            if difference is None:
                difference = fund_return - benchmark_return
            key = (
                (
                    current_share_class.share_class_code
                    if current_share_class
                    else ""
                ),
                period_label,
                fund_return,
                benchmark_return,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(
                OpenFundPerformanceComparison(
                    period_label=period_label,
                    fund_return=fund_return,
                    benchmark_return=benchmark_return,
                    tracking_difference=difference,
                    page_number=table.page_number or 1,
                    share_class_name=(
                        current_share_class.share_class_name
                        if current_share_class
                        else ""
                    ),
                    share_class_code=(
                        current_share_class.share_class_code
                        if current_share_class
                        else ""
                    ),
                )
            )
    return tuple(result)


def _performance_table_layout(
    rows: list[list[str | None]],
) -> tuple[int, int, int, int | None] | None:
    data_start = next(
        (
            index
            for index, row in enumerate(rows)
            if row and _is_performance_period_label(row[0])
        ),
        None,
    )
    if data_start is None:
        return None
    width = max((len(row) for row in rows[:data_start]), default=0)
    columns = [
        "".join(
            _compact(row[column])
            for row in rows[:data_start]
            if column < len(row)
        )
        for column in range(width)
    ]
    fund_column = next(
        (
            column
            for column, text in enumerate(columns)
            if "净值增长率" in text and "标准差" not in text
        ),
        None,
    )
    benchmark_column = next(
        (
            column
            for column, text in enumerate(columns)
            if (
                "业绩比较基准" in text
                and "收益率" in text
                and "标准差" not in text
            )
        ),
        None,
    )
    if fund_column is None or benchmark_column is None:
        return None
    difference_column = next(
        (
            column
            for column, text in enumerate(columns)
            if "①-③" in text.replace("－", "-")
        ),
        None,
    )
    return (
        data_start,
        fund_column,
        benchmark_column,
        difference_column,
    )


def _is_performance_period_label(value: object | None) -> bool:
    text = _compact(value)
    return text.startswith(
        (
            "过去",
            "本报告期",
            "自基金合同",
            "自基金成立",
            "基金合同生效",
        )
    )


def _extraction_state(
    required: dict[str, object],
    text: DocumentText,
    tables: tuple[DocumentTable, ...],
) -> tuple[DisclosureExtractionStatus, tuple[str, ...]]:
    missing = tuple(
        f"missing:{name}"
        for name, value in required.items()
        if value in (None, "", (), [])
    )
    if len(missing) == len(required):
        details = list(missing)
        if text.error:
            details.append(f"text:{text.error}")
        if not tables:
            details.append("tables:no_extracted_tables")
        return (
            DisclosureExtractionStatus.EXTRACTION_FAILED,
            tuple(details),
        )
    if missing:
        return DisclosureExtractionStatus.PARTIAL, missing
    return DisclosureExtractionStatus.EXTRACTED, ()


def _report_period(
    text: str,
    title: str,
) -> tuple[str, str]:
    compact = _compact(text)
    match = re.search(
        r"报告期[（(](\d{4})年(\d{1,2})月(\d{1,2})日"
        r"[-—至]+(\d{4})年(\d{1,2})月(\d{1,2})日",
        compact,
    )
    if match:
        values = [int(value) for value in match.groups()]
        return (
            date(values[0], values[1], values[2]).isoformat(),
            date(values[3], values[4], values[5]).isoformat(),
        )
    title_match = re.search(
        r"(\d{4})年第?([一二三四1234])季度",
        _compact(title),
    )
    if title_match:
        year = int(title_match.group(1))
        quarter_map = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "1": 1,
            "2": 2,
            "3": 3,
            "4": 4,
        }
        quarter = quarter_map[title_match.group(2)]
        starts = {
            1: (1, 1),
            2: (4, 1),
            3: (7, 1),
            4: (10, 1),
        }
        ends = {
            1: (3, 31),
            2: (6, 30),
            3: (9, 30),
            4: (12, 31),
        }
        return (
            date(year, *starts[quarter]).isoformat(),
            date(year, *ends[quarter]).isoformat(),
        )
    raise ValueError("Unable to determine fund report period")


def _extract_chinese_date(text: str, label: str) -> str | None:
    compact = _compact(text)
    match = re.search(
        rf"{re.escape(label)}[:：]?(\d{{4}})年"
        r"(\d{1,2})月(\d{1,2})日",
        compact,
    )
    if not match:
        return None
    return date(*(int(value) for value in match.groups())).isoformat()


def _parse_chinese_date(value: str) -> str | None:
    match = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        value,
    )
    if not match:
        return None
    return date(*(int(item) for item in match.groups())).isoformat()


def _decimal(value: object | None) -> Decimal | None:
    text = _spaced(value)
    if not text or text in {"-", "—"}:
        return None
    multiplier = Decimal("1")
    if "亿元" in text or text.endswith("亿"):
        multiplier = Decimal("100000000")
    elif "万元" in text or text.endswith("万"):
        multiplier = Decimal("10000")
    text = (
        text.replace(",", "")
        .replace("亿元", "")
        .replace("万元", "")
        .replace("亿", "")
        .replace("万", "")
        .replace("份", "")
        .replace("元", "")
        .strip()
    )
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    try:
        return Decimal(text) * multiplier
    except InvalidOperation:
        return None


def _percentage_ratio(value: object | None) -> Decimal | None:
    text = _compact(value)
    if not text or text in {"-", "—"}:
        return None
    number = _decimal(text.removesuffix("%"))
    if number is None:
        return None
    ratio = number / Decimal("100")
    if ratio < 0 or ratio > 1:
        return None
    return ratio


def _signed_percentage_ratio(
    value: object | None,
) -> Decimal | None:
    text = _compact(value)
    if not text or text in {"-", "—"}:
        return None
    number = _decimal(text.removesuffix("%"))
    if number is None:
        return None
    return number / Decimal("100")
