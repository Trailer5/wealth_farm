"""Valuation position of the index an open fund tracks or is benchmarked against.

Design notes: docs/modules/3-分析工具/基金与ETF研究数据模块.md「场外基金时点与仓位事实」.
"""

from __future__ import annotations

import re
from typing import Any

from src.macro_data import MacroDataService
from src.market_analysis.archive import historical_percentile

VALUATION_YEARS = 10
# (name as written in benchmarks, PE series, PB series); Legulegu publishes long history only for these broad indices.
INDEX_VALUATION_SERIES = (
    ("沪深300", "csi300_pe_ttm", "csi300_pb"),
    ("中证500", "csi500_pe_ttm", None),
    ("中证1000", "csi1000_pe_ttm", None),
    ("中证800", "csi800_pe_ttm", None),
    ("中证100", "csi100_pe_ttm", None),
    ("上证50", "sse50_pe_ttm", None),
    ("上证180", "sse180_pe_ttm", None),
    ("上证380", "sse380_pe_ttm", None),
    ("创业板50", "chinext50_pe_ttm", None),
    ("深证100", "szse100_pe_ttm", None),
    ("深证红利", "szse_dividend_pe_ttm", None),
    ("上证红利", "sse_dividend_pe_ttm", None),
)
_COMPONENT = re.compile(r"(.+?)(?:收益率)?[*×](\d+(?:\.\d+)?)%")
_BOND_OR_CASH = ("债", "存款", "利率", "货币")


def benchmark_components(text: str | None) -> list[tuple[str, float]]:
    normalized = re.sub(r"\s+", "", text or "").replace("＋", "+").replace("＊", "*").replace("（", "(").replace("）", ")")
    return [(match.group(1).lstrip("+"), float(match.group(2)) / 100) for match in _COMPONENT.finditer(normalized)]


def match_index(label: str) -> tuple[str, str, str | None] | None:
    for entry in sorted(INDEX_VALUATION_SERIES, key=lambda item: len(item[0]), reverse=True):
        position = label.find(entry[0])
        end = position + len(entry[0])
        if position >= 0 and not (end < len(label) and label[end].isdigit()):
            return entry
    return None


class UnderlyingValuationService:
    def __init__(self, *, macro_data: MacroDataService) -> None:
        self.macro_data = macro_data

    def assess(
        self,
        *,
        benchmark_text: str | None,
        tracking_target: str | None,
        as_of_date: str,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Tracking index first, otherwise the heaviest equity benchmark component with a valuation series."""

        components = benchmark_components(benchmark_text)
        equity = [(label, weight) for label, weight in components if not any(word in label for word in _BOND_OR_CASH)]
        candidates: list[tuple[str, str, float | None]] = []
        if tracking_target:
            candidates.append(("tracking_index", tracking_target, None))
        candidates.extend(("benchmark_component", label, weight) for label, weight in sorted(equity, key=lambda item: -item[1]))
        chosen = next(((basis, label, weight, match_index(label)) for basis, label, weight in candidates if match_index(label)), None)
        result: dict[str, Any] = {
            "tracking_target": tracking_target,
            "benchmark_components": [{"label": label, "weight": weight} for label, weight in components],
            "equity_weight": round(sum(weight for _, weight in equity), 4) if equity else None,
        }
        if chosen is None:
            subject = tracking_target or (equity[0][0] if equity else None)
            result.update(
                basis="none",
                note=(
                    f"{subject or '基准'}没有可用的长期估值序列（目前只有沪深300、中证500 等 12 个宽基指数），"
                    "下面的市场整体估值只作背景，不代表基金底层"
                ),
            )
            result["market_context"] = self._stat("沪深300", "csi300_pe_ttm", "csi300_pb", as_of_date, allow_remote)
            return result
        basis, label, weight, (name, pe_series, pb_series) = chosen
        result.update(basis=basis, component_label=label, component_weight=weight)
        result["index"] = self._stat(name, pe_series, pb_series, as_of_date, allow_remote)
        if basis == "benchmark_component" and tracking_target is None:
            result["note"] = "主动基金的持仓可以偏离业绩比较基准，基准指数估值只代表基金经理被考核的市场"
        return result

    def _stat(self, name: str, pe_series: str, pb_series: str | None, as_of_date: str, allow_remote: bool) -> dict[str, Any]:
        stats: dict[str, Any] = {"name": name}
        for key, series_id in (("pe_ttm", pe_series), ("pb", pb_series)):
            if series_id is None:
                continue
            start = f"{int(as_of_date[:4]) - VALUATION_YEARS - 1}-01-01"
            try:
                series = self.macro_data.get(
                    series_id, start_date=start, end_date=as_of_date, as_of_date=as_of_date, allow_remote=allow_remote
                )
            except Exception as exc:  # noqa: BLE001 - reported to the caller as unavailable
                stats[f"{key}_error"] = f"{type(exc).__name__}: {exc}"
                continue
            stat = historical_percentile(
                [(item.period_end, item.value) for item in series.points], end_date=as_of_date, years=VALUATION_YEARS
            )
            if stat is not None:
                stats[key] = {**stat.to_dict(), "series_id": series_id}
            elif series.provider_errors:
                stats[f"{key}_error"] = "; ".join(f"{k}: {v}" for k, v in series.provider_errors.items())
        stats["source"] = "乐咕乐股指数估值，历史为月末值，最新一期为最近交易日"
        return stats
