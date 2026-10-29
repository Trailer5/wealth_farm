"""Normalize wide provider financial rows into long-form points."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.data_src.common import to_date_string

from .models import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FINANCIAL_DATASETS,
    FinancialDataPoint,
    FinancialValueScope,
)
from .periods import infer_period_kind


class FinancialNormalizationError(ValueError):
    """Raised when provider rows cannot become research data."""


_REPORT_PERIOD_KEYS = (
    "REPORT_DATE",
    "报告期",
    "报告日期",
    "截止日期",
    "日期",
)
_PUBLISHED_AT_KEYS = (
    "NOTICE_DATE",
    "公告日期",
    "发布日期",
    "最新公告日期",
    "UPDATE_DATE",
)
_METADATA_KEYS = frozenset(
    {
        *_REPORT_PERIOD_KEYS,
        *_PUBLISHED_AT_KEYS,
        "股票代码",
        "证券代码",
        "SECURITY_CODE",
        "SECUCODE",
        "股票简称",
        "证券简称",
        "SECURITY_NAME_ABBR",
        "REPORT_TYPE",
        "报告类型",
        "REPORT_DATE_NAME",
        "ORG_CODE",
        "ORG_TYPE",
        "SECURITY_TYPE_CODE",
        "CURRENCY",
    }
)

_CANONICAL_FIELDS = {
    "营业总收入": "total_revenue",
    "营业收入": "revenue",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "parent_net_profit",
    "归母净利润": "parent_net_profit",
    "资产总计": "total_assets",
    "负债合计": "total_liabilities",
    "所有者权益合计": "total_equity",
    "股东权益合计": "total_equity",
    "货币资金": "cash_and_equivalents",
    "总股本": "shares_outstanding",
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "经营活动现金流量净额": "operating_cash_flow",
    "投资活动产生的现金流量净额": "investing_cash_flow",
    "筹资活动产生的现金流量净额": "financing_cash_flow",
    "净资产收益率": "roe",
    "净资产收益率(%)": "roe",
    "总资产报酬率": "roa",
    "销售毛利率": "gross_margin",
    "销售净利率": "net_margin",
    "资产负债率": "debt_to_assets",
    "TOTAL_OPERATE_INCOME": "total_revenue",
    "OPERATE_INCOME": "revenue",
    "OPERATE_COST": "operating_cost",
    "RESEARCH_EXPENSE": "research_expense",
    "研发费用": "research_expense",
    "TOTAL_OPERATE_COST": "total_operating_cost",
    "PARENT_NETPROFIT": "parent_net_profit",
    "DEDUCT_PARENT_NETPROFIT": "deducted_parent_net_profit",
    "扣除非经常性损益后的净利润": "deducted_parent_net_profit",
    "KCFJCXSYJLR": "deducted_parent_net_profit_ytd",
    "NETPROFIT": "net_profit",
    "OPERATE_PROFIT": "operating_profit",
    "TOTAL_PROFIT": "pretax_profit",
    "INCOME_TAX": "income_tax",
    "INTEREST_EXPENSE": "interest_expense",
    "EBIT": "ebit",
    "EBITDA": "ebitda",
    "TOTAL_ASSETS": "total_assets",
    "TOTAL_LIABILITIES": "total_liabilities",
    "TOTAL_EQUITY": "total_equity",
    "TOTAL_PARENT_EQUITY": "total_equity",
    "MONETARYFUNDS": "cash_and_equivalents",
    "TOTAL_SHARE": "shares_outstanding",
    "SHARE_CAPITAL": "shares_outstanding",
    "CURRENT_ASSETS_TOTAL": "current_assets",
    "CURRENT_LIABILITIES_TOTAL": "current_liabilities",
    "SHORT_LOAN": "short_term_debt",
    "LONG_LOAN": "long_term_debt",
    "BOND_PAYABLE": "bonds_payable",
    "NONCURRENT_LIAB_1YEAR": "current_portion_long_debt",
    "NETCASH_OPERATE": "operating_cash_flow",
    "NETCASH_INVEST": "investing_cash_flow",
    "NETCASH_FINANCE": "financing_cash_flow",
    "CONSTRUCT_LONG_ASSET": "capital_expenditure",
    "DPN_AMORTIZATION": "depreciation_amortization",
    # Includes interest paid, so it must not be read as dividends.
    "ASSIGN_DIVIDEND_PORFIT": "dividends_and_interest_paid",
    "BASIC_EPS": "basic_eps",
    "DEDUCT_BASIC_EPS": "deducted_basic_eps",
    "DILUTED_EPS": "diluted_eps",
    "ROE_WEIGHT": "roe",
    "ROEJQ": "roe",
    "ROEKCJQ": "roe_excluding_nonrecurring",
    "ROEJQTZ": "roe_adjusted",
    "BPS": "book_value_per_share",
    "NEWCAPITALADER": "capital_adequacy",
    "NET_INTEREST_MARGIN": "net_interest_margin",
    "NONPERLOAN": "non_performing_loan_ratio",
    "ROIC": "roic",
    "GROSS_MARGIN": "gross_margin",
    "NET_MARGIN": "net_margin",
    "DEBT_ASSET_RATIO": "debt_to_assets",
}


def normalize_financial_records(
    *,
    asset_id: str,
    dataset: str,
    records: list[dict[str, Any]],
    source: str,
    fetched_at: str | None = None,
) -> list[FinancialDataPoint]:
    if dataset not in FINANCIAL_DATASETS:
        raise FinancialNormalizationError(
            f"Unsupported financial dataset: {dataset}"
        )
    fetched = fetched_at or datetime.now(timezone.utc).isoformat()
    points: list[FinancialDataPoint] = []
    for row_index, record in enumerate(records):
        report_period = _required_date(
            record,
            _REPORT_PERIOD_KEYS,
            "report period",
            row_index,
        )
        published_at = _required_date(
            record,
            _PUBLISHED_AT_KEYS,
            "published date",
            row_index,
        )
        try:
            period_kind = infer_period_kind(report_period)
        except ValueError as exc:
            raise FinancialNormalizationError(str(exc)) from exc
        scope = _scope_for_dataset(dataset)
        for raw_field, raw_value in record.items():
            field_label = str(raw_field).strip()
            if field_label in _METADATA_KEYS:
                continue
            normalized = _normalize_numeric(
                raw_value,
                field_label,
                dataset,
            )
            if normalized is None:
                continue
            value, currency, unit = normalized
            points.append(
                FinancialDataPoint(
                    asset_id=asset_id,
                    dataset=dataset,
                    report_period=report_period,
                    published_at=published_at,
                    period_kind=period_kind,
                    value_scope=scope,
                    field_name=_field_name(field_label),
                    field_label=field_label,
                    normalized_value=value,
                    raw_value=str(raw_value).strip(),
                    currency=currency,
                    unit=unit,
                    source=source,
                    fetched_at=fetched,
                    raw={
                        "row_index": row_index,
                        "provider_record": dict(record),
                    },
                )
            )
    if records and not points:
        raise FinancialNormalizationError(
            "Financial records contained no numeric research fields"
        )
    return points


def _required_date(
    record: dict[str, Any],
    keys: tuple[str, ...],
    description: str,
    row_index: int,
) -> str:
    for key in keys:
        value = to_date_string(record.get(key))
        if value:
            return value
    raise FinancialNormalizationError(
        f"Financial row {row_index} requires {description}"
    )


def _scope_for_dataset(dataset: str) -> FinancialValueScope:
    if dataset == DATASET_STOCK_BALANCE_SHEETS:
        return FinancialValueScope.POINT_IN_TIME
    if dataset == DATASET_STOCK_FINANCIAL_INDICATORS:
        return FinancialValueScope.REPORTED
    if dataset in {
        DATASET_STOCK_INCOME_STATEMENTS,
        DATASET_STOCK_CASH_FLOW_STATEMENTS,
    }:
        return FinancialValueScope.CUMULATIVE
    raise FinancialNormalizationError(
        f"Unsupported financial dataset: {dataset}"
    )


def _normalize_numeric(
    raw_value: object,
    field_label: str,
    dataset: str,
) -> tuple[Decimal, str | None, str] | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if text in {"", "-", "--", "nan", "NaN", "None", "null"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    percentage_text = text.endswith("%")
    if percentage_text:
        text = text[:-1]
    multiplier = Decimal("1")
    for suffix, scale in (
        ("亿元", Decimal("100000000")),
        ("万元", Decimal("10000")),
        ("亿", Decimal("100000000")),
        ("万", Decimal("10000")),
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = scale
            break
    try:
        value = Decimal(
            text.replace(",", "").replace(" ", "")
        )
    except InvalidOperation:
        return None
    if negative:
        value = -value
    value *= multiplier

    if _is_ratio_field(field_label) or percentage_text:
        return value / Decimal("100"), None, "ratio"
    if "每股" in field_label or field_label in {
        "BASIC_EPS",
        "DEDUCT_BASIC_EPS",
        "DILUTED_EPS",
        "BPS",
    }:
        return value, "CNY", "CNY_per_share"
    if (
        any(token in field_label for token in ("股本", "股数"))
        or field_label in {"TOTAL_SHARE", "SHARE_CAPITAL"}
    ):
        return value, None, "shares"
    if dataset == DATASET_STOCK_FINANCIAL_INDICATORS:
        return value, None, "number"
    return value, "CNY", "CNY"


def _is_ratio_field(field_label: str) -> bool:
    return any(
        token in field_label
        for token in (
            "率",
            "比率",
            "占比",
            "同比",
            "增长",
            "周转率",
        )
    ) or field_label in {
        "ROE_WEIGHT",
        "GROSS_MARGIN",
        "NET_MARGIN",
        "DEBT_ASSET_RATIO",
        "ROEJQ",
        "ROEKCJQ",
        "ROEJQTZ",
        "NEWCAPITALADER",
        "NET_INTEREST_MARGIN",
        "NONPERLOAN",
        "ROIC",
    }


def canonical_field_name(field_label: str) -> str:
    """Field name for a provider label; applied on read so new mappings cover cached rows."""

    return _field_name(field_label)


def _field_name(field_label: str) -> str:
    canonical = _CANONICAL_FIELDS.get(field_label)
    if canonical is not None:
        return canonical
    digest = hashlib.sha1(
        field_label.encode("utf-8")
    ).hexdigest()[:12]
    return f"source_field:{digest}"
