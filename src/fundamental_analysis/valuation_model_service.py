"""Implied expectations and intrinsic-value ranges per research profile (decisions 1–2).

Design notes: docs/modules/3-分析工具/估值与财务分析模块.md, “设计说明”.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from src.data_access import (
    DATASET_STOCK_INCOME_STATEMENTS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.financial_data import FINANCIAL_DATASETS, FinancialResearchBundle
from src.macro_data import MacroDataService
from src.master_data import Asset
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
)

from .assembler import FundamentalSnapshotAssembler, FundamentalSnapshotError
from .models import FundamentalSnapshot
from .valuation_models import (
    cagr,
    implied_discount_rate,
    implied_growth,
    implied_revenue_growth,
    implied_roe,
    implied_years,
    present_value,
)

VALUATION_MODEL_VERSION = "valuation_models_v1"
DEFAULT_EQUITY_RISK_PREMIUM = 0.05
DEFAULT_TERMINAL_GROWTH = 0.02
EXPLICIT_YEARS = 10
SCENARIO_SPREAD = 0.05
BASE_GROWTH_BOUNDS = (-0.05, 0.20)
TERMINAL_MARGINS = (0.05, 0.10, 0.15)
HISTORY_YEARS = 11
NOT_A_TARGET = "情景区间与敏感性只说明假设变化时价值的范围，不是目标价；反向 DCF 说明价格隐含的预期，不说明市场错了"


@dataclass(frozen=True)
class ValuationAssumptions:
    risk_free: float
    risk_free_date: str
    equity_risk_premium: float = DEFAULT_EQUITY_RISK_PREMIUM
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH
    years: int = EXPLICIT_YEARS
    overrides: tuple[str, ...] = field(default=())

    @property
    def discount_rate(self) -> float:
        return self.risk_free + self.equity_risk_premium

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_free_10y": round(self.risk_free, 6),
            "risk_free_date": self.risk_free_date,
            "risk_free_source": "macro:cgb_10y",
            "equity_risk_premium": self.equity_risk_premium,
            "discount_rate": round(self.discount_rate, 6),
            "terminal_growth": self.terminal_growth,
            "explicit_years": self.years,
            "overrides": list(self.overrides),
        }


class ValuationModelUnavailable(RuntimeError):
    """Inputs for the valuation model are missing."""


class ValuationModelService:
    def __init__(self, *, data_access: DataAccessService, macro_data: MacroDataService) -> None:
        self.data_access = data_access
        self.macro_data = macro_data

    def assumptions(
        self,
        as_of_date: str,
        *,
        equity_risk_premium: float | None = None,
        terminal_growth: float | None = None,
        allow_remote: bool = True,
    ) -> ValuationAssumptions:
        start = (date.fromisoformat(as_of_date) - timedelta(days=60)).isoformat()
        series = self.macro_data.get(
            "cgb_10y", start_date=start, end_date=as_of_date, as_of_date=as_of_date, allow_remote=allow_remote
        )
        if not series.points:
            raise ValuationModelUnavailable("10 年国债收益率不可用，无法确定折现率")
        latest = max(series.points, key=lambda item: item.period_end)
        overrides = tuple(
            name
            for name, value in (("equity_risk_premium", equity_risk_premium), ("terminal_growth", terminal_growth))
            if value is not None
        )
        return ValuationAssumptions(
            risk_free=latest.value / 100,
            risk_free_date=latest.period_end,
            equity_risk_premium=(
                DEFAULT_EQUITY_RISK_PREMIUM if equity_risk_premium is None else equity_risk_premium
            ),
            terminal_growth=DEFAULT_TERMINAL_GROWTH if terminal_growth is None else terminal_growth,
            overrides=overrides,
        )

    def annual_history(self, asset: Asset, as_of_date: str, *, allow_remote: bool = True) -> list[dict[str, Any]]:
        """Fiscal-year revenue, parent net profit and 扣非 profit, oldest first (up to 11 years)."""

        start = f"{int(as_of_date[:4]) - HISTORY_YEARS}-01-01"
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_INCOME_STATEMENTS,
                start_date=start,
                end_date=as_of_date,
                as_of_date=as_of_date,
                max_age_seconds=86400,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                remote_sources=("akshare",),
            )
        )
        bundle = FinancialResearchBundle(
            asset=asset,
            as_of_date=as_of_date,
            start_date=start,
            end_date=as_of_date,
            records_by_dataset={
                name: response.records if name == DATASET_STOCK_INCOME_STATEMENTS else ()
                for name in FINANCIAL_DATASETS
            },
            sources=tuple(response.sources),
            complete=response.complete,
        )
        periods = sorted(
            {
                str(record["report_period"])
                for record in response.records
                if str(record["report_period"]).endswith("-12-31")
                and str(record.get("published_at") or "9999") <= as_of_date
            }
        )
        history = []
        assembler = FundamentalSnapshotAssembler()
        for period in periods:
            try:
                snapshot = assembler.assemble(bundle, market_price=Decimal("1"), target_report_period=period)
            except FundamentalSnapshotError:
                continue
            fields = snapshot.fields
            history.append(
                {
                    "fiscal_year": period[:4],
                    "revenue": _float(fields.get("revenue")),
                    "earnings": _float(fields.get("earnings")),
                    "recurring_earnings": _float(fields.get("recurring_earnings")),
                }
            )
        return history

    def evaluate_company(
        self,
        *,
        snapshot: FundamentalSnapshot,
        asset: Asset,
        profile_id: str,
        as_of_date: str,
        equity_risk_premium: float | None = None,
        terminal_growth: float | None = None,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        if snapshot.shares_outstanding is None:
            raise ValuationModelUnavailable("缺总股本，无法计算市值")
        assumptions = self.assumptions(
            as_of_date,
            equity_risk_premium=equity_risk_premium,
            terminal_growth=terminal_growth,
            allow_remote=allow_remote,
        )
        r, tg, years = assumptions.discount_rate, assumptions.terminal_growth, assumptions.years
        fields = {name: float(value) for name, value in snapshot.fields.items()}
        price = float(snapshot.market_price)
        shares = float(snapshot.shares_outstanding)
        market_cap = price * shares
        gaps: list[str] = []
        net_debt = None
        if "cash_and_equivalents" in fields:
            if "total_debt" not in fields:
                gaps.append("资产负债表未列有息负债科目，有息负债按 0 处理")
            net_debt = fields.get("total_debt", 0.0) - fields["cash_and_equivalents"]
        else:
            gaps.append("缺货币资金：自由现金流口径无法从企业价值换算到股权价值")
        enterprise_value = market_cap + net_debt if net_debt is not None else None
        history = self.annual_history(asset, as_of_date, allow_remote=allow_remote)
        growth = _historical_growth(history)

        bases: list[dict[str, Any]] = []
        if profile_id == PROFILE_HIGH_GROWTH_UNPROFITABLE:
            revenue = fields.get("revenue")
            start_margin = (fields.get("earnings") or 0.0) / revenue if revenue else 0.0
            implied = {
                f"{margin:.0%}": implied_revenue_growth(
                    market_cap,
                    revenue or 0.0,
                    start_margin=start_margin,
                    terminal_margin=margin,
                    discount_rate=r,
                    terminal_growth=tg,
                    years=years,
                )
                for margin in TERMINAL_MARGINS
            }
            return self._payload(
                method="revenue_reverse_dcf",
                snapshot=snapshot,
                profile_id=profile_id,
                assumptions=assumptions,
                market={"price": price, "shares": shares, "market_cap": market_cap, "net_debt": net_debt},
                history=history,
                growth=growth,
                extra={
                    "revenue_ttm": revenue,
                    "net_margin_ttm": round(start_margin, 6),
                    "implied_revenue_growth_by_terminal_margin": {
                        key: None if value is None else round(value, 6) for key, value in implied.items()
                    },
                },
                gaps=gaps,
            )
        if profile_id == PROFILE_CYCLICAL_RESOURCE:
            margins = [
                item["earnings"] / item["revenue"]
                for item in history[-10:]
                if item["earnings"] is not None and item["revenue"]
            ]
            if margins and fields.get("revenue"):
                normalized = median(margins) * fields["revenue"]
                bases.append(
                    _basis(
                        "normalized_earnings",
                        f"正常化利润（近 {len(margins)} 个年报净利率中位数 {median(margins):.1%} × TTM 营收）",
                        normalized,
                        "market_cap",
                        market_cap,
                        r,
                        tg,
                        years,
                    )
                )
            else:
                gaps.append("年报历史不足，无法计算正常化利润")
        else:
            if "free_cash_flow" in fields and enterprise_value is not None:
                bases.append(
                    _basis(
                        "free_cash_flow",
                        "自由现金流（TTM，经营现金流 − 购建长期资产）",
                        fields["free_cash_flow"],
                        "enterprise_value",
                        enterprise_value,
                        r,
                        tg,
                        years,
                    )
                )
        earnings_name = "recurring_earnings" if "recurring_earnings" in fields else "earnings"
        if earnings_name in fields:
            bases.append(
                _basis(
                    earnings_name,
                    "扣非归母净利润（TTM）" if earnings_name == "recurring_earnings" else "归母净利润（TTM）",
                    fields[earnings_name],
                    "market_cap",
                    market_cap,
                    r,
                    tg,
                    years,
                )
            )
        primary = next((item for item in bases if item["implied_growth"] is not None), None)
        if primary is None:
            gaps.append("没有为正的现金流或利润口径：反向 DCF 与情景区间不适用")
        scenarios, sensitivity, high_growth = None, None, None
        cyclical = profile_id == PROFILE_CYCLICAL_RESOURCE
        if cyclical:
            base_growth = growth.get("long_run_revenue_cagr")
            anchor_source = f"近 {growth.get('long_run_span_years')} 年营收复合增速（周期股不用近 3 年利润增速，避免顶部或底部外推）"
        else:
            base_growth = next(
                (
                    growth[key]
                    for key in ("recurring_earnings_cagr", "earnings_cagr", "revenue_cagr")
                    if growth.get(key) is not None
                ),
                None,
            )
            anchor_source = f"近 {growth.get('span_years')} 年扣非利润复合增速（不可算时依次用归母、营收）"
        if primary is not None and base_growth is None:
            gaps.append("年报历史不足以计算历史增速：未给情景区间")
        # The growth anchor is an earnings CAGR, so scenarios compound the earnings base rather than FCF.
        scenario_basis = primary
        if not cyclical:
            scenario_basis = next(
                (item for item in bases if item["basis"] == earnings_name and item["implied_growth"] is not None),
                primary,
            )
        if primary is not None and base_growth is not None:
            anchor = min(max(base_growth, BASE_GROWTH_BOUNDS[0]), BASE_GROWTH_BOUNDS[1])
            debt = net_debt if scenario_basis["target"] == "enterprise_value" else 0.0
            scenarios = {
                name: _scenario(scenario_basis["base_value"], anchor + offset, r, tg, years, debt, shares, price)
                for name, offset in (("pessimistic", -SCENARIO_SPREAD), ("base", 0.0), ("optimistic", SCENARIO_SPREAD))
            }
            implied_return = implied_discount_rate(
                scenario_basis["target_value"], scenario_basis["base_value"], anchor, terminal_growth=tg, years=years
            )
            scenarios["anchor"] = {
                "historical_growth": round(base_growth, 6),
                "growth_used": round(anchor, 6),
                "implied_return": None if implied_return is None else round(implied_return, 6),
                "basis": scenario_basis["basis"],
                "note": (
                    f"基准增速取{anchor_source}，限制在 {BASE_GROWTH_BOUNDS[0]:.0%}–{BASE_GROWTH_BOUNDS[1]:.0%}，"
                    f"悲观与乐观各 ±{SCENARIO_SPREAD:.0%}；情景以{scenario_basis['label']}为基数"
                ),
            }
            sensitivity = _sensitivity(scenario_basis["base_value"], anchor, r, years, debt, shares)
            if base_growth > tg and not cyclical:
                high_growth = {
                    "growth": round(base_growth, 6),
                    "years": implied_years(
                        scenario_basis["target_value"], scenario_basis["base_value"], base_growth, discount_rate=r, terminal_growth=tg
                    ),
                    "note": "按历史增速持续多少年、之后转为永续增长，才能支撑当前价格",
                }
        return self._payload(
            method="normalized_reverse_dcf" if profile_id == PROFILE_CYCLICAL_RESOURCE else "reverse_dcf",
            snapshot=snapshot,
            profile_id=profile_id,
            assumptions=assumptions,
            market={
                "price": price,
                "shares": shares,
                "market_cap": market_cap,
                "net_debt": net_debt,
                "enterprise_value": enterprise_value,
            },
            history=history,
            growth=growth,
            extra={
                "implied": bases,
                "primary_basis": primary["basis"] if primary else None,
                "implied_high_growth_years": high_growth,
                "scenarios": scenarios,
                "sensitivity": sensitivity,
            },
            gaps=gaps,
        )

    def evaluate_bank(
        self,
        *,
        pb: float,
        roe: float | None,
        roe_history: list[tuple[str, float]],
        as_of_date: str,
        report_period: str,
        equity_risk_premium: float | None = None,
        terminal_growth: float | None = None,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        assumptions = self.assumptions(
            as_of_date,
            equity_risk_premium=equity_risk_premium,
            terminal_growth=terminal_growth,
            allow_remote=allow_remote,
        )
        r, tg = assumptions.discount_rate, assumptions.terminal_growth
        grid = {
            f"r={rate:.2%}": {
                f"g={growth:.0%}": round(implied_roe(pb, discount_rate=rate, terminal_growth=growth), 6)
                for growth in (0.01, 0.02, 0.03)
            }
            for rate in (r - 0.01, r, r + 0.01)
        }
        recent = [value for _, value in roe_history[-5:]]
        return {
            "version": VALUATION_MODEL_VERSION,
            "method": "pb_roe",
            "as_of_date": as_of_date,
            "report_period": report_period,
            "assumptions": assumptions.to_dict(),
            "pb": round(pb, 6),
            "implied_roe": round(implied_roe(pb, discount_rate=r, terminal_growth=tg), 6),
            "roe_latest_annualized": None if roe is None else round(roe, 6),
            "roe_history": [{"period": p, "roe": round(v, 6)} for p, v in roe_history],
            "roe_recent_median": round(median(recent), 6) if recent else None,
            "sensitivity_implied_roe": grid,
            "notes": [
                "P/B = (ROE − g) / (r − g)：当前市净率隐含的可持续 ROE，与近年 ROE 对照；隐含 ROE 低于近年 ROE 说明价格假设盈利能力下降",
                NOT_A_TARGET,
            ],
            "gaps": [],
        }

    def _payload(
        self,
        *,
        method: str,
        snapshot: FundamentalSnapshot,
        profile_id: str,
        assumptions: ValuationAssumptions,
        market: dict[str, Any],
        history: list[dict[str, Any]],
        growth: dict[str, Any],
        extra: dict[str, Any],
        gaps: list[str],
    ) -> dict[str, Any]:
        return {
            "version": VALUATION_MODEL_VERSION,
            "method": method,
            "profile_id": profile_id,
            "as_of_date": snapshot.as_of_date,
            "report_period": snapshot.report_period,
            "assumptions": assumptions.to_dict(),
            "market": {key: None if value is None else round(value, 4) for key, value in market.items()},
            "history": history,
            "historical_growth": growth,
            **extra,
            "notes": [NOT_A_TARGET],
            "gaps": gaps,
        }


def valuation_model_lines(model: dict[str, Any]) -> list[tuple[str, str]]:
    """Readable (name, text) lines for reports and ledger assessments."""

    a = model["assumptions"]
    lines: list[tuple[str, str]] = []
    if model["method"] == "pb_roe":
        text = f"市净率 {model['pb']:.2f} 倍隐含可持续 ROE {model['implied_roe']:.1%}"
        if model.get("roe_latest_annualized") is not None:
            text += f"；最新 ROE（{model['report_period']}，年化）{model['roe_latest_annualized']:.1%}"
        if model.get("roe_recent_median") is not None:
            years = min(5, len(model["roe_history"]))
            text += f"；近 {years} 个年报 ROE 中位数 {model['roe_recent_median']:.1%}"
        lines.append(("隐含 ROE", text))
        values = [v for row in model["sensitivity_implied_roe"].values() for v in row.values()]
        lines.append(("敏感性", f"折现率 ±1pp、永续增长 1%–3% 下，隐含 ROE {min(values):.1%}–{max(values):.1%}"))
    elif model["method"] == "revenue_reverse_dcf":
        implied = model["implied_revenue_growth_by_terminal_margin"]
        parts = [f"终局净利率 {k} 时 {'超出求解范围' if v is None else f'{v:.1%}'}" for k, v in implied.items()]
        lines.append(
            (
                "隐含营收增速",
                f"市值 {model['market']['market_cap'] / 1e8:.0f} 亿元隐含未来 {a['explicit_years']} 年营收年增："
                + "；".join(parts),
            )
        )
    else:
        targets = {"market_cap": "市值", "enterprise_value": "企业价值"}
        for item in model["implied"]:
            if item["implied_growth"] is None:
                text = item["note"]
            else:
                text = (
                    f"{targets[item['target']]} {item['target_value'] / 1e8:.0f} 亿元隐含未来 {a['explicit_years']} 年年增"
                    f" {item['implied_growth']:.1%}，之后永续 {a['terminal_growth']:.0%}"
                )
            lines.append((f"隐含增速：{item['label']}", text))
        if model.get("implied_high_growth_years"):
            hg = model["implied_high_growth_years"]
            if hg["years"] is None:
                years = "30 年内无法支撑当前价格"
            elif hg["years"] == 0:
                years = "不需要高增长期：只按永续增长计算的价值已不低于当前价格"
            else:
                years = f"需持续 {hg['years']} 年才能支撑当前价格"
            lines.append(("隐含高增长年限", f"按历史增速 {hg['growth']:.1%}，{years}"))
        scenarios = model.get("scenarios")
        if scenarios:
            names = {"pessimistic": "悲观", "base": "基准", "optimistic": "乐观"}
            lines.append(
                (
                    "情景区间",
                    "；".join(
                        f"{label}（年增 {scenarios[key]['growth']:.1%}）每股 {scenarios[key]['per_share']:.2f} 元，"
                        f"较现价 {scenarios[key]['vs_price']:+.0%}"
                        for key, label in names.items()
                    )
                    + f"。{scenarios['anchor']['note']}",
                )
            )
            implied_return = scenarios["anchor"].get("implied_return")
            if implied_return is not None:
                lines.append(
                    (
                        "隐含回报",
                        f"若基准增速 {scenarios['anchor']['growth_used']:.1%} 兑现，按现价买入的年化回报约 {implied_return:.1%}"
                        f"（对照折现率 {a['discount_rate']:.2%}、10 年国债 {a['risk_free_10y']:.2%}）",
                    )
                )
        sensitivity = model.get("sensitivity")
        if sensitivity:
            values = [v for row in sensitivity["per_share"] for v in row if v is not None]
            rates = sensitivity["discount_rates"]
            lines.append(
                (
                    "敏感性",
                    f"折现率 {rates[0]:.2%}–{rates[-1]:.2%}、永续增长 1%–3% 下，基准情景每股 {min(values):.2f}–{max(values):.2f} 元",
                )
            )
    growth = model.get("historical_growth") or {}
    if growth.get("span_years"):
        years = growth["fiscal_years"]
        parts = [
            f"{label} {growth[key]:.1%}" if growth.get(key) is not None else f"{label} 不可算"
            for key, label in (
                ("recurring_earnings_cagr", "扣非"),
                ("earnings_cagr", "归母"),
                ("revenue_cagr", "营收"),
            )
        ]
        text = f"{years[-1 - growth['span_years']]}–{years[-1]} 年报 {growth['span_years']} 年复合增速：" + "、".join(parts)
        if growth.get("long_run_span_years", 0) > growth["span_years"] and growth.get("long_run_revenue_cagr") is not None:
            text += f"；{years[-1 - growth['long_run_span_years']]}–{years[-1]} 营收复合增速 {growth['long_run_revenue_cagr']:.1%}"
        lines.append(("历史增速", text))
    lines.append(
        (
            "假设",
            f"折现率 {a['discount_rate']:.2%} = 10 年国债 {a['risk_free_10y']:.2%}（{a['risk_free_date']}）"
            f" + 股权风险溢价 {a['equity_risk_premium']:.0%}；永续增长 {a['terminal_growth']:.0%}；显式期 {a['explicit_years']} 年",
        )
    )
    return lines


def _basis(
    basis: str,
    label: str,
    base_value: float,
    target: str,
    target_value: float,
    r: float,
    tg: float,
    years: int,
) -> dict[str, Any]:
    growth = implied_growth(target_value, base_value, discount_rate=r, terminal_growth=tg, years=years)
    return {
        "basis": basis,
        "label": label,
        "base_value": base_value,
        "target": target,
        "target_value": target_value,
        "implied_growth": None if growth is None else round(growth, 6),
        "note": None if growth is not None else ("基数不为正，不求解" if base_value <= 0 else "隐含增速超出 −50%～100% 的求解范围"),
    }


def _scenario(
    base: float,
    growth: float,
    r: float,
    tg: float,
    years: int,
    net_debt: float | None,
    shares: float,
    price: float,
) -> dict[str, Any]:
    value = present_value(base, growth, discount_rate=r, terminal_growth=tg, years=years) - (net_debt or 0.0)
    per_share = value / shares
    return {
        "growth": round(growth, 6),
        "equity_value": round(value, 2),
        "per_share": round(per_share, 4),
        "vs_price": round(per_share / price - 1, 6),
    }


def _sensitivity(
    base: float, growth: float, r: float, years: int, net_debt: float | None, shares: float
) -> dict[str, Any]:
    rates = (r - 0.01, r, r + 0.01)
    terminals = (0.01, 0.02, 0.03)
    return {
        "growth": round(growth, 6),
        "discount_rates": [round(rate, 6) for rate in rates],
        "terminal_growths": list(terminals),
        "per_share": [
            [
                round(
                    (present_value(base, growth, discount_rate=rate, terminal_growth=tg, years=years) - (net_debt or 0.0))
                    / shares,
                    4,
                )
                if rate > tg
                else None
                for tg in terminals
            ]
            for rate in rates
        ],
    }


def _historical_growth(history: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"fiscal_years": [item["fiscal_year"] for item in history]}
    if len(history) < 2:
        return result
    span = min(3, len(history) - 1)
    first, last = history[-1 - span], history[-1]
    result["span_years"] = span
    for name in ("recurring_earnings", "earnings", "revenue"):
        value = cagr(first[name], last[name], span)
        result[f"{name}_cagr"] = None if value is None else round(value, 6)
    long_span = min(10, len(history) - 1)
    value = cagr(history[-1 - long_span]["revenue"], last["revenue"], long_span)
    result["long_run_span_years"] = long_span
    result["long_run_revenue_cagr"] = None if value is None else round(value, 6)
    return result


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
