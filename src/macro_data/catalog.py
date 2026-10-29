"""Macro and rate series catalog with conservative release-date rules."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ReleaseRule:
    """When a period becomes public if the source does not say.

    ``same_day``: published on the observation date (fixings, curves).
    ``business_days``: ``day`` weekdays after the observation date (holidays
    are not skipped, so a release right before a holiday may look early).
    ``next_month``: day ``day`` of the month after the period ends; set later
    than the usual official release so backtests never see data early.
    """

    kind: str
    day: int = 0
    note: str = ""

    def available_at(self, period_end: str) -> str:
        end = date.fromisoformat(period_end)
        if self.kind == "same_day":
            return end.isoformat()
        if self.kind == "business_days":
            current, remaining = end, self.day
            while remaining > 0:
                current += timedelta(days=1)
                if current.weekday() < 5:
                    remaining -= 1
            return current.isoformat()
        year = end.year + (1 if end.month == 12 else 0)
        month = 1 if end.month == 12 else end.month + 1
        day = min(self.day, calendar.monthrange(year, month)[1])
        return date(year, month, day).isoformat()


@dataclass(frozen=True)
class MacroSeriesDefinition:
    series_id: str
    name: str
    category: str
    frequency: str
    unit: str
    sources: tuple[str, ...]
    release_rule: ReleaseRule
    description: str

    def to_dict(self) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "name": self.name,
            "category": self.category,
            "frequency": self.frequency,
            "unit": self.unit,
            "sources": list(self.sources),
            "release_rule": {
                "kind": self.release_rule.kind,
                "day": self.release_rule.day,
                "note": self.release_rule.note,
            },
            "description": self.description,
        }


_NBS_PRICE = ReleaseRule("next_month", 15, "国家统计局通常次月 9–12 日发布")
_NBS_PMI = ReleaseRule("next_month", 1, "国家统计局通常当月最后一天发布")
_PBOC_MONTHLY = ReleaseRule("next_month", 20, "人民银行通常次月 10–15 日发布")
_NBS_GDP = ReleaseRule("next_month", 25, "国家统计局通常季后次月 15–20 日发布")
_SAME_DAY = ReleaseRule("same_day", 0, "当日公布")
_NEXT_BUSINESS_DAY = ReleaseRule("business_days", 1, "收盘后或次一交易日发布")

MACRO_SERIES: tuple[MacroSeriesDefinition, ...] = (
    MacroSeriesDefinition(
        "cpi_yoy", "全国居民消费价格指数（CPI）当月同比", "通胀", "monthly", "%",
        ("akshare", "nbs"), _NBS_PRICE,
        "国家统计局月度数据；统计局发布页只提供最新一期及其发布日期。",
    ),
    MacroSeriesDefinition(
        "ppi_yoy", "工业生产者出厂价格指数（PPI）当月同比", "通胀", "monthly", "%",
        ("akshare",), _NBS_PRICE, "国家统计局月度数据。",
    ),
    MacroSeriesDefinition(
        "pmi_manufacturing", "制造业采购经理指数（PMI）", "增长", "monthly", "指数",
        ("akshare",), _NBS_PMI, "50 为荣枯线。",
    ),
    MacroSeriesDefinition(
        "pmi_non_manufacturing", "非制造业商务活动指数", "增长", "monthly", "指数",
        ("akshare",), _NBS_PMI, "50 为荣枯线。",
    ),
    MacroSeriesDefinition(
        "gdp_yoy_ytd", "国内生产总值累计同比（不变价）", "增长", "quarterly", "%",
        ("akshare",), _NBS_GDP, "季度累计口径：Q3 表示前三季度。",
    ),
    MacroSeriesDefinition(
        "m2_yoy", "广义货币 M2 同比", "流动性", "monthly", "%",
        ("akshare",), _PBOC_MONTHLY, "月末余额同比。",
    ),
    MacroSeriesDefinition(
        "m1_yoy", "狭义货币 M1 同比", "流动性", "monthly", "%",
        ("akshare",), _PBOC_MONTHLY, "月末余额同比；2025 年起口径含个人活期与非银支付机构备付金。",
    ),
    MacroSeriesDefinition(
        "new_rmb_loans", "新增人民币贷款（当月）", "信用", "monthly", "亿元",
        ("akshare",), _PBOC_MONTHLY, "当月新增额，季节性强，比较时用同比。",
    ),
    MacroSeriesDefinition(
        "social_financing_flow", "社会融资规模增量（当月）", "信用", "monthly", "亿元",
        ("akshare",), _PBOC_MONTHLY, "当月增量；当前来源更新明显滞后，使用前看最新期。",
    ),
    MacroSeriesDefinition(
        "lpr_1y", "贷款市场报价利率（1 年期）", "利率", "event", "%",
        ("chinamoney", "akshare"), _SAME_DAY, "每月 20 日（节假日顺延）公布。",
    ),
    MacroSeriesDefinition(
        "lpr_5y", "贷款市场报价利率（5 年期以上）", "利率", "event", "%",
        ("chinamoney", "akshare"), _SAME_DAY, "每月 20 日（节假日顺延）公布。",
    ),
    MacroSeriesDefinition(
        "shibor_on", "上海银行间同业拆放利率（隔夜）", "流动性", "daily", "%",
        ("chinamoney",), _SAME_DAY, "交易日 11:00 公布。",
    ),
    MacroSeriesDefinition(
        "shibor_1w", "上海银行间同业拆放利率（1 周）", "流动性", "daily", "%",
        ("chinamoney",), _SAME_DAY, "交易日 11:00 公布。",
    ),
    MacroSeriesDefinition(
        "shibor_3m", "上海银行间同业拆放利率（3 个月）", "流动性", "daily", "%",
        ("chinamoney",), _SAME_DAY, "交易日 11:00 公布。",
    ),
    MacroSeriesDefinition(
        "cgb_1y", "中债国债到期收益率（1 年）", "利率", "daily", "%",
        ("akshare",), _SAME_DAY, "中债国债收益率曲线，收盘后发布；每次只取最近一年。",
    ),
    MacroSeriesDefinition(
        "cgb_10y", "中债国债到期收益率（10 年）", "利率", "daily", "%",
        ("akshare_em", "akshare"), _SAME_DAY,
        "东方财富中美国债收益率表与中债国债收益率曲线互相核对；前者一次取全历史。",
    ),
    MacroSeriesDefinition(
        "csi300_pe_ttm", "沪深300 滚动市盈率（市值加权）", "市场", "monthly", "倍",
        ("akshare",), _NEXT_BUSINESS_DAY,
        "乐咕乐股计算；历史为月末值，最新一期为最近交易日。",
    ),
    MacroSeriesDefinition(
        "csi300_pb", "沪深300 市净率（市值加权）", "市场", "monthly", "倍",
        ("akshare",), _NEXT_BUSINESS_DAY,
        "乐咕乐股计算；历史为月末值，最新一期为最近交易日。",
    ),
    MacroSeriesDefinition(
        "csi500_pe_ttm", "中证500 滚动市盈率（市值加权）", "市场", "monthly", "倍",
        ("akshare",), _NEXT_BUSINESS_DAY,
        "乐咕乐股计算；历史为月末值，最新一期为最近交易日。",
    ),
    *(
        MacroSeriesDefinition(
            series_id, f"{index_name} 滚动市盈率（市值加权）", "市场", "monthly", "倍",
            ("akshare",), _NEXT_BUSINESS_DAY,
            "乐咕乐股计算；历史为月末值，最新一期为最近交易日。供场外基金底层估值位置使用。",
        )
        for series_id, index_name in (
            ("csi1000_pe_ttm", "中证1000"),
            ("csi800_pe_ttm", "中证800"),
            ("csi100_pe_ttm", "中证100"),
            ("sse50_pe_ttm", "上证50"),
            ("sse180_pe_ttm", "上证180"),
            ("sse380_pe_ttm", "上证380"),
            ("chinext50_pe_ttm", "创业板50"),
            ("szse100_pe_ttm", "深证100"),
            ("szse_dividend_pe_ttm", "深证红利"),
            ("sse_dividend_pe_ttm", "上证红利"),
        )
    ),
    MacroSeriesDefinition(
        "new_high_60", "全 A 股创 60 日新高家数", "市场", "daily", "家",
        ("akshare",), _NEXT_BUSINESS_DAY, "乐咕乐股；来源只保留约两年历史。",
    ),
    MacroSeriesDefinition(
        "new_low_60", "全 A 股创 60 日新低家数", "市场", "daily", "家",
        ("akshare",), _NEXT_BUSINESS_DAY, "乐咕乐股；来源只保留约两年历史。",
    ),
    MacroSeriesDefinition(
        "margin_balance_total", "沪深两市融资融券余额", "市场", "daily", "亿元",
        ("akshare",), _NEXT_BUSINESS_DAY,
        "沪深交易所次一交易日公布；只合计两所都有数据的日期。",
    ),
)

_BY_ID = {item.series_id: item for item in MACRO_SERIES}


def macro_series(series_id: str) -> MacroSeriesDefinition:
    try:
        return _BY_ID[series_id]
    except KeyError:
        raise ValueError(
            f"Unknown macro series {series_id}; known: {', '.join(_BY_ID)}"
        ) from None
