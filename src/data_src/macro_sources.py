"""Macro and rate series from each source, normalized to MacroObservation."""

from __future__ import annotations

import calendar
import re
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Callable

from .akshare import AkShareDataSource
from .chinamoney import ChinaMoneyDataSource
from .common import DataSourceError, to_date_string, to_optional_float
from .models import MacroObservation
from .nbs import NBSMacroDataSource

# series_id -> (AkShare API, period column, value column, unit)
_AKSHARE_PERIODIC = {
    "cpi_yoy": ("macro_china_cpi", "月份", "全国-同比增长", "%"),
    "ppi_yoy": ("macro_china_ppi", "月份", "当月同比增长", "%"),
    "pmi_manufacturing": ("macro_china_pmi", "月份", "制造业-指数", "指数"),
    "pmi_non_manufacturing": ("macro_china_pmi", "月份", "非制造业-指数", "指数"),
    "m2_yoy": (
        "macro_china_money_supply",
        "月份",
        "货币和准货币(M2)-同比增长",
        "%",
    ),
    "m1_yoy": ("macro_china_money_supply", "月份", "货币(M1)-同比增长", "%"),
    "new_rmb_loans": ("macro_china_new_financial_credit", "月份", "当月", "亿元"),
    "social_financing_flow": (
        "macro_china_shrzgm",
        "月份",
        "社会融资规模增量",
        "亿元",
    ),
    "gdp_yoy_ytd": ("macro_china_gdp", "季度", "国内生产总值-同比增长", "%"),
}
_LPR_COLUMNS = {"lpr_1y": ("LPR1Y", "1Y"), "lpr_5y": ("LPR5Y", "5Y")}
_SHIBOR_COLUMNS = {"shibor_on": "ON", "shibor_1w": "1W", "shibor_3m": "3M"}
_CGB_COLUMNS = {"cgb_1y": "1年", "cgb_10y": "10年"}
_CGB_CURVE = "中债国债收益率曲线"
_EM_YIELD_COLUMNS = {"cgb_10y": "中国国债收益率10年"}
# series_id -> (AkShare API, Legu index name, value column, unit)
_LEGU_INDEX = {
    "csi300_pe_ttm": ("stock_index_pe_lg", "沪深300", "滚动市盈率", "倍"),
    "csi300_pb": ("stock_index_pb_lg", "沪深300", "市净率", "倍"),
    "csi500_pe_ttm": ("stock_index_pe_lg", "中证500", "滚动市盈率", "倍"),
}
_HIGH_LOW_COLUMNS = {"new_high_60": "high60", "new_low_60": "low60"}


def fetch_macro_observations(
    *,
    source: str,
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    """Observations whose period ends inside [start_date, end_date]."""

    fetcher = _fetcher(source, series_id)
    if fetcher is None:
        raise DataSourceError(f"{source} does not provide {series_id}")
    return [
        item
        for item in fetcher(series_id, start_date, end_date)
        if start_date[:10] <= item.period_end <= end_date[:10]
    ]


def supported_sources(series_id: str) -> tuple[str, ...]:
    return tuple(
        source
        for source in ("akshare", "akshare_em", "chinamoney", "nbs")
        if _fetcher(source, series_id) is not None
    )


def _fetcher(
    source: str,
    series_id: str,
) -> Callable[[str, str, str], list[MacroObservation]] | None:
    if source == "akshare":
        if series_id in _AKSHARE_PERIODIC:
            return _akshare_periodic
        if series_id in _LPR_COLUMNS:
            return _akshare_lpr
        if series_id in _CGB_COLUMNS:
            return _akshare_cgb
        if series_id in _LEGU_INDEX:
            return _akshare_legu_index
        if series_id in _HIGH_LOW_COLUMNS:
            return _akshare_high_low
        if series_id == "margin_balance_total":
            return _akshare_margin_total
    if source == "akshare_em" and series_id in _EM_YIELD_COLUMNS:
        return _akshare_em_yield
    if source == "chinamoney":
        if series_id in _LPR_COLUMNS:
            return _chinamoney_lpr
        if series_id in _SHIBOR_COLUMNS:
            return _chinamoney_shibor
    if source == "nbs" and series_id == "cpi_yoy":
        return _nbs_cpi
    return None


def _akshare_periodic(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    api, period_column, value_column, unit = _AKSHARE_PERIODIC[series_id]
    output = []
    for record in AkShareDataSource().call_api(api):
        period = _period(str(record.get(period_column) or ""))
        value = to_optional_float(record.get(value_column))
        if period is None or value is None:
            continue
        output.append(
            MacroObservation(
                series_id=series_id,
                period=period,
                period_end=_period_end(period),
                value=value,
                unit=unit,
                source=f"akshare:{api}",
                raw=_plain(record),
            )
        )
    return output


def _akshare_lpr(series_id: str, start_date: str, end_date: str) -> list[MacroObservation]:
    column = _LPR_COLUMNS[series_id][0]
    output = []
    for record in AkShareDataSource().call_api("macro_china_lpr"):
        day = to_date_string(record.get("TRADE_DATE"))
        value = to_optional_float(record.get(column))
        if not day or value is None:
            continue
        output.append(
            MacroObservation(
                series_id=series_id,
                period=day,
                period_end=day,
                value=value,
                unit="%",
                source="akshare:macro_china_lpr",
                published_at=day,
                raw=_plain(record),
            )
        )
    return output


def _akshare_cgb(series_id: str, start_date: str, end_date: str) -> list[MacroObservation]:
    column = _CGB_COLUMNS[series_id]
    source = AkShareDataSource()
    output = []
    start = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    # One call per year is slow; ChinaBond only cross-checks the latest year.
    start = max(start, end - timedelta(days=366))
    # The ChinaBond history query accepts at most one year per call.
    while start <= end:
        window_end = min(end, start + timedelta(days=360))
        records = source.call_api(
            "bond_china_yield",
            start_date=start.strftime("%Y%m%d"),
            end_date=window_end.strftime("%Y%m%d"),
        )
        for record in records:
            if str(record.get("曲线名称") or "") != _CGB_CURVE:
                continue
            day = to_date_string(record.get("日期"))
            value = to_optional_float(record.get(column))
            if not day or value is None:
                continue
            output.append(
                MacroObservation(
                    series_id=series_id,
                    period=day,
                    period_end=day,
                    value=value,
                    unit="%",
                    source="akshare:bond_china_yield",
                    published_at=day,
                    raw=_plain(record),
                )
            )
        start = window_end + timedelta(days=1)
    return output


def _akshare_legu_index(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    api, index_name, column, unit = _LEGU_INDEX[series_id]
    return _dated_rows(
        series_id,
        AkShareDataSource().call_api(api, symbol=index_name),
        date_column="日期",
        value=lambda record: to_optional_float(record.get(column)),
        unit=unit,
        source=f"akshare:{api}",
    )


def _akshare_high_low(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    column = _HIGH_LOW_COLUMNS[series_id]
    return _dated_rows(
        series_id,
        AkShareDataSource().call_api("stock_a_high_low_statistics", symbol="all"),
        date_column="date",
        value=lambda record: to_optional_float(record.get(column)),
        unit="家",
        source="akshare:stock_a_high_low_statistics",
    )


def _akshare_margin_total(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    source = AkShareDataSource()
    totals: dict[str, list[float]] = {}
    for api in ("macro_china_market_margin_sh", "macro_china_market_margin_sz"):
        for record in source.call_api(api):
            day = to_date_string(record.get("日期"))
            value = to_optional_float(record.get("融资融券余额"))
            if day and value is not None:
                totals.setdefault(day, []).append(value)
    # Only dates both exchanges reported; values are in yuan.
    return [
        MacroObservation(
            series_id=series_id,
            period=day,
            period_end=day,
            value=sum(values) / 1e8,
            unit="亿元",
            source="akshare:macro_china_market_margin_sh|sz",
            raw={"exchange_count": len(values)},
        )
        for day, values in sorted(totals.items())
        if len(values) == 2
    ]


def _akshare_em_yield(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    column = _EM_YIELD_COLUMNS[series_id]
    rows = AkShareDataSource().call_api(
        "bond_zh_us_rate",
        start_date=start_date[:10].replace("-", ""),
    )
    return [
        replace(item, published_at=item.period)
        for item in _dated_rows(
            series_id,
            rows,
            date_column="日期",
            value=lambda record: to_optional_float(record.get(column)),
            unit="%",
            source="akshare_em:bond_zh_us_rate",
        )
    ]


def _dated_rows(
    series_id: str,
    records: list[dict[str, Any]],
    *,
    date_column: str,
    value: Callable[[dict[str, Any]], float | None],
    unit: str,
    source: str,
) -> list[MacroObservation]:
    output = []
    for record in records:
        day = to_date_string(record.get(date_column))
        number = value(record)
        if not day or number is None:
            continue
        output.append(
            MacroObservation(
                series_id=series_id,
                period=day,
                period_end=day,
                value=number,
                unit=unit,
                source=source,
                raw=_plain(record),
            )
        )
    return output


def _chinamoney_lpr(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    key = _LPR_COLUMNS[series_id][1]
    return _chinamoney_records(
        series_id,
        ChinaMoneyDataSource().get_lpr_history(start_date, end_date),
        key,
        "chinamoney:LprHis",
    )


def _chinamoney_shibor(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[MacroObservation]:
    return _chinamoney_records(
        series_id,
        ChinaMoneyDataSource().get_shibor_history(start_date, end_date),
        _SHIBOR_COLUMNS[series_id],
        "chinamoney:ShiborHis",
    )


def _chinamoney_records(
    series_id: str,
    records: list[dict[str, Any]],
    key: str,
    source: str,
) -> list[MacroObservation]:
    output = []
    for record in records:
        day = str(record.get("showDateCN") or "")[:10]
        value = to_optional_float(record.get(key))
        if not day or value is None:
            continue
        output.append(
            MacroObservation(
                series_id=series_id,
                period=day,
                period_end=day,
                value=value,
                unit="%",
                source=source,
                published_at=day,
                raw=dict(record),
            )
        )
    return output


def _nbs_cpi(series_id: str, start_date: str, end_date: str) -> list[MacroObservation]:
    latest = NBSMacroDataSource().get_latest_cpi_yoy()
    period = str(latest["reference_period"])
    return [
        MacroObservation(
            series_id=series_id,
            period=period,
            period_end=_period_end(period),
            value=float(latest["cpi_yoy_percent"]),
            unit="%",
            source=str(latest["source"]),
            published_at=str(latest["available_at"]),
            raw=dict(latest),
        )
    ]


def _period(text: str) -> str | None:
    """Monthly "2026年09月份"/"202604" to "2026-09"; quarterly "2026年第1-3季度" to "2026-Q3"."""

    quarter = re.match(r"(\d{4})年第(?:\d-)?(\d)季度", text)
    if quarter:
        return f"{quarter.group(1)}-Q{quarter.group(2)}"
    month = re.match(r"(\d{4})年(\d{1,2})月", text) or re.fullmatch(
        r"(\d{4})(\d{2})",
        text,
    )
    if month:
        return f"{month.group(1)}-{int(month.group(2)):02d}"
    return None


def _period_end(period: str) -> str:
    year = int(period[:4])
    if "-Q" in period:
        month = int(period[-1]) * 3
    else:
        month = int(period[5:7])
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _plain(record: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): (value if isinstance(value, (str, int, float)) or value is None else str(value))
        for key, value in record.items()
    }
