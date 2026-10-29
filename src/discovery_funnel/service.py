"""Multi-strategy discovery funnel: scope → cross-section → strategy ranking → candidates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any

from src.cross_section import CrossSectionService
from src.cross_section.service import FINANCIAL_BASIS_NOTE
from src.sector_data import SectorDataService

from .features import (
    Financials,
    MemberFeatures,
    ValuationSeries,
    build_features,
    exclusion,
    one_off_suspicion,
    shift_quarter,
)
from .history import RETURN_BASIS, replay, scope_context
from .sqlite_repository import SQLiteDiscoveryFunnelRepository
from .strategies import STRATEGIES, STRATEGY_VERSION, Strategy, StrategyResult, strategy

FUNNEL_VERSION = "discovery_funnel_v1"
# Member-level history (own valuation percentile, drawdown, rule replay) is fetched per stock.
MAX_HISTORY_SCOPE = 300
DEFAULT_BASE_PACKS = 5
# Latest period plus the lookbacks TTM and single-quarter growth need (up to eight quarters).
_CURRENT_PERIODS = 13
# Replay starts in 2019; TTM and single-quarter figures then need reports back to mid-2016.
_HISTORY_FIRST_PERIOD = "2016-06-30"
_VALUATION_START = "2018-01-01"
_SCOPE_KINDS = ("industry", "index", "market", "symbols")

IndexMembers = Callable[[str], list[tuple[str, str]]]
# (symbol, as_of_date) -> recurring_profit_summary payload, or None when unavailable.
RecurringLookup = Callable[[str, str], dict[str, Any] | None]
# Candidates checked against per-stock 扣非 profit, per base pack requested.
RECURRING_CHECK_FACTOR = 3


@dataclass(frozen=True)
class DiscoveryScope:
    kind: str
    code: str | None = None
    symbols: tuple[str, ...] = ()
    label: str | None = None
    origin: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _SCOPE_KINDS:
            raise ValueError(f"Unknown scope kind: {self.kind}")
        if self.kind in ("industry", "index") and not self.code:
            raise ValueError(f"Scope {self.kind} needs a code")
        if self.kind == "symbols" and not self.symbols:
            raise ValueError("Scope symbols needs at least one symbol")

    @classmethod
    def parse(cls, text: str, *, label: str | None = None) -> "DiscoveryScope":
        """`industry:801120`, `index:000300`, `market` or `symbols:600519,000858`."""

        kind, _, rest = text.strip().partition(":")
        if kind == "symbols":
            symbols = tuple(item.strip().zfill(6) for item in rest.split(",") if item.strip())
            return cls(kind, symbols=symbols, label=label)
        return cls(kind, code=rest.strip() or None, label=label)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "code": self.code,
            "symbols": list(self.symbols),
            "label": self.label,
            "origin": self.origin,
        }


class DiscoveryFunnelService:
    def __init__(
        self,
        *,
        cross_section: CrossSectionService,
        sector_data: SectorDataService,
        repository: SQLiteDiscoveryFunnelRepository,
        index_members: IndexMembers | None = None,
        recurring_lookup: RecurringLookup | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.cross_section = cross_section
        self.sector_data = sector_data
        self.repository = repository
        self.index_members = index_members
        self.recurring_lookup = recurring_lookup
        self.clock = clock
        self.repository.initialize()

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get(run_id)

    def save(self, payload: dict[str, Any]) -> None:
        self.repository.save(payload)

    def run(
        self,
        scope: DiscoveryScope,
        *,
        as_of_date: str,
        strategy_ids: tuple[str, ...] | None = None,
        base_pack_count: int = DEFAULT_BASE_PACKS,
        history: bool = True,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        strategies = tuple(strategy(i) for i in strategy_ids) if strategy_ids else STRATEGIES
        errors: dict[str, Any] = {}
        gaps: list[str] = []
        label, names, notes = self._scope_members(scope, allow_remote)
        gaps.extend(notes)
        try:
            level1 = {
                symbol: item.name
                for symbol, item in self.sector_data.level1_by_symbol(allow_remote=allow_remote).items()
            }
        except Exception as exc:  # noqa: BLE001 - exclusions fall back to no industry
            level1 = {}
            errors["shenwan_level1"] = f"{type(exc).__name__}: {exc}"
            gaps.append("申万一级行业不可用：金融与周期公司未能排除")
        member_history = scope.kind != "market" and len(names) <= MAX_HISTORY_SCOPE
        financials = self._financials(as_of_date, member_history and history, allow_remote, errors)
        for symbol in names:
            if not names[symbol]:
                row = financials.latest(symbol, "9999-12-31")
                names[symbol] = row.get("name", "") if row else ""
        valuations: dict[str, ValuationSeries] = {}
        snapshot_rows: dict[str, dict[str, Any]] = {}
        data_basis: dict[str, Any] = {"financials": FINANCIAL_BASIS_NOTE}
        if member_history:
            result = self.cross_section.valuation_history(
                sorted(names), start_date=_VALUATION_START, end_date=as_of_date, allow_remote=allow_remote
            )
            valuations = {s: ValuationSeries(rows) for s, rows in result["series"].items() if rows}
            if result["provider_errors"]:
                errors["valuation_history"] = result["provider_errors"]
            data_basis["valuation"] = "东方财富个股估值日频（2018 年起）：截止日当天或之前最近一个交易日"
        else:
            snapshot = self.cross_section.market_snapshot(allow_remote=allow_remote)
            trade_date = snapshot.get("trade_date") or snapshot["fetched_at"][:10]
            if as_of_date < trade_date:
                raise ValueError(
                    f"范围 {len(names)} 只超过 {MAX_HISTORY_SCOPE} 只（或为全市场），只有当前截面（{trade_date}），"
                    "截止日不能早于截面日期；缩小范围可做时点一致的回溯"
                )
            snapshot_rows = {row["symbol"]: row for row in snapshot["rows"]}
            data_basis["valuation"] = f"当前全市场截面 {snapshot['snapshot_id']}（新浪行情 + 腾讯估值，{trade_date}）"
            data_basis["snapshot_id"] = snapshot["snapshot_id"]
            gaps.append(
                f"范围超过 {MAX_HISTORY_SCOPE} 只或为全市场：不取逐只估值历史，"
                "依赖历史的策略与规则历史表现未计算"
            )
        features = {
            symbol: build_features(
                symbol=symbol,
                name=name,
                industry_l1=level1.get(symbol),
                financials=financials,
                on=as_of_date,
                valuation=valuations.get(symbol),
                snapshot_row=snapshot_rows.get(symbol) if not member_history else None,
            )
            for symbol, name in sorted(names.items())
        }
        excluded = {s: reason for s, f in features.items() if (reason := exclusion(f)) is not None}
        evaluable = [f for s, f in features.items() if s not in excluded]
        context = scope_context(evaluable)
        active = tuple(item for item in strategies if member_history or not item.needs_history)
        skipped = [item for item in strategies if item not in active]
        results: dict[str, dict[str, StrategyResult]] = {
            f.symbol: {item.strategy_id: item.evaluate(f, context) for item in active} for f in evaluable
        }
        candidates = _rank_candidates(features, results, active)
        checked = self._check_recurring(candidates, as_of_date, base_pack_count * RECURRING_CHECK_FACTOR)
        rule_history: dict[str, Any] = {}
        if member_history and history and active:
            rule_history = replay(
                strategies=active,
                members={s: (n, level1.get(s)) for s, n in names.items()},
                valuations=valuations,
                financials=financials,
                as_of_date=as_of_date,
            )
        elif not history:
            gaps.append("本次未计算规则历史表现（--no-history）")
        if member_history:
            gaps.append(
                "规则历史表现用当前成分与当前名称回放：已退出范围或退市的公司不在样本内（幸存者偏差，结果偏乐观）"
            )
        gaps.append(
            "ROE 与经营现金流用 TTM（本期累计 + 上一年报 − 去年同期累计），"
            "盈利拐点用单季同比；TTM 缺数据时回退并在候选的 data_gaps 标注"
        )
        gaps.append(
            "截面财报没有扣非利润："
            + (
                f"排序前列的 {checked} 个候选已逐只用利润表的扣非归母净利润（TTM）核对，其余候选仍按启发式标注；"
                if checked
                else ""
            )
            + "疑似非经常性损益的候选排在最后且不生成基础包"
        )
        disclosure = _disclosure_note(evaluable)
        if disclosure:
            gaps.append(disclosure)
        weak = [
            item.name
            for item in active
            if (stats := (rule_history.get(item.strategy_id) or {}).get("6m"))
            and stats.get("samples")
            and (stats["mean_excess"] <= 0 or stats["insufficient_samples"])
        ]
        if weak:
            gaps.append(
                f"本范围内 {'、'.join(weak)} 的 6 个月历史超额不为正或样本不足：命中这些策略不是更值得推荐的理由"
            )
        payload = {
            "version": FUNNEL_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "as_of_date": as_of_date,
            "scope": {**scope.to_dict(), "label": label, "member_count": len(names)},
            "data_basis": data_basis,
            "context": {"median_pe_ttm": context.median_pe, "median_pe_percentile": context.median_pe_percentile},
            "strategies": [
                _strategy_summary(item, results, rule_history.get(item.strategy_id)) for item in active
            ]
            + [
                {**item.describe(), "computed": False, "skip_reason": "需要逐只估值历史：范围须 ≤300 只且不是全市场"}
                for item in skipped
            ],
            "excluded": _excluded_summary(excluded, features),
            "candidate_count": len(candidates),
            "candidates": candidates,
            "selected_for_base_pack": [
                item["symbol"]
                for item in candidates
                if not item["one_off_suspected"]
            ][: max(base_pack_count, 0)],
            "gaps": gaps,
            "provider_errors": errors,
        }
        digest_source = {
            key: payload[key]
            for key in ("version", "strategy_version", "as_of_date", "scope", "candidates", "selected_for_base_pack")
        }
        digest_source["features"] = [f.to_dict() for f in features.values()]
        digest = hashlib.sha256(
            json.dumps(digest_source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        payload["run_id"] = f"discovery-funnel:{as_of_date}:{digest}"
        payload["created_at"] = self.clock().isoformat()
        payload["rule_history_basis"] = RETURN_BASIS if rule_history else None
        payload["members"] = {
            symbol: {
                "features": f.to_dict(),
                "excluded": excluded.get(symbol),
                "results": {k: v.to_dict() for k, v in results.get(symbol, {}).items()},
            }
            for symbol, f in features.items()
        }
        self.repository.save(payload)
        return payload

    def _check_recurring(self, candidates: list[dict[str, Any]], as_of_date: str, limit: int) -> int:
        """Replace the one-off heuristic with 扣非 profit for the leading candidates; re-rank in place."""

        if self.recurring_lookup is None or limit <= 0 or not candidates:
            return 0
        leading = [c for c in candidates if not c["one_off_suspected"]][:limit]
        suspects = [c for c in candidates if c["one_off_suspected"]][:limit]
        checked = 0
        for candidate in [*leading, *suspects]:
            try:
                summary = self.recurring_lookup(candidate["symbol"], as_of_date)
            except Exception as exc:  # noqa: BLE001 - one lookup failure keeps the heuristic
                candidate["data_gaps"].append(f"扣非利润取数失败：{type(exc).__name__}")
                continue
            if summary is None:
                candidate["data_gaps"].append("扣非利润不可用，仍按启发式判断一次性损益")
                continue
            checked += 1
            candidate["recurring"] = summary
            share = summary["non_recurring_share"]
            recurring = summary["recurring_earnings"]
            one_off = recurring <= 0 < summary["earnings"] or (share is not None and share > 0.5)
            heuristic = [n for n in candidate["counter_evidence"] if "非经常性损益" in n or "低基数" in n]
            candidate["counter_evidence"] = [n for n in candidate["counter_evidence"] if n not in heuristic]
            if one_off:
                candidate["counter_evidence"].insert(
                    0,
                    f"扣非归母净利润（TTM，{summary['report_period']}）{recurring / 1e8:.2f} 亿，"
                    f"归母净利润 {summary['earnings'] / 1e8:.2f} 亿：利润主要来自非经常性损益",
                )
            elif share is not None and share > 0.3:
                candidate["counter_evidence"].append(f"非经常性损益占归母净利润 {share:.0%}（TTM）")
            candidate["one_off_suspected"] = one_off
        candidates.sort(key=lambda c: (c["one_off_suspected"], -c["priority"], c["symbol"]))
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        return checked

    def _scope_members(
        self, scope: DiscoveryScope, allow_remote: bool
    ) -> tuple[str, dict[str, str], list[str]]:
        if scope.kind == "industry":
            industry = self.sector_data.industry(scope.code, allow_remote=allow_remote)  # type: ignore[arg-type]
            if industry is None:
                raise KeyError(f"Unknown Shenwan industry: {scope.code}")
            members = self.sector_data.members(industry.code, allow_remote=allow_remote)
            label = scope.label or f"申万{industry.name}（{industry.code}，{industry.level} 级）"
            return label, {m.symbol: m.name for m in members}, ["范围为申万当前成分"]
        if scope.kind == "index":
            if self.index_members is None:
                raise ValueError("Index scopes need an index member source")
            members = self.index_members(scope.code)  # type: ignore[arg-type]
            label = scope.label or f"指数 {scope.code} 当前成分"
            return label, dict(members), ["范围为指数当前成分，不是历史成分"]
        if scope.kind == "symbols":
            label = scope.label or f"指定标的 {len(scope.symbols)} 只"
            return label, {symbol: "" for symbol in scope.symbols}, [scope.origin or "范围为 Agent 指定的标的清单"]
        snapshot = self.cross_section.market_snapshot(allow_remote=allow_remote)
        names = {row["symbol"]: row.get("name", "") for row in snapshot["rows"]}
        return scope.label or "全部 A 股（当前上市）", names, ["范围为当前上市 A 股，不含已退市公司"]

    def _financials(
        self,
        as_of_date: str,
        with_history: bool,
        allow_remote: bool,
        errors: dict[str, Any],
    ) -> Financials:
        latest = _latest_quarter_end(as_of_date)
        periods = [shift_quarter(latest, -offset) for offset in range(_CURRENT_PERIODS)]
        if with_history:
            period = periods[-1]
            while period > _HISTORY_FIRST_PERIOD:
                period = shift_quarter(period, -1)
                periods.append(period)
        rows: dict[str, list[dict[str, Any]]] = {}
        failed = {}
        for period in periods:
            result = self.cross_section.report_period(period, allow_remote=allow_remote)
            rows[period] = result["rows"]
            if result["provider_error"]:
                failed[period] = result["provider_error"]
        if failed:
            errors["financials"] = failed
        return Financials(rows)


def _latest_quarter_end(as_of_date: str) -> str:
    day = date.fromisoformat(as_of_date)
    period = shift_quarter(f"{day.year}-12-31", 0)
    while period >= as_of_date:
        period = shift_quarter(period, -1)
    return period


def _rank_candidates(
    features: dict[str, MemberFeatures],
    results: dict[str, dict[str, StrategyResult]],
    strategies: tuple[Strategy, ...],
) -> list[dict[str, Any]]:
    scores: dict[str, dict[str, float]] = {}
    for item in strategies:
        keyed = sorted(
            (r[item.strategy_id].sort_key, symbol)
            for symbol, r in results.items()
            if r[item.strategy_id].passed
        )
        for position, (_, symbol) in enumerate(keyed):
            scores.setdefault(symbol, {})[item.strategy_id] = (position + 1) / len(keyed)
    ranked = sorted(
        scores,
        key=lambda s: (
            one_off_suspicion(features[s]) is not None,
            -(len(scores[s]) + mean(scores[s].values())),
            s,
        ),
    )
    candidates = []
    newest = max((f.report_period for f in features.values() if f.report_period), default=None)
    for rank, symbol in enumerate(ranked, start=1):
        passed = {sid: results[symbol][sid] for sid in scores[symbol]}
        failed = {
            sid: [text for _, text in result.failed] + [f"缺少：{m}" for m in result.missing]
            for sid, result in results[symbol].items()
            if not result.passed
        }
        suspicion = one_off_suspicion(features[symbol])
        counters = list(
            dict.fromkeys(
                [*([suspicion] if suspicion else []), *(n for r in passed.values() for n in r.counter_evidence)]
            )
        )
        f = features[symbol]
        candidates.append(
            {
                "rank": rank,
                "symbol": symbol,
                "name": f.name,
                "industry_l1": f.industry_l1,
                "strategies": list(passed),
                "priority": round(len(scores[symbol]) + mean(scores[symbol].values()), 4),
                "one_off_suspected": suspicion is not None,
                "strategy_scores": {k: round(v, 4) for k, v in scores[symbol].items()},
                "reasons": {sid: list(r.reasons) for sid, r in passed.items()},
                "counter_evidence": counters,
                "not_matched": failed,
                "data_gaps": _data_gaps(f, newest),
                "features": f.to_dict(),
            }
        )
    return candidates


def _disclosure_note(features: list[MemberFeatures]) -> str | None:
    periods = Counter(f.report_period for f in features if f.report_period)
    if len(periods) < 2:
        return None
    newest = max(periods)
    return (
        f"财报季进行中：{periods[newest]}/{sum(periods.values())} 家已披露 {newest}，其余仍用上一期；"
        "跨期比较会影响排序，披露结束后应重跑"
    )


def _data_gaps(features: MemberFeatures, newest_period: str | None = None) -> list[str]:
    gaps = []
    if newest_period and features.report_period and features.report_period < newest_period:
        gaps.append(
            f"最新财报为 {features.report_period}，范围内已有公司披露 {newest_period}：与已更新的公司跨期比较"
        )
    if features.valuation_date is None:
        gaps.append("截止日附近没有估值数据（可能停牌）")
    if features.pe_percentile is None and features.pe_samples:
        gaps.append(f"PE 自身分位样本不足或 PE 为负（{features.pe_samples} 个月末样本）")
    if features.roe_basis == "annualized_ytd":
        gaps.append("ROE 缺上一年报或去年同期，改用累计值按月份年化（季节性公司会失真）")
    if features.cash_flow_basis == "ytd":
        gaps.append("经营现金流缺 TTM，改用报告期累计值")
    if features.roe_prev_year is None:
        gaps.append("缺一年前的 ROE")
    if features.net_profit_quarter_yoy is None:
        gaps.append("单季净利润同比不可用（去年同季亏损或缺数据）")
    if features.industry_l1 is None:
        gaps.append("不在申万当前成分中（新上市或分类缺失）")
    return gaps


def _strategy_summary(
    item: Strategy,
    results: dict[str, dict[str, StrategyResult]],
    rule_history: dict[str, Any] | None,
) -> dict[str, Any]:
    outcomes = [r[item.strategy_id] for r in results.values()]
    fails = Counter(label for r in outcomes for label, _ in r.failed[:1])
    missing = Counter(label for r in outcomes if not r.failed for label in r.missing[:1])
    return {
        **item.describe(),
        "computed": True,
        "evaluated": len(outcomes),
        "passed": sum(1 for r in outcomes if r.passed),
        "main_elimination_reasons": [
            {"condition": label, "count": count} for label, count in fails.most_common()
        ],
        "missing_data": [{"condition": label, "count": count} for label, count in missing.most_common()],
        "rule_history": rule_history,
    }


def _excluded_summary(excluded: dict[str, str], features: dict[str, MemberFeatures]) -> dict[str, Any]:
    by_reason: dict[str, list[str]] = {}
    for symbol, reason in sorted(excluded.items()):
        by_reason.setdefault(reason, []).append(symbol)
    return {
        reason: {
            "count": len(symbols),
            "examples": [f"{s} {features[s].name}" for s in symbols[:5]],
        }
        for reason, symbols in by_reason.items()
    }
