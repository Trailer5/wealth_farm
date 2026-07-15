"""Validate live external data access functions one by one.

This script is intentionally outside unit tests and CI. It calls public
network data sources, expects at least one real record from each check, and
prints JSON that can be copied into development tracking documents.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMP_ROOT = PROJECT_ROOT / "temp" / "real_data_validation"


class ValidationContext:
    """Shared provider instances and cached upstream results."""

    def __init__(self, include_downloads: bool) -> None:
        from src.data_src.akshare import AkShareDataSource
        from src.data_src.baostock import BaoStockDataSource
        from src.data_src.bse import BseDisclosureDataSource
        from src.data_src.cninfo import CninfoDataSource
        from src.data_src.csrc import CsrcPublicInfoDataSource
        from src.data_src.cs_fund import CsFundDisclosureDataSource
        from src.data_src.eastmoney import EastmoneyExchangeFundDataSource, EastmoneyResearchReportDataSource
        from src.data_src.fund_company import FundCompanyDisclosureDataSource
        from src.data_src.sina import SinaExchangeFundDataSource
        from src.data_src.sse import SseDisclosureDataSource
        from src.data_src.szse import SzseDisclosureDataSource
        from src.data_src.tencent import TencentExchangeFundDataSource

        self.include_downloads = include_downloads
        self.akshare = AkShareDataSource()
        self.baostock = BaoStockDataSource()
        self.bse = BseDisclosureDataSource()
        self.cninfo = CninfoDataSource()
        self.csrc = CsrcPublicInfoDataSource()
        self.cs_fund = CsFundDisclosureDataSource()
        self.eastmoney = EastmoneyExchangeFundDataSource()
        self.eastmoney_research = EastmoneyResearchReportDataSource()
        self.fund_company = FundCompanyDisclosureDataSource()
        self.sina = SinaExchangeFundDataSource()
        self.sse = SseDisclosureDataSource()
        self.szse = SzseDisclosureDataSource()
        self.tencent = TencentExchangeFundDataSource()
        self.cache: dict[str, Any] = {}

    def close(self) -> None:
        self.baostock.logout()


class ValidationCase:
    def __init__(
        self,
        group: str,
        name: str,
        function_name: str,
        call: Callable[[ValidationContext], Any],
    ) -> None:
        self.group = group
        self.name = name
        self.function_name = function_name
        self.call = call


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live validation checks for all external data access functions.")
    parser.add_argument(
        "--groups",
        nargs="*",
        default=[],
        help="Optional group filters, e.g. akshare baostock disclosure.",
    )
    parser.add_argument(
        "--functions",
        nargs="*",
        default=[],
        help="Optional substring filters matched against case names and function names.",
    )
    parser.add_argument(
        "--exclude-functions",
        nargs="*",
        default=[],
        help="Optional substring filters for functions that should be skipped.",
    )
    parser.add_argument(
        "--skip-downloads",
        action="store_true",
        help="Skip PDF download checks and only validate metadata endpoints.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between checks to avoid hammering public endpoints.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)

    context = ValidationContext(include_downloads=not args.skip_downloads)
    try:
        selected_groups = {group.lower() for group in args.groups}
        selected_functions = [item.lower() for item in args.functions]
        excluded_functions = [item.lower() for item in args.exclude_functions]
        cases = [
            case
            for case in build_cases()
            if _case_selected(case, selected_groups, selected_functions, excluded_functions)
        ]
        results = []
        for case in cases:
            print(f"[real-validation] START {case.group}::{case.function_name}", file=sys.stderr, flush=True)
            result = run_case(case, context)
            print(
                f"[real-validation] END {case.group}::{case.function_name} ok={result['ok']} count={result['count']}",
                file=sys.stderr,
                flush=True,
            )
            results.append(result)
            if args.sleep:
                time.sleep(args.sleep)
    finally:
        context.close()

    summary = {
        "total": len(results),
        "ok": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
    }
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=True, indent=2))
    return 0 if summary["failed"] == 0 else 1


def _case_selected(
    case: ValidationCase,
    selected_groups: set[str],
    selected_functions: list[str],
    excluded_functions: list[str],
) -> bool:
    text = f"{case.name} {case.function_name}".lower()
    if selected_groups and case.group.lower() not in selected_groups:
        return False
    if selected_functions and not any(item in text for item in selected_functions):
        return False
    return not any(item in text for item in excluded_functions)


def build_cases() -> list[ValidationCase]:
    return [
        ValidationCase("akshare", "A股日线", "AkShareDataSource.get_a_share_daily_bars", lambda c: c.akshare.get_a_share_daily_bars("600519", "2025-01-01", "2025-01-31")),
        ValidationCase("akshare", "A股历史周线", "AkShareDataSource.get_a_share_history", lambda c: c.akshare.get_a_share_history("600519", "2025-01-01", "2025-06-30", period="weekly")),
        ValidationCase("akshare", "A股实时行情", "AkShareDataSource.get_a_share_spot", lambda c: c.akshare.get_a_share_spot()),
        ValidationCase("akshare", "A股分钟线", "AkShareDataSource.get_a_share_minute_bars", lambda c: c.akshare.get_a_share_minute_bars("600519", "2025-01-02 09:30:00", "2025-01-02 15:00:00")),
        ValidationCase("akshare", "ETF实时行情", "AkShareDataSource.get_etf_spot", lambda c: c.akshare.get_etf_spot()),
        ValidationCase("akshare", "ETF日线", "AkShareDataSource.get_etf_daily_bars", lambda c: c.akshare.get_etf_daily_bars("510300", "2025-01-01", "2025-01-31")),
        ValidationCase("akshare", "开放式基金最新净值", "AkShareDataSource.get_open_fund_daily_navs", lambda c: c.akshare.get_open_fund_daily_navs()),
        ValidationCase("akshare", "基金名称表", "AkShareDataSource.get_fund_names", lambda c: c.akshare.get_fund_names()),
        ValidationCase("akshare", "基金申购状态原始表", "AkShareDataSource.get_fund_purchase_status", lambda c: c.akshare.get_fund_purchase_status()),
        ValidationCase("akshare", "基金申购状态归一记录", "AkShareDataSource.get_fund_purchase_status_records", lambda c: c.akshare.get_fund_purchase_status_records()),
        ValidationCase("akshare", "开放式基金历史净值原始表", "AkShareDataSource.get_open_fund_nav_history", lambda c: c.akshare.get_open_fund_nav_history("000001")),
        ValidationCase("akshare", "开放式基金历史净值归一记录", "AkShareDataSource.get_open_fund_nav_history_records", lambda c: c.akshare.get_open_fund_nav_history_records("000001")),
        ValidationCase("akshare", "货币基金最新净值", "AkShareDataSource.get_money_fund_daily_navs", lambda c: c.akshare.get_money_fund_daily_navs()),
        ValidationCase("akshare", "基金估值", "AkShareDataSource.get_fund_value_estimates", lambda c: c.akshare.get_fund_value_estimates()),
        ValidationCase("akshare", "基金规模", "AkShareDataSource.get_fund_scale_records", lambda c: c.akshare.get_fund_scale_records("股票型基金")),
        ValidationCase("akshare", "基金股票持仓", "AkShareDataSource.get_fund_stock_holdings", lambda c: c.akshare.get_fund_stock_holdings("000001", "2024")),
        ValidationCase("akshare", "基金债券持仓", "AkShareDataSource.get_fund_bond_holdings", lambda c: c.akshare.get_fund_bond_holdings("000001", "2024")),
        ValidationCase("akshare", "基金行业配置", "AkShareDataSource.get_fund_industry_allocation", lambda c: c.akshare.get_fund_industry_allocation("000001", "2024")),
        ValidationCase("akshare", "CNINFO基金资产配置", "AkShareDataSource.get_fund_asset_allocation_cninfo", lambda c: c.akshare.get_fund_asset_allocation_cninfo(date="2024")),
        ValidationCase("akshare", "CNINFO基金行业配置", "AkShareDataSource.get_fund_industry_allocation_cninfo", lambda c: c.akshare.get_fund_industry_allocation_cninfo(date="2024")),
        ValidationCase("akshare", "利润表", "AkShareDataSource.get_stock_profit_sheet", lambda c: c.akshare.get_stock_profit_sheet("600519")),
        ValidationCase("akshare", "资产负债表", "AkShareDataSource.get_stock_balance_sheet", lambda c: c.akshare.get_stock_balance_sheet("600519")),
        ValidationCase("akshare", "现金流量表", "AkShareDataSource.get_stock_cash_flow_sheet", lambda c: c.akshare.get_stock_cash_flow_sheet("600519")),
        ValidationCase("akshare", "财务指标", "AkShareDataSource.get_stock_financial_indicators", lambda c: c.akshare.get_stock_financial_indicators("600519")),
        ValidationCase("akshare", "财报披露计划", "AkShareDataSource.get_stock_report_disclosure", lambda c: c.akshare.get_stock_report_disclosure()),
        ValidationCase("akshare", "行业板块列表", "AkShareDataSource.get_industry_board_names", lambda c: c.akshare.get_industry_board_names()),
        ValidationCase("akshare", "行业板块成分", "AkShareDataSource.get_industry_board_constituents", lambda c: c.akshare.get_industry_board_constituents(_first_board_name(c, "industry"))),
        ValidationCase("akshare", "概念板块列表", "AkShareDataSource.get_concept_board_names", lambda c: c.akshare.get_concept_board_names()),
        ValidationCase("akshare", "概念板块成分", "AkShareDataSource.get_concept_board_constituents", lambda c: c.akshare.get_concept_board_constituents(_first_board_name(c, "concept"))),
        ValidationCase("akshare", "通用API调用", "AkShareDataSource.call_api", lambda c: c.akshare.call_api("macro_china_gdp")),
        ValidationCase("baostock", "登录", "BaoStockDataSource.login", lambda c: _call_login(c)),
        ValidationCase("baostock", "A股日线", "BaoStockDataSource.get_a_share_daily_bars", lambda c: c.baostock.get_a_share_daily_bars("600519", "2025-01-01", "2025-01-31")),
        ValidationCase("baostock", "历史K线原始记录", "BaoStockDataSource.get_history_records", lambda c: c.baostock.get_history_records("600519", "2025-01-01", "2025-01-31")),
        ValidationCase("baostock", "交易日历", "BaoStockDataSource.get_trade_dates", lambda c: c.baostock.get_trade_dates("2025-01-01", "2025-01-31")),
        ValidationCase("baostock", "全市场股票列表", "BaoStockDataSource.get_all_stocks", lambda c: c.baostock.get_all_stocks("2025-01-02")),
        ValidationCase("baostock", "股票基础信息原始记录", "BaoStockDataSource.get_stock_basic", lambda c: c.baostock.get_stock_basic("600519")),
        ValidationCase("baostock", "股票基础信息归一记录", "BaoStockDataSource.get_stock_basic_records", lambda c: c.baostock.get_stock_basic_records("600519")),
        ValidationCase("baostock", "盈利能力", "BaoStockDataSource.get_profit_data", lambda c: c.baostock.get_profit_data("600519", 2024, 4)),
        ValidationCase("baostock", "营运能力", "BaoStockDataSource.get_operation_data", lambda c: c.baostock.get_operation_data("600519", 2024, 4)),
        ValidationCase("baostock", "成长能力", "BaoStockDataSource.get_growth_data", lambda c: c.baostock.get_growth_data("600519", 2024, 4)),
        ValidationCase("baostock", "偿债能力", "BaoStockDataSource.get_balance_data", lambda c: c.baostock.get_balance_data("600519", 2024, 4)),
        ValidationCase("baostock", "现金流量", "BaoStockDataSource.get_cash_flow_data", lambda c: c.baostock.get_cash_flow_data("600519", 2024, 4)),
        ValidationCase("baostock", "杜邦分析", "BaoStockDataSource.get_dupont_data", lambda c: c.baostock.get_dupont_data("600519", 2024, 4)),
        ValidationCase("exchange_fund", "东方财富场内基金实时", "EastmoneyExchangeFundDataSource.get_exchange_fund_spot", lambda c: c.eastmoney.get_exchange_fund_spot()),
        ValidationCase("exchange_fund", "腾讯场内基金实时", "TencentExchangeFundDataSource.get_exchange_fund_spot", lambda c: c.tencent.get_exchange_fund_spot(["510300", "159915", "160706"])),
        ValidationCase("exchange_fund", "新浪场内基金实时", "SinaExchangeFundDataSource.get_exchange_fund_spot", lambda c: c.sina.get_exchange_fund_spot(["510300", "159915", "160706"])),
        ValidationCase("disclosure", "CNINFO证券映射", "CninfoDataSource.search_securities", lambda c: c.cninfo.search_securities("贵州茅台")),
        ValidationCase("disclosure", "CNINFO公告查询", "CninfoDataSource.search_announcements", lambda c: _cninfo_documents(c)),
        ValidationCase("disclosure", "CNINFO公告PDF下载", "CninfoDataSource.download_pdf", lambda c: _download_cninfo_pdf(c)),
        ValidationCase("disclosure", "上交所公告查询", "SseDisclosureDataSource.search_announcements", lambda c: c.sse.search_announcements("600519", "2025-01-01", "2026-07-15")),
        ValidationCase("disclosure", "深交所公告查询", "SzseDisclosureDataSource.search_announcements", lambda c: c.szse.search_announcements("000001", "2025-01-01", "2026-07-15")),
        ValidationCase("disclosure", "北交所公告查询", "BseDisclosureDataSource.search_announcements", lambda c: c.bse.search_announcements("430047", "2025-01-01", "2026-07-15")),
        ValidationCase("disclosure", "中证基金公告", "CsFundDisclosureDataSource.search_announcements", lambda c: c.cs_fund.search_announcements(report_type="latest")),
        ValidationCase("disclosure", "基金公司公告页解析", "FundCompanyDisclosureDataSource.search_announcements", lambda c: c.fund_company.search_announcements(["https://www.efunds.com.cn/lm/jgfw/xxpl/"], keyword="公告")),
        ValidationCase("disclosure", "证监会公开网页解析", "CsrcPublicInfoDataSource.search_public_documents", lambda c: c.csrc.search_public_documents(["https://www.csrc.gov.cn/"], keyword="行政许可")),
        ValidationCase("research", "东方财富个股研报", "EastmoneyResearchReportDataSource.search_stock_reports", lambda c: _research_reports(c)),
        ValidationCase("research", "东方财富研报PDF下载", "EastmoneyResearchReportDataSource.download_pdf", lambda c: _download_research_pdf(c)),
    ]


def run_case(case: ValidationCase, context: ValidationContext) -> dict[str, Any]:
    try:
        value = case.call(context)
        count = _count_records(value)
        file_error = _download_file_error(case, value)
        ok = count > 0 and file_error is None
        return {
            "group": case.group,
            "name": case.name,
            "function": case.function_name,
            "ok": ok,
            "count": count,
            "sample": _sample(value),
            "error": None if ok else file_error or "returned empty result",
        }
    except Exception as exc:
        return {
            "group": case.group,
            "name": case.name,
            "function": case.function_name,
            "ok": False,
            "count": 0,
            "sample": None,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=2),
        }


def _call_login(context: ValidationContext) -> list[dict[str, str]]:
    context.baostock.login()
    return [{"status": "logged_in"}]


def _first_board_name(context: ValidationContext, board_type: str) -> str:
    cache_key = f"{board_type}_board_name"
    if cache_key in context.cache:
        return context.cache[cache_key]
    if board_type == "industry":
        rows = context.akshare.get_industry_board_names()
    else:
        rows = context.akshare.get_concept_board_names()
    name = str((rows[0] or {}).get("板块名称") or (rows[0] or {}).get("名称") or "")
    if not name:
        raise ValueError(f"cannot resolve first {board_type} board name")
    context.cache[cache_key] = name
    return name


def _cninfo_documents(context: ValidationContext) -> Any:
    if "cninfo_documents" not in context.cache:
        context.cache["cninfo_documents"] = context.cninfo.search_announcements(
            "600519",
            "2025-01-01",
            "2026-07-15",
            category=context.cninfo.CATEGORY_ANNUAL_REPORT,
        )
    return context.cache["cninfo_documents"]


def _download_cninfo_pdf(context: ValidationContext) -> Any:
    if not context.include_downloads:
        return [{"status": "skipped_by_flag"}]
    documents = _cninfo_documents(context)
    if not documents:
        return []
    return [context.cninfo.download_pdf(documents[0], root_dir=TEMP_ROOT / "disclosures")]


def _research_reports(context: ValidationContext) -> Any:
    if "research_reports" not in context.cache:
        context.cache["research_reports"] = context.eastmoney_research.search_stock_reports(
            "600519",
            "2025-01-01",
            "2026-07-15",
            page_size=20,
        )
    return context.cache["research_reports"]


def _download_research_pdf(context: ValidationContext) -> Any:
    if not context.include_downloads:
        return [{"status": "skipped_by_flag"}]
    reports = _research_reports(context)
    if not reports:
        return []
    return [context.eastmoney_research.download_pdf(reports[0], root_dir=TEMP_ROOT / "research_reports")]


def _count_records(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict, str, bytes)):
        return len(value)
    return 1


def _download_file_error(case: ValidationCase, value: Any) -> str | None:
    if "download_pdf" not in case.function_name:
        return None
    files = value if isinstance(value, list) else [value]
    for file in files:
        file_path = getattr(file, "file_path", None)
        if file_path is None and isinstance(file, dict):
            file_path = file.get("file_path")
        if not file_path:
            return "download did not return a file path"
        path = Path(str(file_path))
        if not path.exists():
            return f"downloaded file path does not exist: {path}"
        if path.read_bytes()[:4] != b"%PDF":
            return "downloaded file is not a PDF"
    return None


def _sample(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in list(value)[:2]]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in list(value.items())[:12]}
    return _to_jsonable(value)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _compact_dict(asdict(value))
    if isinstance(value, dict):
        return _compact_dict(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in list(value)[:5]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    preferred_keys = [
        "symbol",
        "name",
        "trade_date",
        "nav_date",
        "title",
        "announcement_date",
        "publish_date",
        "source",
        "file_url",
        "file_path",
        "sha256",
        "size_bytes",
        "latest",
        "close",
    ]
    compact: dict[str, Any] = {}
    for key in preferred_keys:
        if key in value:
            compact[key] = _to_jsonable(value[key])
    if compact:
        return compact
    for key, item in list(value.items())[:12]:
        if key == "raw":
            continue
        compact[str(key)] = _to_jsonable(item)
    return compact


if __name__ == "__main__":
    raise SystemExit(main())
