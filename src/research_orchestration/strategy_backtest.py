"""Backtest an archived strategy through the unified target-weight entry (stage 9 slice 2).

Loads total-return bars for the archive's universe, builds signals with the strategy's generator,
runs the strategy and four simple benchmarks (buy and hold, monthly equal weight, CSI 300, cash)
with the same costs, estimates capacity, and logs the run as an attempt of the strategy family.

Design notes: docs/modules/3-分析工具/低频回测模块.md「统一回测入口」.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from src.backtest.significance import significance
from src.backtest.target_weights import (
    CAPACITY_PARTICIPATION,
    PriceBar,
    TargetSignal,
    buy_and_hold_signals,
    cost_model_for,
    month_end_dates,
    performance_metrics,
    run_target_weights,
)
from src.data_access import (
    DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_src.service import DataFetchService
from src.macro_data import MacroDataService
from src.master_data import MasterDataService
from src.strategy import SQLiteStrategyArchiveRepository, StrategyArchive
from src.strategy.selection import factor_selection_weights
from src.strategy.signals import GeneratorContext, signal_generator_for

from .stock_pool import StockPoolError, StockPoolService
from .strategy_archive import StrategyArchiveError, StrategyArchiveService

_DATASETS = {
    "stock": DATASET_STOCK_TOTAL_RETURN_BARS,
    "exchange_fund": DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
    "index": DATASET_INDEX_DAILY_BARS,
}
DEFAULT_START = "2005-01-01"
CSI300 = "000300"
CASH_SERIES = "shibor_3m"
SELECTION_TEMPLATE = "factor_selection"
_RANK_TEXT = {
    "earnings_yield": "市盈率倒数",
    "book_yield": "市净率倒数",
    "roe": "ROE",
    "profit_growth": "净利润增长",
}
MAX_LISTED = 30


class StrategyBacktestService:
    def __init__(
        self,
        *,
        archives: StrategyArchiveService,
        repository: SQLiteStrategyArchiveRepository,
        data_access: DataAccessService,
        data_fetch: DataFetchService,
        master_data: MasterDataService,
        macro_data: MacroDataService,
        pools: StockPoolService | None = None,
    ) -> None:
        self.archives = archives
        self.repository = repository
        self.data_access = data_access
        self.data_fetch = data_fetch
        self.master_data = master_data
        self.macro_data = macro_data
        self.pools = pools

    def run(
        self,
        strategy_id: str,
        *,
        as_of_date: str,
        version: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        overrides: dict[str, str] | None = None,
        initial_cash: float = 1_000_000.0,
        lots: bool = False,
        allow_remote: bool = True,
        record: bool = True,
    ) -> dict[str, Any]:
        archive, state, parameters = self.resolve(strategy_id, as_of_date=as_of_date, version=version, overrides=overrides)
        end = end_date or as_of_date
        start = start_date or DEFAULT_START
        gaps: list[str] = []
        prepared = self.prepare(
            archive, parameters, start=start, end=end, initial_cash=initial_cash, lots=lots, allow_remote=allow_remote, gaps=gaps
        )
        inputs, assets, signals = prepared.inputs, prepared.inputs.assets, prepared.inputs.signals
        effective_start, costs, bars, common = prepared.start, prepared.costs, prepared.bars, prepared.common
        result = run_target_weights(bars, signals, **common)
        benchmarks = self._benchmarks(assets, inputs, result, common, bars, allow_remote, gaps)
        metrics = result.metrics()
        summary = {
            "metrics": metrics,
            "benchmarks": {name: item["metrics"] for name, item in benchmarks.items()},
            "capacity": result.capacity(),
        }
        attempt_id = None
        if record:
            attempt_id = self.repository.record_backtest(
                archive,
                run_on=as_of_date,
                start_date=effective_start,
                end_date=end,
                parameters=parameters,
                summary=summary,
            )
        attempts = self.repository.backtests(archive.family)
        trials = _trial_sharpes(attempts, (parameters, effective_start, end, metrics.get("sharpe")))
        return {
            "strategy_id": archive.strategy_id,
            "version": archive.version,
            "name": archive.name,
            "lifecycle_state": state.value,
            "generator": inputs.description,
            "sample": {
                "start": effective_start,
                "end": end,
                "pool": inputs.pool,
                "universe": [
                    {key: item[key] for key in ("symbol", "name", "asset_type", "role", "first_date")}
                    for item in assets.values()
                ][:MAX_LISTED],
                "universe_size": len(assets),
            },
            "parameters": parameters,
            "overrides": overrides or {},
            "signal_count": len([s for s in signals if s.as_of_date >= effective_start]),
            "selection": inputs.selection_summary,
            "metrics": metrics,
            "benchmarks": benchmarks,
            "comparison": _comparison(metrics, benchmarks),
            "capacity": summary["capacity"],
            "costs": _cost_text(costs, assets, lots),
            "attempt_id": attempt_id,
            "family_attempts": len(attempts),
            "significance": significance(result.excess_returns(), trials),
            "gaps": gaps,
            "limitations": [
                *archive.limitations,
                *inputs.notes,
                *_limitations(assets, selection=inputs.selection_summary is not None),
            ],
        }

    def resolve(
        self, strategy_id: str, *, as_of_date: str, version: str | None = None, overrides: dict[str, str] | None = None
    ) -> tuple[StrategyArchive, Any, dict[str, str]]:
        self.archives.ensure_defaults(as_of_date)
        found = self.repository.get(strategy_id, version)
        if found is None:
            raise StrategyArchiveError(f"没有策略档案 {strategy_id}；用 strategy-archive 列出已登记的策略")
        archive, state, _ = found
        unknown = sorted(set(overrides or {}) - set(archive.parameters))
        if unknown:
            raise StrategyArchiveError(f"档案没有这些参数：{', '.join(unknown)}")
        return archive, state, {**archive.parameters, **(overrides or {})}

    def prepare(
        self,
        archive: StrategyArchive,
        parameters: dict[str, str],
        *,
        start: str,
        end: str,
        initial_cash: float = 1_000_000.0,
        lots: bool = False,
        allow_remote: bool = True,
        gaps: list[str],
    ) -> "PreparedRun":
        """Data, signals, costs and cash rates for one parameter set; the start moves to the first tradable signal."""

        if archive.template_id == SELECTION_TEMPLATE:
            inputs = self._selection_inputs(archive, parameters, start, end, allow_remote, gaps)
        else:
            inputs = self._generator_inputs(archive, parameters, start, end, allow_remote, gaps)
        assets, signals = inputs.assets, inputs.signals
        calendar = sorted({bar.trade_date for item in assets.values() for bar in item["bars"]})
        first_trade = next((day for day in calendar if day > signals[0].as_of_date), None)
        effective_start = max(start, first_trade or start)
        if effective_start > start:
            gaps.append(f"首个信号在 {signals[0].as_of_date}，回测从 {effective_start} 开始（之前历史不足以计算参数窗口，或标的尚无行情）")
        costs = {asset_id: cost_model_for(item["asset_type"], lots=lots) for asset_id, item in assets.items()}
        cash_rates = self._cash_rates(inputs.data_start, end, allow_remote, gaps)
        if len(cash_rates) > 1 and cash_rates[0][0] == "1900-01-01" and cash_rates[1][0] > effective_start:
            gaps.append(f"{cash_rates[1][0]} 前没有 Shibor，闲置现金与现金基准按 {cash_rates[1][1]:.2%} 计息")
        return PreparedRun(
            inputs=inputs,
            bars=[bar for item in assets.values() for bar in item["bars"]],
            costs=costs,
            cash_rates=cash_rates,
            start=effective_start,
            end=end,
            initial_cash=initial_cash,
        )

    def simple_benchmark(self, prepared: "PreparedRun", start: str) -> tuple[str, list[TargetSignal]]:
        """The simple alternative a strategy must not lose to: pool equal weight for pools and sets, else buy and hold."""

        inputs = prepared.inputs
        before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        if inputs.selection_summary is None and len(inputs.members_on(prepared.end)) <= 1:
            return "buy_and_hold", buy_and_hold_signals(inputs.members_on(start), before)
        calendar = sorted({bar.trade_date for bar in prepared.bars if bar.trade_date >= start})
        signals = []
        for day in [before, *month_end_dates(calendar)]:
            members = inputs.members_on(max(day, start))
            if members:
                signals.append(TargetSignal(day, {a: 1 / len(members) for a in members}, "等权再平衡"))
        return "equal_weight", signals

    def market_series(
        self, start: str, end: str, *, allow_remote: bool, gaps: list[str]
    ) -> tuple[list[tuple[str, float]], list[tuple[str, str, float]]]:
        """CSI 300 closes and P/E points with enough history before `start` for the market-state definitions."""

        year = int(start[:4])
        index = self._load(CSI300, "index", f"{year - 5}{start[4:]}", end, allow_remote, gaps)
        closes = [(bar.trade_date, bar.close) for bar in index["bars"]] if index else []
        try:
            valuation = self._valuation(
                {"valuation_series": "csi300_pe_ttm", "lookback_years": "15"}, start, end, allow_remote, gaps
            )
        except StrategyArchiveError as exc:
            gaps.append(str(exc))
            valuation = []
        return closes, valuation

    def record_trial(
        self, archive: StrategyArchive, *, run_on: str, parameters: dict[str, str], start: str, end: str, metrics: dict[str, Any]
    ) -> str:
        return self.repository.record_backtest(
            archive, run_on=run_on, start_date=start, end_date=end, parameters=parameters, summary={"metrics": metrics}
        )

    def _generator_inputs(
        self, archive: StrategyArchive, parameters: dict[str, str], start: str, end: str, allow_remote: bool, gaps: list[str]
    ) -> "_Inputs":
        generator = signal_generator_for(archive.strategy_id, archive.template_id)
        if generator is None:
            raise StrategyArchiveError(
                f"{archive.name} 还没有可回测的信号实现：单标的条件规则需要逐日因子输入，按阶段 9 计划以常见模板接入"
            )
        if not archive.universe_assets:
            raise StrategyArchiveError(f"{archive.name} 的档案没有 universe_assets，无法确定回测标的")
        data_start = (date.fromisoformat(start) - timedelta(days=int(generator.warmup_days(parameters) * 1.5) + 30)).isoformat()
        assets = self._load_universe(archive, data_start, end, allow_remote, gaps)
        if not assets:
            raise StrategyArchiveError("回测标的都取不到行情：" + "；".join(gaps))
        context = GeneratorContext(
            roles={item["role"]: item["asset_id"] for item in assets.values() if item["role"]},
            symbols={asset_id: item["symbol"] for asset_id, item in assets.items()},
            start=start,
            valuation=self._valuation(parameters, start, end, allow_remote, gaps) if generator.needs_valuation else (),
        )
        series = {asset_id: [(bar.trade_date, bar.close) for bar in item["bars"]] for asset_id, item in assets.items()}
        calendar = sorted({day for points in series.values() for day, _ in points})
        raw_signals = generator.build(series, calendar, parameters, context)
        if not raw_signals:
            raise StrategyArchiveError("样本内没有生成任何信号（历史长度不足以覆盖参数窗口）")
        risk = [asset_id for asset_id, item in assets.items() if item["role"] != "defensive"]
        return _Inputs(
            assets=assets,
            signals=[TargetSignal(day, weights, reason) for day, weights, reason in raw_signals],
            description=generator.description,
            data_start=data_start,
            members_on=lambda day: [asset_id for asset_id in risk if assets[asset_id]["first_date"] <= day],
            pool=None,
            notes=(),
            selection_summary=None,
        )

    def _selection_inputs(
        self, archive: StrategyArchive, parameters: dict[str, str], start: str, end: str, allow_remote: bool, gaps: list[str]
    ) -> "_Inputs":
        scope = parameters.get("scope")
        if not scope:
            raise StrategyArchiveError(f"{archive.name} 的参数里没有 scope（sw:<申万代码> 或 symbols:<代码,...>）")
        try:
            pool = self.pools.resolve(scope, allow_remote=allow_remote)
        except (StockPoolError, KeyError) as exc:
            raise StrategyArchiveError(str(exc)) from exc
        gaps.extend(pool.gaps)
        data_start = (date.fromisoformat(start) - timedelta(days=45)).isoformat()
        assets: dict[str, dict[str, Any]] = {}
        by_symbol: dict[str, str] = {}
        missing = []
        for symbol in pool.symbols:
            loaded = self._load(symbol, "stock", data_start, end, allow_remote, [])
            if loaded is None:
                missing.append(symbol)
                continue
            loaded["role"] = None
            assets[loaded["asset_id"]] = loaded
            by_symbol[symbol] = loaded["asset_id"]
        if missing:
            gaps.append(
                f"{len(missing)} 只取不到行情，回测中视为无法参与：{', '.join(missing[:20])}"
                + "（其中已退市的会让结果偏乐观）"
            )
        if not assets:
            raise StrategyArchiveError("股票池里没有任何一只取得到行情")
        fundamentals = self.pools.annual_fundamentals(int(start[:4]) - 2, int(end[:4]), allow_remote=allow_remote)
        gaps.extend(fundamentals.errors)
        calendar = sorted({bar.trade_date for item in assets.values() for bar in item["bars"]})
        rebalance = [day for day in month_end_dates(calendar) if day >= data_start]
        signals, counts_by_month = [], []
        for day in rebalance:
            candidates = []
            for symbol in pool.members_on(day):
                asset_id = by_symbol.get(symbol)
                close = assets[asset_id]["raw_closes"].get(day) if asset_id else None
                if close is None:
                    continue
                row = fundamentals.as_of(symbol, day) or {}
                eps, bvps = row.get("eps"), row.get("bvps")
                candidates.append(
                    {
                        "asset_id": asset_id,
                        "roe_percent": row.get("roe_percent"),
                        "debt_ratio_percent": row.get("debt_ratio_percent"),
                        "net_profit_yoy_percent": row.get("net_profit_yoy_percent"),
                        "earnings_yield": eps / close if eps is not None and close else None,
                        "book_yield": bvps / close if bvps is not None and close else None,
                    }
                )
            weights, counts = factor_selection_weights(candidates, parameters)
            counts_by_month.append(counts)
            signals.append(TargetSignal(day, weights, f"候选 {counts['candidates']}，入选 {counts['selected']}"))
        if not signals:
            raise StrategyArchiveError("样本内没有调仓日")

        def members_on(day: str) -> list[str]:
            return [
                by_symbol[symbol]
                for symbol in pool.members_on(day)
                if symbol in by_symbol and assets[by_symbol[symbol]]["first_date"] <= day
            ]

        in_sample = counts_by_month[-len([s for s in signals if s.as_of_date >= start]) or None:]
        return _Inputs(
            assets=assets,
            signals=signals,
            description=(
                "因子选股：月末用已公告的最新年报筛选 ROE、负债率与利润增长，"
                f"按{_RANK_TEXT[parameters.get('rank_by', 'earnings_yield')]}从高到低取前若干只等权持有"
            ),
            data_start=data_start,
            members_on=members_on,
            pool=pool.label,
            notes=pool.notes,
            selection_summary=_selection_summary(in_sample),
        )

    def _load_universe(
        self, archive: StrategyArchive, start: str, end: str, allow_remote: bool, gaps: list[str]
    ) -> dict[str, dict[str, Any]]:
        assets: dict[str, dict[str, Any]] = {}
        for item in archive.universe_assets:
            symbol, asset_type, *rest = item.split(":")
            loaded = self._load(symbol, asset_type, start, end, allow_remote, gaps)
            if loaded is not None:
                loaded["role"] = rest[0] if rest else None
                assets[loaded["asset_id"]] = loaded
        return assets

    def _load(
        self, symbol: str, asset_type: str, start: str, end: str, allow_remote: bool, gaps: list[str]
    ) -> dict[str, Any] | None:
        found = self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
        if not found and allow_remote:
            try:
                self.data_fetch.fetch_and_store_identities(symbol, asset_type=asset_type)
            except (OSError, RuntimeError, ValueError) as exc:
                gaps.append(f"{symbol} 身份登记失败：{exc}")
                return None
            found = self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
        if not found:
            gaps.append(f"{symbol} 没有登记的{asset_type}身份")
            return None
        asset = found[0]
        listed = next(
            (row["listed_on"] for row in self.data_fetch.store.get_exchange_listings(code=symbol)
             if row["asset_type"] == asset_type and row["listed_on"]),
            None,
        )
        try:
            response = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset.asset_id),
                    dataset=_DATASETS[asset_type],
                    start_date=max(start, listed) if listed else start,
                    end_date=end,
                    as_of_date=end,
                    allow_remote=allow_remote,
                    provider_timeout_seconds=180.0,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            gaps.append(f"{symbol} 行情取不到：{exc}")
            return None
        bars = _bars(asset.asset_id, symbol, asset_type, response.records)
        if not bars:
            gaps.append(f"{symbol} 区间内没有行情")
            return None
        if response.error:
            gaps.append(f"{symbol} 行情不完整：{response.error}")
        return {
            "asset_id": asset.asset_id,
            "symbol": symbol,
            "name": asset.display_name,
            "asset_type": asset_type,
            "first_date": bars[0].trade_date,
            "bars": bars,
            "raw_closes": {
                str(record["trade_date"])[:10]: float(record.get("unadjusted_close") or record["close"])
                for record in response.records
                if record.get("close")
            },
        }

    def _valuation(
        self, parameters: dict[str, str], start: str, end: str, allow_remote: bool, gaps: list[str]
    ) -> list[tuple[str, str, float]]:
        series_id = parameters["valuation_series"]
        first = f"{int(start[:4]) - int(parameters['lookback_years']) - 1}-01-01"
        try:
            result = self.macro_data.get(series_id, start_date=first, end_date=end, as_of_date=end, allow_remote=allow_remote)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            raise StrategyArchiveError(f"估值序列 {series_id} 取不到：{exc}") from exc
        if not result.points:
            raise StrategyArchiveError(f"估值序列 {series_id} 在区间内没有数据")
        if result.points[0].period_end[:4] > first[:4]:
            gaps.append(f"估值序列 {series_id} 从 {result.points[0].period_end} 开始，之前无法计算分位")
        return [(point.available_at, point.period_end, point.value) for point in result.points]

    def _cash_rates(self, start: str, end: str, allow_remote: bool, gaps: list[str]) -> list[tuple[str, float]]:
        try:
            result = self.macro_data.get(CASH_SERIES, start_date=start, end_date=end, as_of_date=end, allow_remote=allow_remote)
            points = [(p["period"], float(p["value"]) / 100) for p in result.to_dict(limit=0)["points"]]
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            gaps.append(f"现金利率（Shibor 3 个月）取不到，闲置现金按 0 计息：{exc}")
            return []
        if points and points[0][0] > start:
            points.insert(0, ("1900-01-01", points[0][1]))
        return points

    def _benchmarks(
        self,
        assets: dict[str, dict[str, Any]],
        inputs: "_Inputs",
        result: Any,
        common: dict[str, Any],
        bars: list[PriceBar],
        allow_remote: bool,
        gaps: list[str],
    ) -> dict[str, dict[str, Any]]:
        start = common["start_date"]
        before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        tradable = inputs.members_on(start)
        everyone = {asset_id for day in month_end_dates(list(result.dates)) for asset_id in inputs.members_on(day)}
        late = sorted(assets[asset_id]["symbol"] for asset_id in everyone - set(tradable))
        if late and inputs.selection_summary is None:
            gaps.append(f"买入持有基准只含期初已有行情的标的；{', '.join(late)} 在期初之后才有行情，只进入等权基准")
        output: dict[str, dict[str, Any]] = {}
        hold = run_target_weights(bars, buy_and_hold_signals(tradable, before), **common)
        output["buy_and_hold"] = {"label": "同标的买入持有（期初等权，不再平衡）", "metrics": hold.metrics()}
        if len(everyone) > 1 and tradable:
            equal_signals = [TargetSignal(before, {a: 1 / len(tradable) for a in tradable}, "等权")]
            for day in month_end_dates(list(result.dates)):
                members = inputs.members_on(day)
                if members:
                    equal_signals.append(TargetSignal(day, {a: 1 / len(members) for a in members}, "等权再平衡"))
            equal = run_target_weights(bars, equal_signals, **common)
            output["equal_weight"] = {"label": "标的池等权（月末再平衡，只含当时在市的标的）", "metrics": equal.metrics()}
        index = self._load(CSI300, "index", start, common["end_date"], allow_remote, gaps)
        if index is not None:
            closes = {bar.trade_date: bar.close for bar in index["bars"] if bar.trade_date >= start}
            dates = [day for day in result.dates if day in closes]
            output["csi300"] = {
                "label": "沪深300 价格指数（不含分红）",
                "metrics": performance_metrics(dates, [closes[day] for day in dates]),
            }
        cash_curve, value = [], 1.0
        for daily in result.cash_returns:
            value *= 1 + daily
            cash_curve.append(value)
        output["cash"] = {"label": "现金（Shibor 3 个月）", "metrics": performance_metrics(result.dates, cash_curve)}
        return output


def _trial_sharpes(attempts: list[dict[str, Any]], current: tuple[dict[str, str], str, str, Any]) -> list[float]:
    """Annual Sharpe of each distinct trial (parameters and sample) in the family; a rerun replaces, not adds.
    A trial without a Sharpe (it never moved away from cash) counts with a Sharpe of zero."""

    by_trial: dict[str, float] = {}
    for item in attempts:
        sharpe = item["summary"].get("metrics", {}).get("sharpe")
        by_trial[_trial_key(item["parameters"], item["start_date"], item["end_date"])] = float(sharpe or 0.0)
    parameters, start, end, sharpe = current
    by_trial[_trial_key(parameters, start, end)] = float(sharpe or 0.0)
    return list(by_trial.values())


def _trial_key(parameters: dict[str, str], start: str, end: str) -> str:
    return json.dumps([sorted(parameters.items()), start, end], ensure_ascii=False)


def _bars(asset_id: str, symbol: str, asset_type: str, records: list[dict[str, Any]]) -> list[PriceBar]:
    limit = _price_limit(symbol, asset_type)
    bars = []
    for record in records:
        open_, close, volume = record.get("open"), record.get("close"), record.get("volume")
        if not open_ or not close or (volume is not None and float(volume) <= 0):
            continue
        factor = float(record.get("adjustment_factor") or 1.0)
        raw_open = float(open_) / factor
        preclose = record.get("preclose")
        move = raw_open / float(preclose) - 1 if limit and preclose else 0.0
        day = str(record["trade_date"])[:10]
        bars.append(
            PriceBar(
                asset_id=asset_id,
                trade_date=day,
                open=float(open_),
                close=float(close),
                raw_open=raw_open if asset_type != "index" else None,
                amount=float(record["amount"]) if record.get("amount") and asset_type != "index" else None,
                can_buy=not (limit and move >= limit(day) - 0.002),
                can_sell=not (limit and move <= -limit(day) + 0.002),
            )
        )
    return sorted(bars, key=lambda bar: bar.trade_date)


def _price_limit(symbol: str, asset_type: str):
    """Daily limit by board and date; ST stocks (5%) are not identified and use the board limit."""

    if asset_type == "index":
        return None
    if asset_type == "exchange_fund":
        return lambda day: 0.10
    if symbol.startswith("688"):
        return lambda day: 0.20
    if symbol.startswith(("300", "301")):
        return lambda day: 0.20 if day >= "2020-08-24" else 0.10
    if symbol.startswith(("4", "8", "92")):
        return lambda day: 0.30
    return lambda day: 0.10


def _comparison(metrics: dict[str, Any], benchmarks: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for item in benchmarks.values():
        other = item["metrics"]
        if metrics.get("annual_return") is None or other.get("annual_return") is None:
            continue
        sharpe = (
            f"夏普 {metrics['sharpe']} 对 {other['sharpe']}，"
            if metrics.get("sharpe") is not None and other.get("sharpe") is not None
            else ""
        )
        lines.append(
            f"相对{item['label']}：年化 {metrics['annual_return']:.2%} 对 {other['annual_return']:.2%}"
            f"（{(metrics['annual_return'] - other['annual_return']) * 100:+.2f} 个百分点），{sharpe}"
            f"最大回撤 {metrics['max_drawdown']:.1%} 对 {other['max_drawdown']:.1%}，"
            f"波动 {metrics['annual_volatility']:.1%} 对 {other['annual_volatility']:.1%}"
        )
    return lines


def _cost_text(costs: dict[str, Any], assets: dict[str, dict[str, Any]], lots: bool) -> list[str]:
    kinds = {assets[asset_id]["asset_type"] for asset_id in costs}
    lines = ["佣金万分之 2.5，每笔最低 5 元；滑点万分之 5；信号日收盘决定、下一交易日开盘成交"]
    if "stock" in kinds:
        lines.append("股票卖出印花税按当时税率（2008-09-19 起千分之一、2023-08-28 起万分之五；更早双边收取）")
    lines.append("按每手 100 股（份）取整" if lots else "按金额成交，不取整（--lots 可按整手）")
    lines.append("闲置现金按 Shibor 3 个月计息")
    return lines


def _limitations(assets: dict[str, dict[str, Any]], *, selection: bool = False) -> list[str]:
    lines = [
        "这是单次回测：参数没有经过滚动样本外与邻域检验，不能据此说策略有效；显著性折扣只计入经正式入口登记的尝试，系统外的试错不在其中",
        f"容量按“95% 分位的单笔成交占当日成交额 {CAPACITY_PARTICIPATION:.0%}”线性外推，忽略冲击成本随规模的非线性变化",
        "涨跌停按开盘价相对前收盘判断；ST 股的 5% 限制未识别",
        "沪深300 基准是价格指数，不含分红，长期年化约低估 1–2 个百分点",
    ]
    if selection:
        lines += [
            "财务数据按公告日使用：每个调仓日只用当时已公告的最新年报；修订过的年报以修订日为准，会把部分公司推迟一年用上（偏保守）",
            "市盈率用年报每股收益除以当日未复权收盘价；年报后送转会使当年每股收益偏大、市盈率偏低",
            "年报 ROE 与利润增长来自东方财富业绩报表，已退市公司多数不在该来源中",
        ]
    elif any(item["role"] != "defensive" for item in assets.values()) and len(assets) > 1:
        lines.append("标的池是当前选定的固定集合，存在事后选择偏差")
    return lines


def _selection_summary(counts: list[dict[str, int]]) -> dict[str, Any]:
    if not counts:
        return {}
    months = len(counts)
    return {
        "rebalances": months,
        "average_candidates": round(sum(c["candidates"] for c in counts) / months, 1),
        "average_selected": round(sum(c["selected"] for c in counts) / months, 1),
        "months_all_cash": sum(1 for c in counts if not c["selected"]),
        "average_no_data": round(sum(c["no_data"] for c in counts) / months, 1),
        "average_failed_quality": round(sum(c["failed_quality"] for c in counts) / months, 1),
    }


@dataclass(frozen=True)
class PreparedRun:
    inputs: "_Inputs"
    bars: list[PriceBar]
    costs: dict[str, Any]
    cash_rates: list[tuple[str, float]]
    start: str
    end: str
    initial_cash: float

    @property
    def common(self) -> dict[str, Any]:
        return {
            "start_date": self.start,
            "end_date": self.end,
            "initial_cash": self.initial_cash,
            "costs": self.costs,
            "cash_rates": self.cash_rates,
        }

    def run(self, signals: list[TargetSignal] | None = None, *, start: str | None = None, end: str | None = None) -> Any:
        common = {**self.common, "start_date": start or self.start, "end_date": end or self.end}
        return run_target_weights(self.bars, self.inputs.signals if signals is None else signals, **common)


@dataclass(frozen=True)
class _Inputs:
    assets: dict[str, dict[str, Any]]
    signals: list[TargetSignal]
    description: str
    data_start: str
    members_on: Callable[[str], list[str]]
    pool: str | None
    notes: tuple[str, ...]
    selection_summary: dict[str, Any] | None
