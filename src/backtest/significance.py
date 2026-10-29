"""Backtest significance after selection: probabilistic and deflated Sharpe ratios (stage 9 slice 5).

Per-period (daily) Sharpe ratios throughout; annual figures are scaled by sqrt(252) only for display.
Sources: docs/evidence/strategy_sources/deflated_sharpe.md. Design notes:
docs/modules/3-分析工具/低频回测模块.md「尝试次数与显著性折扣」.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Sequence

EULER_GAMMA = 0.5772156649015329
PERIODS_PER_YEAR = 252
CONFIDENCE = 0.95
_NORMAL = NormalDist()


@dataclass(frozen=True)
class SharpeMoments:
    sharpe: float
    observations: int
    skew: float
    kurtosis: float  # not excess: 3 for normal returns


def sharpe_moments(returns: Sequence[float]) -> SharpeMoments | None:
    """Per-period Sharpe (sample standard deviation) with population skew and kurtosis; None without dispersion."""

    n = len(returns)
    if n < 3:
        return None
    mean = sum(returns) / n
    deviations = [r - mean for r in returns]
    m2 = sum(d * d for d in deviations) / n
    if m2 <= 0:
        return None
    m3 = sum(d ** 3 for d in deviations) / n
    m4 = sum(d ** 4 for d in deviations) / n
    sd = math.sqrt(sum(d * d for d in deviations) / (n - 1))
    return SharpeMoments(mean / sd, n, m3 / m2 ** 1.5, m4 / m2 ** 2)


def sharpe_std(moments: SharpeMoments) -> float:
    sr = moments.sharpe
    variance = (1 - moments.skew * sr + (moments.kurtosis - 1) / 4 * sr * sr) / (moments.observations - 1)
    return math.sqrt(max(variance, 0.0))


def probabilistic_sharpe(moments: SharpeMoments, benchmark: float = 0.0) -> float:
    """Probability that the true per-period Sharpe exceeds the benchmark."""

    sd = sharpe_std(moments)
    if sd == 0:
        return 1.0 if moments.sharpe > benchmark else 0.0
    return _NORMAL.cdf((moments.sharpe - benchmark) / sd)


def expected_max_z(trials: int) -> float:
    """Expected maximum of `trials` independent standard normals (zero for a single trial)."""

    if trials < 2:
        return 0.0
    return (1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials) + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * math.e))


def expected_max_sharpe(trials: int, trials_sharpe_std: float, mean: float = 0.0) -> float:
    return mean + trials_sharpe_std * expected_max_z(trials)


def min_track_record(moments: SharpeMoments, benchmark: float, confidence: float = CONFIDENCE) -> float | None:
    """Observations needed for PSR(benchmark) to reach `confidence` at the observed Sharpe and moments."""

    gap = moments.sharpe - benchmark
    if gap <= 0:
        return None
    sr = moments.sharpe
    spread = 1 - moments.skew * sr + (moments.kurtosis - 1) / 4 * sr * sr
    return 1 + spread * (_NORMAL.inv_cdf(confidence) / gap) ** 2


def min_backtest_years(trials: int, annual_sharpe: float) -> float | None:
    """Years of data below which N skill-less trials are expected to show this annual Sharpe by luck alone."""

    if trials < 2 or annual_sharpe <= 0:
        return None
    return (expected_max_z(trials) / annual_sharpe) ** 2


def significance(
    returns: Sequence[float], trial_sharpes: Sequence[float], *, periods_per_year: int = PERIODS_PER_YEAR
) -> dict[str, object]:
    """returns: per-period excess returns of the evaluated run; trial_sharpes: annual Sharpe of every distinct
    trial in the family (including this one). Annual Sharpe of trials is converted to per-period internally."""

    moments = sharpe_moments(returns)
    trials = max(1, len(trial_sharpes))
    if moments is None:
        return {"trials": trials, "error": "收益没有波动，无法计算夏普"}
    scale = math.sqrt(periods_per_year)
    per_period = [value / scale for value in trial_sharpes]
    spread = _sample_std(per_period) if len(per_period) > 1 else 0.0
    threshold = expected_max_sharpe(trials, spread)
    psr = probabilistic_sharpe(moments)
    dsr = probabilistic_sharpe(moments, threshold)
    needed = min_track_record(moments, threshold)
    min_btl = min_backtest_years(trials, moments.sharpe * scale)
    years = moments.observations / periods_per_year
    return {
        "sharpe_annual": round(moments.sharpe * scale, 3),
        "observations": moments.observations,
        "sample_years": round(years, 2),
        "skew": round(moments.skew, 3),
        "kurtosis": round(moments.kurtosis, 3),
        "trials": trials,
        "trials_sharpe_std_annual": round(spread * scale, 3),
        "expected_max_sharpe_annual": round(threshold * scale, 3),
        "psr": round(psr, 4),
        "dsr": round(dsr, 4),
        "min_track_record_years": round(needed / periods_per_year, 1) if needed is not None else None,
        "min_backtest_years": round(min_btl, 1) if min_btl is not None else None,
        "verdict": _verdict(psr, dsr, trials, years, min_btl),
    }


def _verdict(psr: float, dsr: float, trials: int, years: float, min_btl: float | None) -> str:
    if dsr >= CONFIDENCE:
        text = f"按 {trials} 次尝试折扣后仍显著优于现金（DSR {dsr:.0%}）"
    elif psr >= CONFIDENCE:
        text = f"单次看显著（PSR {psr:.0%}），但按 {trials} 次尝试折扣后不显著（DSR {dsr:.0%}）"
    else:
        text = f"没有显著优于现金（PSR {psr:.0%}）"
    if min_btl is not None and years < min_btl:
        text += f"；样本 {years:.1f} 年短于 {trials} 次尝试所需的最短回测长度 {min_btl:.1f} 年"
    return text


def _sample_std(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
