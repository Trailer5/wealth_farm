"""Rolling out-of-sample segments and parameter-neighbourhood helpers (stage 9 slice 6).

Sources: docs/evidence/strategy_sources/rolling_oos_neighbourhood.md. Design notes:
docs/modules/3-分析工具/低频回测模块.md「滚动样本外与参数邻域」.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Mapping, Sequence

TRADING_DAYS = 252
# One-sided 5%: a neighbour (or a state) differs from the reference only beyond this Jobson–Korkie statistic.
WORSE_Z = 1.645
# Shorter last test segments are merged into the previous one.
MIN_TEST_YEARS = 1.0


def rolling_segments(
    calendar: Sequence[str], *, train_years: float, test_years: float, gap_days: int
) -> list[tuple[str, str, str]]:
    """(training end, test start, test end) with an expanding training window: each training segment runs from the
    first day to its end; the test starts `gap_days` trading days later and lasts `test_years`."""

    if not calendar:
        return []
    segments = []
    train_end_index = _index_after_years(calendar, 0, train_years) - 1
    while train_end_index < len(calendar) - 1:
        test_start_index = train_end_index + 1 + gap_days
        if test_start_index >= len(calendar):
            break
        test_end_index = min(_index_after_years(calendar, train_end_index + 1, test_years) - 1, len(calendar) - 1)
        if test_end_index <= test_start_index:
            break
        segments.append((calendar[train_end_index], calendar[test_start_index], calendar[test_end_index]))
        train_end_index = test_end_index
    if len(segments) > 1 and _years(segments[-1][1], segments[-1][2]) < MIN_TEST_YEARS:
        last = segments.pop()
        segments[-1] = (segments[-1][0], segments[-1][1], last[2])
    return segments


def annual_sharpe(returns: Sequence[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    return round(mean / sd * math.sqrt(TRADING_DAYS), 3) if sd > 0 else None


def sharpe_difference_z(first: Sequence[float], second: Sequence[float]) -> float | None:
    """Jobson–Korkie statistic (with the corrected variance) for Sharpe(first) − Sharpe(second) on paired returns.

    Large positive values mean `first` has the higher Sharpe beyond what sampling noise of two correlated series
    explains. None when either series has no dispersion or the lengths differ."""

    n = len(first)
    if n < 3 or n != len(second):
        return None
    mean_a, mean_b = sum(first) / n, sum(second) / n
    var_a = sum((x - mean_a) ** 2 for x in first) / (n - 1)
    var_b = sum((y - mean_b) ** 2 for y in second) / (n - 1)
    if var_a <= 0 or var_b <= 0:
        return None
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(first, second)) / (n - 1)
    rho = cov / math.sqrt(var_a * var_b)
    sr_a, sr_b = mean_a / math.sqrt(var_a), mean_b / math.sqrt(var_b)
    variance = (2 * (1 - rho) + 0.5 * (sr_a ** 2 + sr_b ** 2 - 2 * sr_a * sr_b * rho ** 2)) / n
    if variance <= 0:
        return 0.0
    return round((sr_a - sr_b) / math.sqrt(variance), 3)


def neighbourhood_verdict(
    base_returns: Sequence[float], variants: Mapping[str, Sequence[float]]
) -> dict[str, object]:
    """Whether most neighbours are significantly worse than the registered parameters ("parameter sensitive").

    Paired daily excess returns over the same window; a neighbour is worse when the Jobson–Korkie statistic of
    base minus neighbour reaches WORSE_Z, or when it never moved (no Sharpe)."""

    base_sharpe = annual_sharpe(base_returns)
    if base_sharpe is None or not variants:
        return {"variants": len(variants), "sensitive": None, "verdict": "没有可比较的夏普，无法判断参数敏感性"}
    rows, worse, sharpes = {}, [], []
    for label, returns in variants.items():
        sharpe = annual_sharpe(returns)
        z = sharpe_difference_z(base_returns, returns) if sharpe is not None else None
        rows[label] = {"sharpe": sharpe, "z_vs_base": z}
        if sharpe is None or (z is not None and z >= WORSE_Z):
            worse.append(label)
        if sharpe is not None:
            sharpes.append(sharpe)
    sensitive = len(worse) * 2 > len(variants)
    spread = None
    if len(sharpes) > 1:
        mean = sum(sharpes) / len(sharpes)
        spread = round(math.sqrt(sum((v - mean) ** 2 for v in sharpes) / (len(sharpes) - 1)), 3)
    return {
        "variants": len(variants),
        "comparisons": rows,
        "worse": sorted(worse),
        "min_sharpe": min(sharpes) if sharpes else None,
        "max_sharpe": max(sharpes) if sharpes else None,
        "sharpe_std": spread,
        "sensitive": sensitive,
        "verdict": (
            f"参数敏感：{len(worse)}/{len(variants)} 个邻近参数的夏普显著低于登记参数（单侧 5%）"
            if sensitive
            else f"参数稳定：{len(worse)}/{len(variants)} 个邻近参数的夏普显著更低"
        ),
    }


def _index_after_years(calendar: Sequence[str], start_index: int, years: float) -> int:
    target_text = _add_days(date.fromisoformat(calendar[start_index]), years * 365.25)
    for index in range(start_index, len(calendar)):
        if calendar[index] >= target_text:
            return index
    return len(calendar)


def _add_days(start: date, days: float) -> str:
    return date.fromordinal(start.toordinal() + int(round(days))).isoformat()


def _years(first: str, last: str) -> float:
    return (date.fromisoformat(last) - date.fromisoformat(first)).days / 365.25
