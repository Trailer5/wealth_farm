"""Financial field extraction from parsed disclosure tables."""

from __future__ import annotations

import re

from src.data_src.models import DocumentTable, DocumentText, FinancialField


FIELD_ALIASES = {
    "revenue": ("营业收入", "营业总收入", "一、营业总收入", "主营业务收入"),
    "net_profit": ("净利润", "归属于母公司股东的净利润", "归母净利润", "归属于上市公司股东的净利润"),
    "total_assets": ("资产总计", "总资产", "资产合计"),
    "total_liabilities": ("负债合计", "负债总计", "总负债"),
    "operating_cash_flow": ("经营活动产生的现金流量净额", "经营活动现金流量净额", "经营现金流量净额"),
}

CURRENT_PERIOD_HEADER_KEYWORDS = (
    "本期",
    "本期金额",
    "本报告期",
    "本年",
    "本年累计",
    "期末",
    "期末余额",
)


class FinancialFieldExtractor:
    """Extract standard financial fields from tables and text."""

    extractor_name = "table_keyword_v1"

    def extract_from_tables(self, tables: list[DocumentTable]) -> list[FinancialField]:
        """Extract standard fields from table rows using keyword aliases."""

        fields: list[FinancialField] = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for table in tables:
            if table.status != "extracted":
                continue
            header_row: list[str | None] | None = None
            for row_index, row in enumerate(table.rows):
                row_text = " ".join(cell or "" for cell in row)
                if _looks_like_header(row):
                    header_row = row
                    continue
                for field_name, aliases in FIELD_ALIASES.items():
                    matched_alias = _match_alias(row_text, aliases)
                    if not matched_alias:
                        continue
                    value, column_index, unit = _find_numeric_value(row, header_row)
                    key = (table.document_id, field_name, table.table_index, row_index)
                    if key in seen:
                        continue
                    seen.add(key)
                    fields.append(
                        FinancialField(
                            document_id=table.document_id,
                            field_name=field_name,
                            field_label=matched_alias,
                            value=value,
                            unit=unit,
                            source=self.extractor_name,
                            table_index=table.table_index,
                            row_index=row_index,
                            column_index=column_index,
                            page_number=table.page_number,
                            raw={"row": row, "header": header_row},
                        )
                    )
        return fields

    def extract_from_texts(self, texts: list[DocumentText]) -> list[FinancialField]:
        """Extract standard fields from plain text paragraphs."""

        fields: list[FinancialField] = []
        seen: set[tuple[str, str]] = set()
        for text in texts:
            if text.status != "extracted":
                continue
            lines = [line.strip() for line in text.text_content.splitlines() if line.strip()]
            for line in lines:
                for field_name, aliases in FIELD_ALIASES.items():
                    matched_alias = _match_alias(line, aliases)
                    if not matched_alias:
                        continue
                    value, unit = _parse_number(line)
                    key = (text.document_id, field_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    fields.append(
                        FinancialField(
                            document_id=text.document_id,
                            field_name=field_name,
                            field_label=matched_alias,
                            value=value,
                            unit=unit,
                            source="text_keyword_v1",
                            raw={"line": line},
                        )
                    )
        return fields


def _match_alias(text: str, aliases: tuple[str, ...]) -> str | None:
    compact_text = re.sub(r"\s+", "", text)
    for alias in aliases:
        if re.sub(r"\s+", "", alias) in compact_text:
            return alias
    return None


def _find_numeric_value(row: list[str | None], header_row: list[str | None] | None = None) -> tuple[float | None, int | None, str | None]:
    if header_row:
        for index in _preferred_value_columns(header_row):
            if index >= len(row):
                continue
            cell = row[index]
            if not cell:
                continue
            parsed = _parse_number(cell)
            if parsed[0] is not None:
                return parsed[0], index, parsed[1]
    for index in range(len(row) - 1, -1, -1):
        cell = row[index]
        if not cell:
            continue
        parsed = _parse_number(cell)
        if parsed[0] is not None:
            return parsed[0], index, parsed[1]
    return None, None, None


def _looks_like_header(row: list[str | None]) -> bool:
    text = "".join(cell or "" for cell in row)
    has_period_keyword = any(keyword in text for keyword in CURRENT_PERIOD_HEADER_KEYWORDS)
    has_project_label = any(label in text for label in ("项目", "科目", "指标"))
    return has_period_keyword and has_project_label


def _preferred_value_columns(header_row: list[str | None]) -> list[int]:
    preferred: list[int] = []
    fallback: list[int] = []
    for index, cell in enumerate(header_row):
        text = cell or ""
        if not text:
            continue
        if any(keyword in text for keyword in CURRENT_PERIOD_HEADER_KEYWORDS):
            preferred.append(index)
        elif index > 0:
            fallback.append(index)
    return preferred + fallback


def _parse_number(value: str) -> tuple[float | None, str | None]:
    text = value.replace(",", "").replace("，", "").strip()
    negative = False
    if re.search(r"[（(]\s*[-+]?\d+(\.\d+)?\s*[）)]", text):
        negative = True
    unit = None
    multiplier = 1.0
    if "万元" in text:
        unit = "万元"
        multiplier = 10000.0
    elif "亿元" in text:
        unit = "亿元"
        multiplier = 100000000.0
    elif "元" in text:
        unit = "元"
    match = re.search(r"[-+]?\d+(\.\d+)?", text)
    if not match:
        return None, unit
    number = float(match.group(0)) * multiplier
    return -abs(number) if negative else number, "元" if unit else None
