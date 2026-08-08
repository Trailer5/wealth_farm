"""Point-in-time tracking of confirmed holdings and anonymous weekly ETF flows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .models import (
    ETFDailyObservation,
    ConfirmedHoldingChange,
    StateHolderEvidence,
    StatePurchaseAnnouncement,
    WeeklyStateFlowMetric,
)
from .registry import DEFAULT_STATE_ENTITY_REGISTRY, StateEntityRegistry


class StateCapitalFlowTracker:
    """Keep identity evidence separate from anonymous share-flow proxies."""

    def __init__(
        self,
        registry: StateEntityRegistry = DEFAULT_STATE_ENTITY_REGISTRY,
    ) -> None:
        self.registry = registry

    def confirmed_holding_changes(
        self,
        evidence: list[StateHolderEvidence],
        as_of_date: str,
        core_only: bool = True,
        all_intervals: bool = False,
    ) -> list[ConfirmedHoldingChange]:
        as_of = date.fromisoformat(as_of_date)
        grouped: dict[
            tuple[str, str],
            list[StateHolderEvidence],
        ] = defaultdict(list)
        for item in evidence:
            if (
                item.source_tier != "official"
                or date.fromisoformat(item.published_at) > as_of
                or not self._entity_allowed(item.entity_id, core_only)
            ):
                continue
            grouped[(item.entity_id, item.instrument_asset_id)].append(item)

        changes: list[ConfirmedHoldingChange] = []
        for (entity_id, instrument_asset_id), items in sorted(grouped.items()):
            snapshots: dict[
                tuple[str, str],
                StateHolderEvidence,
            ] = {}
            for item in items:
                key = (item.holding_date, item.report_period)
                previous = snapshots.get(key)
                if previous is None or (
                    item.published_at,
                    item.evidence_id,
                ) > (
                    previous.published_at,
                    previous.evidence_id,
                ):
                    snapshots[key] = item
            ordered = sorted(
                snapshots.values(),
                key=lambda item: (
                    item.holding_date,
                    item.published_at,
                    item.report_period,
                    item.evidence_id,
                ),
            )
            if len(ordered) < 2:
                continue
            pairs = (
                list(zip(ordered, ordered[1:]))
                if all_intervals
                else [(ordered[-2], ordered[-1])]
            )
            for previous, current in pairs:
                if current.holding_date == previous.holding_date:
                    continue
                changes.append(
                    ConfirmedHoldingChange(
                        entity_id=entity_id,
                        instrument_asset_id=instrument_asset_id,
                        previous_holding_shares=previous.holding_shares,
                        current_holding_shares=current.holding_shares,
                        previous_report_period=previous.report_period,
                        current_report_period=current.report_period,
                        previous_holding_date=previous.holding_date,
                        current_holding_date=current.holding_date,
                        previous_published_at=previous.published_at,
                        current_published_at=current.published_at,
                        delta_shares=(
                            current.holding_shares
                            - previous.holding_shares
                        ),
                        available_at=current.published_at,
                    )
                )
        return changes

    def weekly_metrics(
        self,
        basket_id: str,
        observations: list[ETFDailyObservation],
        holder_evidence: list[StateHolderEvidence],
        announcements: list[StatePurchaseAnnouncement],
        as_of_date: str,
        core_only: bool = True,
    ) -> list[WeeklyStateFlowMetric]:
        if not isinstance(basket_id, str) or not basket_id.strip():
            raise ValueError("basket_id must not be blank")
        as_of = date.fromisoformat(as_of_date)
        week_start_date = as_of - timedelta(days=as_of.weekday())
        week_end_date = week_start_date + timedelta(days=6)

        latest_holders = self._latest_visible_holders(
            holder_evidence,
            as_of,
            core_only,
        )
        holders_by_instrument: dict[str, list[StateHolderEvidence]] = defaultdict(
            list
        )
        for item in latest_holders.values():
            if item.holding_shares > 0:
                holders_by_instrument[item.instrument_asset_id].append(item)
        members = sorted(holders_by_instrument)
        if not members:
            return []

        observations_by_instrument: dict[
            str,
            list[ETFDailyObservation],
        ] = defaultdict(list)
        member_set = set(members)
        for observation in observations:
            if (
                observation.instrument_asset_id in member_set
                and date.fromisoformat(observation.trade_date) <= as_of
                and date.fromisoformat(observation.available_at) <= as_of
            ):
                observations_by_instrument[
                    observation.instrument_asset_id
                ].append(observation)

        valid_pairs: dict[
            str,
            tuple[ETFDailyObservation, ETFDailyObservation],
        ] = {}
        for instrument_asset_id in members:
            pair = _weekly_pair(
                observations_by_instrument.get(instrument_asset_id, []),
                week_start_date,
            )
            if pair is not None:
                valid_pairs[instrument_asset_id] = pair

        holder_available_dates = [
            date.fromisoformat(item.published_at)
            for items in holders_by_instrument.values()
            for item in items
        ]
        current_available_dates = [
            date.fromisoformat(current.available_at)
            for _, current in valid_pairs.values()
        ]
        base_available_at = max(
            [*holder_available_dates, *current_available_dates]
        )
        eligible_count = len(members)
        valid_count = len(valid_pairs)
        coverage_ratio = valid_count / eligible_count
        base_parameters = {
            "members": members,
            "sample_count": valid_count,
            "eligible_member_count": eligible_count,
            "coverage_ratio": coverage_ratio,
            "identity_confirmed_by_holder_report": True,
            "identity_inference": False,
            "not_confirmed_state_flow": True,
        }

        metrics: list[WeeklyStateFlowMetric] = []
        flow_by_instrument: dict[str, Decimal] = {}
        delta_by_instrument: dict[str, Decimal] = {}
        for instrument_asset_id, (previous, current) in valid_pairs.items():
            delta = current.total_shares - previous.total_shares
            delta_by_instrument[instrument_asset_id] = delta
            if current.nav is not None:
                flow_by_instrument[instrument_asset_id] = delta * current.nav

        core_holders_by_instrument: dict[
            str,
            list[StateHolderEvidence],
        ] = defaultdict(list)
        for item in self._latest_visible_holders(
            holder_evidence,
            as_of,
            True,
        ).values():
            core_holders_by_instrument[item.instrument_asset_id].append(item)
        lower_bound_details: dict[str, dict[str, object]] = {}
        confirmed_lower_bounds: list[Decimal] = []
        confirmed_lower_bound_dates: list[date] = []
        for instrument_asset_id, (_, current) in sorted(valid_pairs.items()):
            instrument_holders = core_holders_by_instrument.get(
                instrument_asset_id,
                [],
            )
            if not any(
                item.holding_shares > 0 for item in instrument_holders
            ):
                continue
            disclosed_shares = sum(
                (
                    item.holding_shares
                    for item in instrument_holders
                ),
                Decimal("0"),
            )
            continuity_confirmed = current.share_unit_continuity_confirmed
            minimum_reduction = (
                max(
                    Decimal("0"),
                    disclosed_shares - current.total_shares,
                )
                if continuity_confirmed
                else None
            )
            lower_bound_details[instrument_asset_id] = {
                "disclosed_core_holding_shares": str(disclosed_shares),
                "current_total_shares": str(current.total_shares),
                "minimum_reduction_shares": (
                    None
                    if minimum_reduction is None
                    else str(minimum_reduction)
                ),
                "holder_evidence_ids": sorted(
                    item.evidence_id for item in instrument_holders
                ),
                "continuity_confirmed": continuity_confirmed,
            }
            if continuity_confirmed:
                if minimum_reduction is not None and minimum_reduction > 0:
                    confirmed_lower_bounds.append(minimum_reduction)
                confirmed_lower_bound_dates.extend(
                    date.fromisoformat(item.published_at)
                    for item in instrument_holders
                )
                confirmed_lower_bound_dates.append(
                    date.fromisoformat(current.available_at)
                )

        confirmed_member_count = sum(
            1
            for detail in lower_bound_details.values()
            if detail["continuity_confirmed"]
        )
        if confirmed_member_count:
            lower_bound_parameters = dict(base_parameters)
            lower_bound_parameters.update(
                {
                    "sample_count": confirmed_member_count,
                    "confirmed_as_lower_bound": True,
                    "member_details": lower_bound_details,
                }
            )
            metrics.append(
                self._metric(
                    basket_id,
                    "state_minimum_reduction_since_disclosure_shares",
                    week_start_date,
                    week_end_date,
                    max(confirmed_lower_bound_dates),
                    sum(confirmed_lower_bounds, Decimal("0")),
                    "confirmed_lower_bound",
                    lower_bound_parameters,
                )
            )

        if flow_by_instrument:
            flow_parameters = dict(base_parameters)
            flow_parameters["sample_count"] = len(flow_by_instrument)
            flow_parameters["nav_coverage_ratio"] = (
                len(flow_by_instrument) / eligible_count
            )
            flow_parameters["member_details"] = _member_details(
                valid_pairs,
                delta_by_instrument,
                holders_by_instrument,
            )
            metrics.append(
                self._metric(
                    basket_id,
                    "state_held_etf_weekly_anonymous_flow_cny",
                    week_start_date,
                    week_end_date,
                    base_available_at,
                    sum(flow_by_instrument.values(), Decimal("0")),
                    "anonymous_proxy",
                    flow_parameters,
                )
            )

            weighted_flows: dict[str, Decimal] = {}
            ratios: dict[str, Decimal] = {}
            for instrument_asset_id, flow in flow_by_instrument.items():
                ratio_values = [
                    item.holding_ratio
                    for item in holders_by_instrument[instrument_asset_id]
                    if item.holding_ratio is not None
                ]
                if not ratio_values:
                    continue
                ratio = sum(ratio_values, Decimal("0"))
                if ratio > 1:
                    continue
                ratios[instrument_asset_id] = ratio
                weighted_flows[instrument_asset_id] = flow * ratio
            if weighted_flows:
                weighted_parameters = dict(base_parameters)
                weighted_parameters["sample_count"] = len(weighted_flows)
                weighted_parameters["pro_rata_assumption"] = True
                weighted_parameters["holding_ratios"] = {
                    key: str(value) for key, value in sorted(ratios.items())
                }
                metrics.append(
                    self._metric(
                        basket_id,
                        "state_exposure_weighted_weekly_proxy_cny",
                        week_start_date,
                        week_end_date,
                        base_available_at,
                        sum(weighted_flows.values(), Decimal("0")),
                        "anonymous_proxy",
                        weighted_parameters,
                    )
                )

        if valid_pairs:
            breadth = sum(
                1 for delta in delta_by_instrument.values() if delta > 0
            ) / valid_count
            breadth_parameters = dict(base_parameters)
            breadth_parameters["positive_member_count"] = sum(
                1 for delta in delta_by_instrument.values() if delta > 0
            )
            breadth_parameters["member_details"] = _member_details(
                valid_pairs,
                delta_by_instrument,
                holders_by_instrument,
            )
            metrics.append(
                self._metric(
                    basket_id,
                    "state_held_etf_weekly_positive_breadth",
                    week_start_date,
                    week_end_date,
                    base_available_at,
                    breadth,
                    "anonymous_proxy",
                    breadth_parameters,
                )
            )

        buy_announcements = [
            item
            for item in announcements
            if (
                item.direction == "buy"
                and week_start_date
                <= date.fromisoformat(item.published_at)
                <= min(week_end_date, as_of)
                and self._entity_allowed(item.entity_id, core_only)
            )
        ]
        if buy_announcements:
            flag_parameters = dict(base_parameters)
            flag_parameters["sample_count"] = len(buy_announcements)
            flag_parameters["announcement_ids"] = sorted(
                item.announcement_id for item in buy_announcements
            )
            flag_available_at = max(
                base_available_at,
                *(
                    date.fromisoformat(item.published_at)
                    for item in buy_announcements
                ),
            )
            metrics.append(
                self._metric(
                    basket_id,
                    "state_purchase_confirmed_flag",
                    week_start_date,
                    week_end_date,
                    flag_available_at,
                    1,
                    "confirmed_direction",
                    flag_parameters,
                )
            )
        return metrics

    def _latest_visible_holders(
        self,
        evidence: list[StateHolderEvidence],
        as_of: date,
        core_only: bool,
    ) -> dict[tuple[str, str], StateHolderEvidence]:
        latest: dict[tuple[str, str], StateHolderEvidence] = {}
        for item in evidence:
            if (
                item.source_tier != "official"
                or date.fromisoformat(item.published_at) > as_of
                or not self._entity_allowed(item.entity_id, core_only)
            ):
                continue
            key = (item.entity_id, item.instrument_asset_id)
            previous = latest.get(key)
            if previous is None or (
                item.holding_date,
                item.published_at,
                item.evidence_id,
            ) > (
                previous.holding_date,
                previous.published_at,
                previous.evidence_id,
            ):
                latest[key] = item
        return latest

    def _entity_allowed(self, entity_id: str, core_only: bool) -> bool:
        entity = self.registry.get(entity_id)
        if entity is None:
            return False
        return not core_only or entity.group == "stabilization_core"

    @staticmethod
    def _metric(
        basket_id: str,
        factor_name: str,
        week_start: date,
        week_end: date,
        available_at: date,
        value: Decimal | float | int,
        evidence_tier: str,
        parameters: dict[str, object],
    ) -> WeeklyStateFlowMetric:
        return WeeklyStateFlowMetric(
            basket_id=basket_id,
            factor_name=factor_name,
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            estimated_for_date=week_end.isoformat(),
            available_at=available_at.isoformat(),
            value=float(value),
            evidence_tier=evidence_tier,  # type: ignore[arg-type]
            parameters=parameters,
        )


def _weekly_pair(
    observations: list[ETFDailyObservation],
    current_week_start: date,
) -> tuple[ETFDailyObservation, ETFDailyObservation] | None:
    by_week: dict[date, ETFDailyObservation] = {}
    for observation in observations:
        trade_date = date.fromisoformat(observation.trade_date)
        week_start = trade_date - timedelta(days=trade_date.weekday())
        previous = by_week.get(week_start)
        if previous is None or (
            observation.trade_date,
            observation.available_at,
            observation.source,
        ) > (
            previous.trade_date,
            previous.available_at,
            previous.source,
        ):
            by_week[week_start] = observation
    current = by_week.get(current_week_start)
    if current is None:
        return None
    previous_weeks = [week for week in by_week if week < current_week_start]
    if not previous_weeks:
        return None
    previous = by_week[max(previous_weeks)]
    if (
        date.fromisoformat(current.trade_date)
        - date.fromisoformat(previous.trade_date)
    ).days > 14:
        return None
    return previous, current


def _member_details(
    pairs: dict[
        str,
        tuple[ETFDailyObservation, ETFDailyObservation],
    ],
    deltas: dict[str, Decimal],
    holders: dict[str, list[StateHolderEvidence]],
) -> dict[str, dict[str, object]]:
    details: dict[str, dict[str, object]] = {}
    for instrument_asset_id, (previous, current) in sorted(pairs.items()):
        details[instrument_asset_id] = {
            "previous_trade_date": previous.trade_date,
            "current_trade_date": current.trade_date,
            "delta_total_shares": str(deltas[instrument_asset_id]),
            "current_nav": None if current.nav is None else str(current.nav),
            "current_observation_source": current.source,
            "holder_evidence_ids": sorted(
                item.evidence_id for item in holders[instrument_asset_id]
            ),
        }
    return details
