"""US stock valuation: reverse DCF and value at given growth from SEC XBRL facts and USD prices.

The A-share model reads statements from the local financial store; US companies file XBRL with the
SEC instead, so this service reads companyfacts, rebuilds trailing-twelve-month figures and applies
the same DCF arithmetic (src/fundamental_analysis/valuation_models.py). The discount rate is the
10-year Treasury yield plus an equity risk premium. Design notes:
docs/modules/3-分析工具/估值与财务分析模块.md「美股估值」.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from src.fundamental_analysis.us_facts import summarize
from src.fundamental_analysis.valuation_models import implied_growth, present_value

DEFAULT_ERP = 0.05
DEFAULT_TERMINAL_GROWTH = 0.025
EXPLICIT_YEARS = 10
GROWTH_GRID = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
NOT_A_TARGET = "价值表说明“在给定增速下值多少”，不是目标价；反向 DCF 说明价格隐含的增速，不说明市场错了"

Fetch = Callable[[str, dict[str, Any]], Any]


class USValuationService:
    def __init__(self, *, fetch: Fetch, price_history: Any) -> None:
        self.fetch = fetch
        self.price_history = price_history

    def run(
        self,
        symbol: str,
        as_of_date: str,
        *,
        risk_free: float | None = None,
        equity_risk_premium: float | None = None,
        terminal_growth: float | None = None,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        asset = self.price_history.resolve(symbol, market="us")
        start = (date.fromisoformat(as_of_date) - timedelta(days=20)).isoformat()
        prices = self.price_history.history(asset, start_date=start, end_date=as_of_date, allow_remote=allow_remote)
        if not prices.get("last"):
            raise ValueError(f"{symbol} 在 {as_of_date} 前没有价格")
        facts = self.fetch("sec_company_facts", {"ticker": asset.standard_symbol})
        rate = (
            {"percent": risk_free * 100, "date": as_of_date, "source": "用户指定"}
            if risk_free is not None
            else self.fetch("us_treasury_10y", {"as_of_date": as_of_date})
        )
        return value_us_company(
            facts,
            price=float(prices["last"]["close"]),
            price_date=prices["last"]["date"],
            as_of_date=as_of_date,
            risk_free=rate,
            equity_risk_premium=DEFAULT_ERP if equity_risk_premium is None else equity_risk_premium,
            terminal_growth=DEFAULT_TERMINAL_GROWTH if terminal_growth is None else terminal_growth,
        ) | {"symbol": asset.standard_symbol, "name": asset.display_name}


def value_us_company(
    facts: dict[str, Any],
    *,
    price: float,
    price_date: str,
    as_of_date: str,
    risk_free: dict[str, Any],
    equity_risk_premium: float,
    terminal_growth: float,
) -> dict[str, Any]:
    summary = summarize(facts["facts"], as_of_date)
    gaps: list[str] = []
    shares = (summary["shares"] or {}).get("value")
    if not shares:
        raise ValueError("SEC 申报里没有流通股数（dei:EntityCommonStockSharesOutstanding）")
    discount = risk_free["percent"] / 100 + equity_risk_premium
    market_cap = price * shares
    flows = {name: (item or {}).get("value") for name, item in summary["ttm"].items()}
    balances = {name: (item or {}).get("value") for name, item in summary["balances"].items()}
    cash = (balances.get("cash") or 0.0) + (balances.get("short_investments") or 0.0)
    debt = (balances.get("debt") or 0.0) + (balances.get("debt_current") or 0.0)
    if balances.get("cash") is None:
        gaps.append("缺现金科目，企业价值按市值计")
    net_debt = debt - cash
    enterprise_value = market_cap + net_debt
    fcf = None
    if flows.get("operating_cash_flow") is not None:
        fcf = flows["operating_cash_flow"] - (flows.get("capex") or 0.0)
    bases = []
    for name, label, base, target, debt_adjust in (
        ("net_income", "GAAP 归母净利润（TTM）", flows.get("net_income"), market_cap, 0.0),
        ("free_cash_flow", "自由现金流（TTM，经营现金流 − 资本开支）", fcf, enterprise_value, net_debt),
    ):
        if base is None or base <= 0:
            gaps.append(f"{label}不为正或缺失：该口径的反向 DCF 不适用")
            continue
        growth = implied_growth(target, base, discount_rate=discount, terminal_growth=terminal_growth, years=EXPLICIT_YEARS)
        value_by_growth = {
            f"{rate:.0%}": round(
                (present_value(base, rate, discount_rate=discount, terminal_growth=terminal_growth, years=EXPLICIT_YEARS) - debt_adjust)
                / shares,
                2,
            )
            for rate in GROWTH_GRID
        }
        bases.append(
            {
                "basis": name,
                "label": label,
                "base_value": round(base, 0),
                "multiple": round(target / base, 2),
                "implied_growth": None if growth is None else round(growth, 4),
                "value_per_share_by_growth": value_by_growth,
            }
        )
    history = {
        name: [{"fiscal_year_end": row["fiscal_year_end"], "value": round(row["value"], 0)} for row in rows[-6:]]
        for name, rows in summary["annual"].items()
    }
    cagr = {}
    for name, rows in summary["annual"].items():
        if len(rows) >= 4 and rows[-4]["value"] > 0 and rows[-1]["value"] > 0:
            cagr[f"{name}_3y"] = round((rows[-1]["value"] / rows[-4]["value"]) ** (1 / 3) - 1, 4)
    gaps.append("GAAP 口径含一次性项目（如税收利益、收购摊销），项目没有接入非 GAAP 与一致预期")
    return {
        "as_of_date": as_of_date,
        "company": facts.get("company"),
        "currency": "USD",
        "market": {
            "price": price,
            "price_date": price_date,
            "shares": shares,
            "shares_as_of": summary["shares"]["period_end"],
            "market_cap": round(market_cap, 0),
            "cash_and_short_investments": round(cash, 0),
            "debt": round(debt, 0),
            "enterprise_value": round(enterprise_value, 0),
        },
        "ttm": {
            name: None if item is None else {"value": round(item["value"], 0), "period_end": item["period_end"], "basis": item["basis"]}
            for name, item in summary["ttm"].items()
        },
        "assumptions": {
            "risk_free_10y": round(risk_free["percent"] / 100, 6),
            "risk_free_date": risk_free.get("date"),
            "risk_free_source": risk_free.get("source"),
            "equity_risk_premium": equity_risk_premium,
            "discount_rate": round(discount, 6),
            "terminal_growth": terminal_growth,
            "explicit_years": EXPLICIT_YEARS,
        },
        "bases": bases,
        "history": history,
        "historical_cagr": cagr,
        "gaps": gaps,
        "notes": [NOT_A_TARGET, "TTM = 上一财年 + 本年迄今 − 上年同期迄今，只用截止日前已申报的数据"],
    }
