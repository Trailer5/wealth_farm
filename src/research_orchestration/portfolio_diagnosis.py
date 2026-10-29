"""Collect holdings, prices and fund look-through, then diagnose the user's portfolio.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「组合诊断」.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.data_access import DataAccessService
from src.fund_research import (
    FundPortfolioService,
    IndexWeightService,
    OpenFundDecisionFactsService,
    default_class,
    fund_asset_class,
    look_through,
    tracked_index_code,
)
from src.research_contracts import (
    ASSET_CLASS_BOND,
    ASSET_CLASS_CASH,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_LABELS,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
)
from src.portfolio_risk import (
    STRESS_SCENARIOS,
    UNCOVERED_INDUSTRY,
    DiagnosisPosition,
    PortfolioRiskService,
    exposure_summary,
    preference_check,
    risk_summary,
    stress_summary,
    weights_of,
)
from src.research_ledger import DataAccessPriceSeriesSource
from src.research_ledger.settlement import PriceDataUnavailable
from src.sector_data import SectorDataService
from src.user_space import UserContext

HISTORY_START = "2015-01-01"
STALE_PRICE_DAYS = 10
CASH_ASSET_ID = "cash"
BENCHMARK_SYMBOL = "000300"
# An index fund whose report shows less stock than this holds its target ETF instead (feeder fund).
FEEDER_STOCK_RATIO = 0.5
_MARKET_INDUSTRY = {"hk": "港股（未分行业）", "us": "美股（未分行业）", "other": "海外（未分行业）"}
_STATUS_LABELS = {"ok": "符合", "above": "超出上限", "below": "低于下限", "unknown": "无法判断"}


class PortfolioDiagnosisService:
    def __init__(
        self,
        *,
        portfolio_risk: PortfolioRiskService,
        master_data: MasterDataService,
        data_access: DataAccessService,
        sector_data: SectorDataService,
        fund_facts: OpenFundDecisionFactsService,
        fund_portfolio: FundPortfolioService,
        index_weights: IndexWeightService,
        report_root: str | Path,
    ) -> None:
        self.portfolio_risk = portfolio_risk
        self.master_data = master_data
        self.data_access = data_access
        self.sector_data = sector_data
        self.fund_facts = fund_facts
        self.fund_portfolio = fund_portfolio
        self.index_weights = index_weights
        self.report_root = Path(report_root)

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        allow_remote: bool = True,
        write_report: bool = True,
    ) -> dict[str, Any]:
        state = self.collect(user_id=user_id, as_of_date=as_of_date, cash=cash, allow_remote=allow_remote)
        context = UserContext(user_id=user_id)
        positions, levels, latest, benchmark, gaps = (
            state["positions"], state["levels"], state["latest"], state["benchmark"], state["gaps"]
        )
        labels = {item.asset_id: item.label for item in positions}
        weights = weights_of(positions)
        exposures = exposure_summary(positions)
        cash_like = frozenset(item.asset_id for item in positions if item.main_class() == ASSET_CLASS_CASH)
        risk = risk_summary(
            weights,
            levels,
            as_of_date=as_of_date,
            proxy_levels=benchmark,
            proxy_eligible=frozenset(
                item.asset_id for item in positions if item.main_class() == ASSET_CLASS_EQUITY
            ),
            zero_variance=cash_like,
            labels=labels,
        )
        stress = stress_summary(
            weights,
            levels,
            as_of_date=as_of_date,
            benchmark_levels=benchmark or {},
            labels=labels,
            zero_return=cash_like,
        )
        preference = self.portfolio_risk.latest_preference(context)
        checks = preference_check(exposures, risk, stress, preference) if preference else []
        snapshot_id = self._save_valuation(context, as_of_date, latest, gaps)
        payload = {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "prices": {
                labels[key]: {"date": value[0], "price": round(value[1], 4)} for key, value in latest.items()
            },
            "exposures": exposures,
            "risk": risk,
            "stress": stress,
            "preference_id": preference.preference_id if preference else None,
            "preference_checks": checks,
            "main_risks": _main_risks(exposures, risk, stress, checks),
            "portfolio_snapshot_id": snapshot_id,
            "gaps": gaps,
            "limitations": _limitations(),
        }
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
        return payload

    def collect(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Positions with look-through, price levels since 2015 and the CSI 300 levels."""

        context = UserContext(user_id=user_id)
        holdings = [
            item for item in self.portfolio_risk.research_state.list_holdings(context) if item.quantity > 0
        ]
        if not holdings:
            raise ValueError("用户空间没有持仓记录；先用 record-holding 记录用户给出的持仓")
        prices = self.price_source(allow_remote)
        gaps: list[str] = []
        levels: dict[str, dict[str, float]] = {}
        latest: dict[str, tuple[str, float]] = {}
        assets: dict[str, Asset] = {}
        for holding in holdings:
            asset = self.master_data.get_asset(holding.asset_id)
            if asset is None:
                raise ValueError(f"未知标的 {holding.asset_id}")
            assets[asset.asset_id] = asset
            if self._is_money_fund(asset, allow_remote):
                # Money funds report yield per 10,000 units, not a unit NAV; a unit is worth 1 yuan.
                levels[asset.asset_id] = {}
                latest[asset.asset_id] = (as_of_date, 1.0)
                continue
            levels[asset.asset_id] = self.levels(prices, asset, as_of_date, gaps)
            day = max(levels[asset.asset_id])
            latest[asset.asset_id] = (day, levels[asset.asset_id][day])
            if (date.fromisoformat(as_of_date) - date.fromisoformat(day)).days > STALE_PRICE_DAYS:
                gaps.append(f"{_label(asset)} 最新价格停在 {day}")
        industry_map = self._industries(allow_remote, gaps)
        positions = [
            self.position(
                assets[holding.asset_id],
                market_value=holding.quantity * latest[holding.asset_id][1],
                as_of_date=as_of_date,
                industry_map=industry_map,
                allow_remote=allow_remote,
                gaps=gaps,
            )
            for holding in holdings
        ]
        if cash:
            positions.append(
                DiagnosisPosition(
                    asset_id=CASH_ASSET_ID,
                    label="现金",
                    asset_type="cash",
                    market_value=float(cash),
                    asset_classes={ASSET_CLASS_CASH: 1.0},
                )
            )
        return {
            "positions": positions,
            "levels": levels,
            "latest": latest,
            "benchmark": self._benchmark_levels(prices, as_of_date, gaps),
            "gaps": gaps,
            "industry_map": industry_map,
        }

    def price_source(self, allow_remote: bool) -> DataAccessPriceSeriesSource:
        return DataAccessPriceSeriesSource(
            data_access=self.data_access,
            master_data=self.master_data,
            allow_remote=allow_remote,
        )

    def levels(
        self,
        prices: DataAccessPriceSeriesSource,
        asset: Asset,
        as_of_date: str,
        gaps: list[str] | None = None,
    ) -> dict[str, float]:
        """Price levels since 2015; when the remote fetch fails, prices already stored locally are used and noted."""

        try:
            series = prices.series(asset.asset_id, HISTORY_START, as_of_date)
            error = None
        except PriceDataUnavailable as exc:
            series, error = (), exc
        if not series and prices.allow_remote:
            try:
                series = self.price_source(False).series(asset.asset_id, HISTORY_START, as_of_date)
            except PriceDataUnavailable:
                series = ()
            if series and gaps is not None:
                gaps.append(f"{_label(asset)} 联网取价失败，改用本地已有价格（最新 {series[-1].date}）")
        if error is not None and not series:
            raise ValueError(f"{_label(asset)} 取不到价格：{error}") from error
        if not series:
            raise ValueError(f"{_label(asset)} 在 {as_of_date} 前没有价格")
        return {item.date: float(item.value) for item in series}

    def position(
        self,
        asset: Asset,
        *,
        market_value: float,
        as_of_date: str,
        industry_map: dict[str, str],
        allow_remote: bool,
        gaps: list[str],
    ) -> DiagnosisPosition:
        label = _label(asset)
        if asset.asset_type == ASSET_TYPE_STOCK:
            industry = industry_map.get(asset.standard_symbol, "未分类")
            return DiagnosisPosition(
                asset_id=asset.asset_id,
                label=label,
                asset_type=asset.asset_type,
                market_value=market_value,
                asset_classes={ASSET_CLASS_EQUITY: 1.0},
                industries={industry: 1.0},
                stocks={asset.standard_symbol: (asset.display_name or asset.standard_symbol, 1.0)},
            )
        if asset.asset_type not in (ASSET_TYPE_OPEN_FUND, ASSET_TYPE_EXCHANGE_FUND):
            gaps.append(f"{label} 的资产类型 {asset.asset_type} 按权益处理")
            return DiagnosisPosition(
                asset.asset_id, label, asset.asset_type, market_value, {ASSET_CLASS_EQUITY: 1.0},
                industries={UNCOVERED_INDUSTRY: 1.0},
            )
        info, error = self.fund_facts.dealing_info(asset, allow_remote=allow_remote)
        if error:
            gaps.append(f"{label} 基金类型抓取失败：{error}")
        fund_type = info.fund_type if info else None
        whole = fund_asset_class(fund_type, info.tracking_target if info else None, asset.display_name)
        disclosure, error = self.fund_portfolio.disclosure(asset, allow_remote=allow_remote)
        if error:
            gaps.append(f"{label} 季报持仓抓取失败：{error}")
        lt = look_through(disclosure, as_of_date=as_of_date) if disclosure else None
        notes = [f"基金类型：{fund_type or '未知'}"]
        allocation = lt["allocation"] if lt else None
        total = 0.0
        if whole is not None:
            classes = {whole: 1.0}
            notes.append(f"整体归入{ASSET_CLASS_LABELS[whole]}")
        elif allocation is not None:
            parts = {
                ASSET_CLASS_EQUITY: float(allocation["stock"]),
                ASSET_CLASS_BOND: float(allocation["bond"]),
                ASSET_CLASS_CASH: float(allocation["cash"]),
            }
            total = sum(parts.values())
            classes = {key: value / total for key, value in parts.items() if value > 0} if total > 0 else {}
            notes.append(
                f"按 {allocation['period_end']} 季报资产配置拆分（股票 {allocation['stock']:.1%}、"
                f"债券 {allocation['bond']:.1%}、现金 {allocation['cash']:.1%}，合计 {total:.1%} 后归一）"
            )
        else:
            classes = {}
        if not classes:
            fallback = default_class(fund_type)
            classes = {fallback: 1.0}
            notes.append(f"未取得季报资产配置，整体按{ASSET_CLASS_LABELS[fallback]}处理")
        equity = classes.get(ASSET_CLASS_EQUITY, 0.0)
        scale = 1 / total if whole is None and total > 0 else 1.0
        index = (
            self._index_weights(info, fund_type, as_of_date=as_of_date, allow_remote=allow_remote, gaps=gaps, label=label)
            if whole is None and equity > 0
            else None
        )
        if index is not None and allocation is not None and float(allocation["stock"]) < FEEDER_STOCK_RATIO:
            classes = {ASSET_CLASS_EQUITY: 0.95, ASSET_CLASS_CASH: 0.05}
            equity = 0.95
            notes.append("季报直接持股很少，按联接基金处理：通过目标 ETF 持有指数成分，按 95% 权益估算")
        industries: dict[str, float] = {}
        stocks: dict[str, tuple[str, float]] = {}
        cross_check: dict[str, float] = {}
        covered = 0.0
        if index is not None:
            index_name, weight_date, rows = index
            total_weight = sum(float(row["weight"]) for row in rows)
            for row in rows:
                share = equity * float(row["weight"]) / total_weight
                stocks[row["code"]] = (row["name"], share)
                industry = industry_map.get(row["code"], "未分类")
                industries[industry] = industries.get(industry, 0.0) + share
            covered = equity
            notes.append(f"行业与个股按跟踪指数{index_name} {weight_date} 的全部成分权重穿透")
        else:
            items = lt["industries"] if lt else []
            hk_items = [item for item in items if item["scheme"] == "hk"]
            cross_check = {item["name"]: float(item["ratio"]) * scale for item in items if item["scheme"] == "csrc"}
            for item in (lt["holdings"] if lt else []):
                share = float(item["ratio"]) * scale
                a_share = item["market"] in ("sh", "sz", "bj")
                key = item["code"] if a_share else f"{item['market']}:{item['code']}"
                stocks[key] = (item["name"], share)
                if equity <= 0 or (item["market"] == "hk" and hk_items):
                    continue
                industry = (
                    industry_map.get(item["code"], "未分类")
                    if a_share
                    else _MARKET_INDUSTRY.get(item["market"], _MARKET_INDUSTRY["other"])
                )
                industries[industry] = industries.get(industry, 0.0) + share
                covered += share
            if equity > 0:
                for item in hk_items:
                    share = float(item["ratio"]) * scale
                    name = f"港股·{item['name']}"
                    industries[name] = industries.get(name, 0.0) + share
                    covered += share
                notes.append(
                    f"行业按 {lt['holdings_period']} 前十大持仓穿透"
                    + (f"，港股部分按 {lt['industry_period']} 季报港股行业配置全部归类" if hk_items else "")
                    + f"，覆盖权益部分的 {min(covered / equity, 1):.0%}"
                    if lt and lt["holdings_period"]
                    else "没有可用的前十大持仓，权益部分行业未穿透"
                )
        if equity > 0:
            remainder = equity - covered
            if remainder > 0.001:
                industries[UNCOVERED_INDUSTRY] = industries.get(UNCOVERED_INDUSTRY, 0.0) + remainder
        return DiagnosisPosition(
            asset_id=asset.asset_id,
            label=label,
            asset_type=asset.asset_type,
            market_value=market_value,
            asset_classes=classes,
            industries=industries,
            stocks=stocks,
            notes=tuple(notes),
            cross_check=cross_check,
        )

    def _index_weights(
        self,
        info: Any,
        fund_type: str | None,
        *,
        as_of_date: str,
        allow_remote: bool,
        gaps: list[str],
        label: str,
    ) -> tuple[str, str, list[dict[str, str]]] | None:
        """Constituent weights of the tracked index, only when they verifiably belong to it."""

        if info is None or not (fund_type or "").startswith("指数型"):
            return None
        tracked = tracked_index_code(info.tracking_target)
        if tracked is None:
            if info.tracking_target:
                gaps.append(f"{label} 跟踪的{info.tracking_target}没有可用的成分权重来源，按前十大持仓穿透")
            return None
        name, code = tracked
        rows, error = self.index_weights.weights(code, allow_remote=allow_remote)
        if error:
            gaps.append(f"{label} 跟踪指数{name}（{code}）成分权重抓取失败：{error}")
        if not rows:
            return None
        published = rows[0].get("index_name", "")
        if _strip_index(name) not in _strip_index(published) and _strip_index(published) not in _strip_index(name):
            gaps.append(f"{label} 成分权重返回的是“{published}”，与跟踪标的{name}不符，未使用")
            return None
        weight_date = max(row["weight_date"] for row in rows)
        if weight_date > as_of_date:
            gaps.append(f"{label} 跟踪指数成分权重为 {weight_date} 的数据，晚于截止日，未使用")
            return None
        return name, weight_date, rows

    def _is_money_fund(self, asset: Asset, allow_remote: bool) -> bool:
        if asset.asset_type != ASSET_TYPE_OPEN_FUND:
            return False
        info, _ = self.fund_facts.dealing_info(asset, allow_remote=allow_remote)
        return info is not None and "货币" in (info.fund_type or "")

    def _industries(self, allow_remote: bool, gaps: list[str]) -> dict[str, str]:
        try:
            return {
                symbol: industry.name
                for symbol, industry in self.sector_data.level1_by_symbol(allow_remote=allow_remote).items()
            }
        except Exception as exc:  # noqa: BLE001 - industries degrade to unclassified
            gaps.append(f"申万一级行业成分不可用，个股行业记为未分类：{type(exc).__name__}: {exc}")
            return {}

    def _benchmark_levels(
        self,
        prices: DataAccessPriceSeriesSource,
        as_of_date: str,
        gaps: list[str],
    ) -> dict[str, float] | None:
        matches = self.master_data.find_assets(standard_symbol=BENCHMARK_SYMBOL, asset_type=ASSET_TYPE_INDEX)
        if not matches:
            gaps.append("本地没有沪深300 指数，历史不足的持仓无法用 Beta 代理")
            return None
        try:
            return self.levels(prices, matches[0], as_of_date, gaps)
        except ValueError as exc:
            gaps.append(f"沪深300 指数取不到价格：{exc}")
            return None

    def _save_valuation(
        self,
        context: UserContext,
        as_of_date: str,
        latest: dict[str, tuple[str, float]],
        gaps: list[str],
    ) -> str | None:
        try:
            valuation = self.portfolio_risk.value_current_holdings(
                context,
                as_of_date=as_of_date,
                prices={key: value[1] for key, value in latest.items()},
            )
        except (KeyError, ValueError) as exc:
            gaps.append(f"组合估值快照未保存：{exc}")
            return None
        return valuation.snapshot.snapshot_id

    def _write_report(self, payload: dict[str, Any]) -> Path:
        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"组合诊断-{safe_user}-{payload['as_of_date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_diagnosis(payload), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return path


def render_diagnosis(payload: dict[str, Any]) -> str:
    exposures, risk, stress = payload["exposures"], payload["risk"], payload["stress"]
    lines = [
        f"# 组合诊断（截止 {payload['as_of_date']}）",
        "",
        "## 主要风险来源",
        "",
        *[f"- {item}" for item in payload["main_risks"]],
        "",
        "## 持仓与权重",
        "",
        "| 持仓 | 市值 | 权重 | 主要归类 | 说明 |",
        "| --- | ---: | ---: | --- | --- |",
        *[
            f"| {item['label']} | {item['market_value']:,.2f} | {item['weight']:.1%} | {item['main_class']} | "
            f"{'；'.join(item['notes'])} |"
            for item in exposures["positions"]
        ],
        "",
        f"总市值 {exposures['total_market_value']:,.2f}；最大单一持仓 {exposures['max_weight']:.1%}；"
        f"持仓 HHI {exposures['concentration_hhi']:.3f}。",
        "",
        "## 大类资产",
        "",
        "| 大类 | 占组合 |",
        "| --- | ---: |",
        *[f"| {item['label']} | {item['weight']:.1%} |" for item in exposures["asset_classes"]],
        "",
        "## 行业暴露（指数基金按成分权重、其他基金按前十大与季报港股行业穿透）",
        "",
        "| 行业 | 占组合 | 占权益 |",
        "| --- | ---: | ---: |",
        *[
            f"| {item['industry']} | {item['weight']:.1%} | "
            f"{'-' if item['share_of_equity'] is None else format(item['share_of_equity'], '.1%')} |"
            for item in exposures["industries"]
        ],
        "",
        "## 重叠",
        "",
    ]
    if exposures["csrc_industries"]:
        lines[-3:-3] = [
            "",
            "主动基金 A 股仓位的证监会行业分布（季报全量，口径与申万不同，用来对照“未穿透”部分大致落在哪里）："
            + "、".join(f"{item['industry']} {item['weight']:.1%}" for item in exposures["csrc_industries"][:8])
            + "。",
        ]
    if exposures["overlaps"]:
        lines += [
            "| 股票 | 合计占组合 | 来源 |",
            "| --- | ---: | --- |",
            *[
                f"| {item['name']} | {item['weight']:.2%} | "
                + "、".join(f"{source['label']} {source['weight']:.2%}" for source in item["sources"])
                + " |"
                for item in exposures["overlaps"]
            ],
        ]
    else:
        lines.append("直接持股与基金前十大持仓之间没有发现同一只股票。")
    for item in exposures["shared_fund_holdings"]:
        lines.append(f"- {item['pair'][0]} 与 {item['pair'][1]} 的穿透持仓共有：{'、'.join(item['common'])}")
    lines += [
        "",
        "穿透后占组合最多的股票："
        + "、".join(f"{item['name']} {item['weight']:.2%}" for item in exposures["top_stocks"])
        + "。",
        "",
        f"## 波动、相关性与风险贡献（{risk['window']['start']} 至 {risk['window']['end']} 日收益）",
        "",
        "组合年化波动 "
        + ("-" if risk["portfolio_volatility"] is None else f"{risk['portfolio_volatility']:.1%}")
        + "（近一年 "
        + ("-" if risk["portfolio_volatility_1y"] is None else f"{risk['portfolio_volatility_1y']:.1%}")
        + "）。",
        "",
        "| 持仓 | 权重 | 年化波动 | 风险贡献占比 |",
        "| --- | ---: | ---: | ---: |",
        *[
            f"| {item['label']} | {item['weight']:.1%} | {item['volatility']:.1%} | {item['risk_share']:.1%} |"
            for item in risk["risk_contributions"]
        ],
        "",
    ]
    if risk["high_correlations"]:
        lines.append(
            "高相关（≥0.8）："
            + "；".join(f"{'/'.join(item['pair'])} {item['correlation']:.2f}" for item in risk["high_correlations"])
            + "。"
        )
    if risk["proxied"]:
        lines.append(f"历史不足、用沪深300 代替的持仓：{'、'.join(risk['proxied'])}。")
    if risk["excluded"]:
        lines.append(
            "历史不足、未计入风险分解的持仓："
            + "、".join(f"{item['label']}（{item['weight']:.1%}）" for item in risk["excluded"])
            + "。"
        )
    lines += [
        "",
        "## 历史压力情景（按持仓实际历史重放）",
        "",
        "| 情景 | 区间 | 沪深300 | 组合 | 未覆盖权重 | Beta 代理的持仓 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
        *[
            f"| {item['label']} | {item['start']}–{item['end']} | "
            f"{'-' if item['benchmark_return'] is None else format(item['benchmark_return'], '.1%')} | "
            f"{item['portfolio_return']:.1%} | {item['uncovered_weight']:.1%} | "
            + "、".join(
                f"{row['label']}（β {row['beta']}）" for row in item["positions"] if row["method"] == "beta_proxy"
            )
            + " |"
            for item in stress
        ],
        "",
    ]
    if payload["preference_checks"]:
        lines += [
            "## 与投资偏好对照",
            "",
            "| 项目 | 当前 | 偏好 | 状态 | 说明 |",
            "| --- | ---: | --- | --- | --- |",
            *[_check_row(item) for item in payload["preference_checks"]],
            "",
        ]
    else:
        lines += ["## 与投资偏好对照", "", "用户还没有记录投资偏好，无法对照。", ""]
    if payload["gaps"]:
        lines += ["## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]], ""]
    lines += ["## 口径与局限", "", *[f"- {item}" for item in payload["limitations"]], ""]
    return "\n".join(lines)


def _check_row(item: dict[str, Any]) -> str:
    return (
        f"| {item['label']} | {_value_text(item)} | {_limit_text(item)} | "
        f"{_STATUS_LABELS[item['status']]} | {item.get('detail', '')} |"
    )


def _value_text(item: dict[str, Any]) -> str:
    value = item.get("value")
    return "-" if value is None else format(value, ".1%")


def _limit_text(item: dict[str, Any]) -> str:
    low, high = item.get("low"), item.get("high")
    if item.get("limit") is not None:
        return f"≤{item['limit']:.0%}"
    if low is not None and high is not None:
        return f"{low:.0%}–{high:.0%}"
    if low is not None:
        return f"≥{low:.0%}"
    if high is not None:
        return f"≤{high:.0%}"
    return "-"


def _main_risks(
    exposures: dict[str, Any],
    risk: dict[str, Any],
    stress: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> list[str]:
    result = []
    contributions = risk["risk_contributions"][:3]
    if contributions:
        result.append(
            "风险贡献最大的持仓："
            + "、".join(f"{item['label']}（权重 {item['weight']:.0%}，风险 {item['risk_share']:.0%}）" for item in contributions)
        )
    industries = [item for item in exposures["industries"] if item["industry"] != UNCOVERED_INDUSTRY][:3]
    if industries:
        result.append("最大的行业暴露：" + "、".join(f"{item['industry']} {item['weight']:.0%}" for item in industries))
    if exposures["overlaps"]:
        top = exposures["overlaps"][0]
        result.append(f"重叠：{top['name']} 通过 {len(top['sources'])} 个持仓合计占组合 {top['weight']:.1%}")
    if risk["high_correlations"]:
        result.append(f"高相关持仓 {len(risk['high_correlations'])} 对，分散效果有限")
    covered = [item for item in stress if item["uncovered_weight"] < 0.05]
    if covered:
        worst = min(covered, key=lambda item: item["portfolio_return"])
        result.append(f"最差历史情景：{worst['label']}，组合 {worst['portfolio_return']:.1%}")
    result += [
        f"与偏好不符：{item['label']} {_value_text(item)}（偏好 {_limit_text(item)}）"
        + (f"，{item['detail']}" if item.get("detail") else "")
        for item in checks
        if item["status"] in ("above", "below")
    ]
    return result


def _limitations() -> list[str]:
    return [
        "指数基金与 ETF 按跟踪指数在中证指数公司公布的月末成分权重全部穿透（只覆盖已登记的中证、上证系列指数，返回的指数名称须与跟踪标的一致）；其余基金按最近一期已披露季报（季末后 25 天视为可得）的前十大股票穿透，港股部分按季报港股行业配置归类，A 股其余部分记为“未穿透”，并给出季报证监会行业分布作对照；季报之后的调仓看不到。",
        "基金大类按季报“股票、债券、现金占净比”归一拆分；货币、QDII、商品和固收指数基金整体归类。",
        "港股行业为季报的港股行业分类（接近 GICS），不是申万；美股持仓不分行业；个股行业为申万一级当前归属。",
        "波动与相关性用近 3 年日收益（每年 252 个交易日），并给出近 1 年组合波动对照；历史不足 120 个交易日的权益类持仓用沪深300 代替。",
        f"压力情景为固定的 {len(STRESS_SCENARIOS)} 段历史区间，按持仓实际历史重放；区间内没有历史的持仓用近 3 年对沪深300 的 Beta 估算，现金类按 0 计。这是历史重演，不是对未来跌幅的预测。",
        "价格为截止日或之前最近一个交易日的收盘价或单位净值；货币基金按每份 1 元估值，与现金一起按零波动计。",
    ]


def _strip_index(name: str) -> str:
    return re.sub(r"指数|中证|\s", "", name)


def _label(asset: Asset) -> str:
    return f"{asset.display_name or asset.standard_symbol}（{asset.standard_symbol}）"
