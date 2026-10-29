"""Record an investment thesis as settleable metric views, and check it against later reports.

Design notes: docs/modules/5-研究产物/观点账本模块.md, “投资论点”.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.fundamental_analysis import (
    COMPUTED_METRICS,
    INDICATOR_PREFIX,
    is_period_end_indicator,
    SNAPSHOT_FIELDS,
    format_metric_value,
    metric_label,
)
from src.master_data import ASSET_TYPE_STOCK, MasterDataService
from src.research_contracts import (
    ClaimComparison,
    InvestmentThesis,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    ThesisIndicator,
    View,
    ViewClaim,
    ViewOrigin,
    settlement_checkpoints,
)
from src.research_ledger import (
    LedgerEntryType,
    LedgerScope,
    MetricPoint,
    PriceDataUnavailable,
    ResearchLedgerService,
    fin_metric_id,
)

RECONSTRUCTED_PREFIX = "（事后重建）"
# The view-date reference must be found for TTM metrics: one year plus the release lag.
_REFERENCE_LOOKBACK_DAYS = 400
ON_TRACK, DEVIATING, INVALIDATED, AWAITING = "on_track", "deviating", "invalidated", "awaiting_data"
STATUS_LABELS = {ON_TRACK: "符合", DEVIATING: "偏离", INVALIDATED: "失效", AWAITING: "待数据"}


def thesis_source_ref(thesis_id: str) -> str:
    return f"thesis:{thesis_id}"


def thesis_views(thesis: InvestmentThesis) -> tuple[View, ...]:
    """One settleable view per expected bound, settled on the indicator's target report period."""

    prefix = RECONSTRUCTED_PREFIX if thesis.reconstructed else ""
    views = []
    for item in thesis.indicators:
        rule = SettlementRule(SettlementMethod.METRIC_OBSERVATION, fin_metric_id(thesis.asset_id, item.metric))
        for suffix, bound, comparison, word in (
            ("low", item.expected_low, ClaimComparison.ABOVE, "不低于"),
            ("high", item.expected_high, ClaimComparison.BELOW, "不高于"),
        ):
            if bound is None:
                continue
            falsifier = (
                _invalidation_text(item)
                if item.invalidation_comparison is not None
                else f"{item.label}{'低于' if suffix == 'low' else '高于'} {format_metric_value(item.metric, bound)}"
            )
            views.append(
                View(
                    view_id=f"{thesis.thesis_id}:{item.indicator_id}:{suffix}",
                    subject_type=SubjectType.ASSET,
                    subject_id=thesis.asset_id,
                    origin=thesis.origin,
                    proposition=(
                        f"{prefix}{item.label}：{item.observe_period} 期{word} "
                        f"{format_metric_value(item.metric, bound)}"
                    ),
                    horizon=thesis.horizon,
                    as_of_date=thesis.as_of_date,
                    expires_on=thesis.expires_on,
                    confidence=thesis.confidence,
                    user_id=thesis.user_id,
                    claim=ViewClaim(f"level_at:{item.observe_period}", comparison, bound),
                    support_refs=thesis.source_refs,
                    falsifiers=(falsifier,),
                    implications=(thesis_source_ref(thesis.thesis_id),),
                    settlement_rule=rule,
                )
            )
    return tuple(views)


class ThesisService:
    def __init__(self, *, ledger: ResearchLedgerService, metrics: Any, master_data: MasterDataService) -> None:
        self.ledger = ledger
        self.metrics = metrics
        self.master_data = master_data

    def record(self, thesis: InvestmentThesis) -> dict[str, Any]:
        asset = self.master_data.get_asset(thesis.asset_id)
        if asset is None or asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(f"论点只支持股票；未知或非股票标的 {thesis.asset_id}")
        for item in thesis.indicators:
            if metric_label(item.metric) is None:
                raise ValueError(
                    f"{item.indicator_id}: 不支持的指标 {item.metric}；可用 "
                    + "、".join([*COMPUTED_METRICS, *SNAPSHOT_FIELDS])
                    + " 或 indicator.<披露字段>"
                )
        warnings, latest = [], {}
        for item in thesis.indicators:
            try:
                points = self._points(thesis.asset_id, item.metric, thesis.as_of_date, thesis.as_of_date)
            except PriceDataUnavailable as exc:
                warnings.append(f"{item.indicator_id}: 截止日前的数据取不到（{exc}），结算时再取")
                continue
            if any(point.period_end == item.observe_period for point in points):
                raise ValueError(
                    f"{item.indicator_id}: {item.observe_period} 期在 {thesis.as_of_date} 前已披露，论点只能跟踪未来报告期"
                )
            if points:
                point = points[-1]
                latest[item.indicator_id] = _point(item.metric, point)
            else:
                warnings.append(f"{item.indicator_id}: 截止日前没有已披露的值，无法确定参考期")
        views = thesis_views(thesis)
        user_id = thesis.user_id if thesis.origin is ViewOrigin.USER else None
        entries = self.ledger.record(
            (thesis, *views),
            scope=LedgerScope.PERSONAL if user_id is not None else LedgerScope.OBJECTIVE,
            user_id=user_id,
            source_ref=thesis_source_ref(thesis.thesis_id),
        )
        return {
            "thesis_entry_id": entries[0].entry_id,
            "view_entries": [
                {"entry_id": entry.entry_id, "proposition": view.proposition}
                for entry, view in zip(entries[1:], views)
            ],
            "checkpoints": list(settlement_checkpoints(thesis.horizon, thesis.as_of_date, thesis.expires_on)),
            "latest_known": latest,
            "warnings": warnings,
        }

    def check(
        self,
        asset_id: str,
        *,
        as_of_date: str,
        user_id: str | None = None,
        thesis_entry_id: str | None = None,
    ) -> dict[str, Any]:
        """Each indicator: on track, deviating, invalidated or awaiting data, from reports published by as_of_date."""

        if thesis_entry_id is not None:
            entries = (self.ledger.get_entry(thesis_entry_id, user_id=user_id),)
            if entries[0].entry_type is not LedgerEntryType.THESIS:
                raise ValueError(f"{thesis_entry_id} 不是论点条目")
        else:
            entries = self.ledger.list_entries(
                user_id=user_id,
                entry_type=LedgerEntryType.THESIS,
                subject_id=asset_id,
                active_on=as_of_date,
            )
        theses = [self._check_one(entry, as_of_date) for entry in entries]
        return {
            "asset_id": asset_id,
            "as_of_date": as_of_date,
            "theses": theses,
            "note": (
                None
                if theses
                else "账本中没有该标的在此日期有效的论点；用 record-thesis 记录（系统外买入的持仓按用户口述重建并标注 reconstructed）"
            ),
        }

    def _check_one(self, entry: Any, as_of_date: str) -> dict[str, Any]:
        thesis: InvestmentThesis = entry.artifact
        indicators = [self._indicator_status(thesis, item, as_of_date) for item in thesis.indicators]
        statuses = {item["status"] for item in indicators}
        if INVALIDATED in statuses:
            overall = INVALIDATED
        elif DEVIATING in statuses:
            overall = DEVIATING
        elif statuses == {AWAITING}:
            overall = AWAITING
        else:
            overall = ON_TRACK
        views = self.ledger.list_entries(
            user_id=entry.user_id,
            entry_type=LedgerEntryType.VIEW,
            source_ref=thesis_source_ref(thesis.thesis_id),
        )
        return {
            "thesis_entry_id": entry.entry_id,
            "thesis_id": thesis.thesis_id,
            "summary": thesis.summary,
            "as_of_date": thesis.as_of_date,
            "expires_on": thesis.expires_on,
            "reconstructed": thesis.reconstructed,
            "original_date": thesis.original_date,
            "status": overall,
            "status_label": STATUS_LABELS[overall],
            "indicators": indicators,
            "view_entry_ids": [item.entry_id for item in views],
            "notes": [
                "中期状态用目标期之前新披露的报告期判断（TTM 或披露口径），不等于目标期结果",
                *(
                    [f"事后重建的论点：按用户口述于 {thesis.as_of_date} 记录，原买入日 {thesis.original_date or '未说明'}，结算只从记录日起算"]
                    if thesis.reconstructed
                    else []
                ),
            ],
        }

    def _indicator_status(self, thesis: InvestmentThesis, item: ThesisIndicator, as_of_date: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "indicator_id": item.indicator_id,
            "metric": item.metric,
            "label": item.label,
            "observe_period": item.observe_period,
            "expected": _expected_text(item),
            "invalidation": _invalidation_text(item) if item.invalidation_comparison is not None else None,
            "reference": None,
            "new_periods": [],
            "basis": None,
        }
        try:
            points = self._points(thesis.asset_id, item.metric, thesis.as_of_date, as_of_date)
        except PriceDataUnavailable as exc:
            return {**result, "status": AWAITING, "status_label": STATUS_LABELS[AWAITING], "note": f"数据取不到：{exc}"}
        known = [p for p in points if p.available_at <= thesis.as_of_date]
        reference = known[-1] if known else None
        new = [
            p
            for p in points
            if p.available_at > thesis.as_of_date and (reference is None or p.period_end > reference.period_end)
        ]
        result["reference"] = None if reference is None else _point(item.metric, reference)
        result["new_periods"] = [_point(item.metric, p) for p in new]
        cumulative = item.metric.startswith(INDICATOR_PREFIX) and not is_period_end_indicator(item.metric)
        if cumulative:
            # Year-to-date disclosures (ROE, margins) are only comparable with the same quarter.
            new = [p for p in new if p.period_end[5:] == item.observe_period[5:]]
        breach = next((p for p in new if item.invalidated_by(p.value)), None)
        target = next((p for p in new if p.period_end == item.observe_period), None)
        if breach is not None:
            status, basis = INVALIDATED, breach.period_end
            note = f"{breach.period_end} 期 {format_metric_value(item.metric, breach.value)} 触及失效条件"
        elif target is not None:
            status = ON_TRACK if item.within_expected(target.value) else DEVIATING
            basis, note = "target_period", f"目标期 {target.period_end} 为 {format_metric_value(item.metric, target.value)}"
        elif new:
            status = ON_TRACK if item.within_expected(new[-1].value) else DEVIATING
            basis = "interim"
            note = f"目标期未披露；最新 {new[-1].period_end} 期为 {format_metric_value(item.metric, new[-1].value)}"
        else:
            status, basis = AWAITING, None
            note = (
                "论点记录后还没有新披露的同季度报告期（披露指标按年初累计口径，只与同季度比较）"
                if cumulative
                else "论点记录后还没有新披露的报告期"
            )
        return {**result, "status": status, "status_label": STATUS_LABELS[status], "basis": basis, "note": note}

    def _points(self, asset_id: str, metric: str, start: str, end: str) -> list[MetricPoint]:
        lookback = (date.fromisoformat(start) - timedelta(days=_REFERENCE_LOOKBACK_DAYS)).isoformat()
        series = self.metrics.series(fin_metric_id(asset_id, metric), lookback, end)
        return sorted((p for p in series if p.available_at <= end), key=lambda p: p.period_end)


def _point(metric: str, point: MetricPoint) -> dict[str, Any]:
    return {
        "period_end": point.period_end,
        "published": point.available_at,
        "value": str(point.value),
        "display": format_metric_value(metric, point.value),
    }


def _expected_text(item: ThesisIndicator) -> str:
    parts = []
    if item.expected_low is not None:
        parts.append(f"≥ {format_metric_value(item.metric, item.expected_low)}")
    if item.expected_high is not None:
        parts.append(f"≤ {format_metric_value(item.metric, item.expected_high)}")
    return f"{item.observe_period} 期 " + " 且 ".join(parts)


def _invalidation_text(item: ThesisIndicator) -> str:
    word = "低于" if item.invalidation_comparison is ClaimComparison.BELOW else "高于"
    threshold: Decimal = item.invalidation_threshold
    return f"{item.label}{word} {format_metric_value(item.metric, threshold)} 即论点失效"
