"""Derived historical summaries from official open-fund facts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.factor_engineering import FactorValue

from .models import OpenFundPeriodicSnapshot, OpenFundProfileSnapshot


OPEN_FUND_HISTORY_SUMMARY_VERSION = "open_fund_history_summary_v1"


class OpenFundHistoricalSummaryCalculator:
    """Derive concentration, flow, scale, and fee summaries."""

    def calculate(
        self,
        *,
        asset_id: str,
        as_of_date: str,
        share_class_code: str,
        profile_snapshot: OpenFundProfileSnapshot | None,
        periodic_snapshot: OpenFundPeriodicSnapshot | None,
        scale_records: tuple[dict[str, Any], ...] = (),
    ) -> tuple[FactorValue, ...]:
        values: list[FactorValue] = []
        computed_at = datetime.now(timezone.utc).isoformat()
        if periodic_snapshot is not None:
            values.extend(
                self._periodic_values(
                    asset_id,
                    share_class_code,
                    periodic_snapshot,
                    computed_at,
                )
            )
        if profile_snapshot is not None:
            fees = [
                value
                for value in (
                    profile_snapshot.management_fee_rate,
                    profile_snapshot.custody_fee_rate,
                    profile_snapshot.sales_service_fee_rate,
                )
                if value is not None
            ]
            if fees:
                values.append(
                    FactorValue(
                        asset_id=asset_id,
                        factor_name="open_fund_ongoing_fee_rate",
                        as_of_date=profile_snapshot.effective_date,
                        value=float(sum(fees, Decimal("0"))),
                        calculation_version=(
                            OPEN_FUND_HISTORY_SUMMARY_VERSION
                        ),
                        parameters={
                            "components": {
                                "management_fee_rate": _string_or_none(
                                    profile_snapshot.management_fee_rate
                                ),
                                "custody_fee_rate": _string_or_none(
                                    profile_snapshot.custody_fee_rate
                                ),
                                "sales_service_fee_rate": (
                                    _string_or_none(
                                        profile_snapshot.sales_service_fee_rate
                                    )
                                ),
                            },
                            "treatment": (
                                "static_disclosed_rate_not_realized_drag"
                            ),
                            "source_document_id": (
                                profile_snapshot.source_document_id
                            ),
                            "source": profile_snapshot.source,
                        },
                        input_start_date=profile_snapshot.effective_date,
                        input_end_date=profile_snapshot.effective_date,
                        source_dataset="open_fund_profile_snapshot",
                        computed_at=computed_at,
                    )
                )
        values.extend(
            _scale_change_values(
                asset_id=asset_id,
                as_of_date=as_of_date,
                records=scale_records,
                computed_at=computed_at,
            )
        )
        return tuple(values)

    def _periodic_values(
        self,
        asset_id: str,
        share_class_code: str,
        snapshot: OpenFundPeriodicSnapshot,
        computed_at: str,
    ) -> list[FactorValue]:
        common = {
            "asset_id": asset_id,
            "as_of_date": snapshot.report_period_end,
            "calculation_version": OPEN_FUND_HISTORY_SUMMARY_VERSION,
            "input_start_date": snapshot.report_period_start,
            "input_end_date": snapshot.report_period_end,
            "source_dataset": "open_fund_periodic_snapshot",
            "computed_at": computed_at,
        }
        source_parameters = {
            "source_document_id": snapshot.source_document_id,
            "source": snapshot.source,
            "report_period_start": snapshot.report_period_start,
            "report_period_end": snapshot.report_period_end,
        }
        values = []
        share_flow = next(
            (
                item
                for item in snapshot.share_flows
                if item.share_class_code == share_class_code
            ),
            snapshot.share_flows[0] if snapshot.share_flows else None,
        )
        if share_flow is not None:
            net_flow = share_flow.subscriptions - share_flow.redemptions
            parameters = {
                **source_parameters,
                "share_class_name": share_flow.share_class_name,
                "share_class_code": share_flow.share_class_code,
                "subscriptions": str(share_flow.subscriptions),
                "redemptions": str(share_flow.redemptions),
                "page_number": share_flow.page_number,
            }
            values.append(
                FactorValue(
                    factor_name="open_fund_net_share_flow",
                    value=float(net_flow),
                    parameters=parameters,
                    **common,
                )
            )
            if share_flow.opening_shares > 0:
                values.append(
                    FactorValue(
                        factor_name="open_fund_net_share_flow_rate",
                        value=float(
                            net_flow / share_flow.opening_shares
                        ),
                        parameters={
                            **parameters,
                            "opening_shares": str(
                                share_flow.opening_shares
                            ),
                        },
                        **common,
                    )
                )
        if snapshot.top_holdings:
            values.append(
                FactorValue(
                    factor_name="open_fund_top10_concentration",
                    value=float(
                        sum(
                            (
                                item.net_asset_ratio
                                for item in snapshot.top_holdings
                            ),
                            Decimal("0"),
                        )
                    ),
                    parameters={
                        **source_parameters,
                        "holding_count": len(snapshot.top_holdings),
                        "page_numbers": sorted(
                            {
                                item.page_number
                                for item in snapshot.top_holdings
                            }
                        ),
                    },
                    **common,
                )
            )
        hong_kong = sum(
            (
                item.net_asset_ratio
                for item in snapshot.industry_allocations
                if item.market_scope == "hong_kong"
            ),
            Decimal("0"),
        )
        values.append(
            FactorValue(
                factor_name="open_fund_hong_kong_exposure",
                value=float(hong_kong),
                parameters={
                    **source_parameters,
                    "market_scope": "hong_kong",
                    "basis": "net_asset_ratio",
                },
                **common,
            )
        )
        by_industry: dict[str, Decimal] = defaultdict(
            lambda: Decimal("0")
        )
        for item in snapshot.industry_allocations:
            by_industry[item.category] += item.net_asset_ratio
        if by_industry:
            top_three = sorted(
                by_industry.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            values.append(
                FactorValue(
                    factor_name=(
                        "open_fund_top3_industry_concentration"
                    ),
                    value=float(
                        sum(
                            (ratio for _, ratio in top_three),
                            Decimal("0"),
                        )
                    ),
                    parameters={
                        **source_parameters,
                        "industries": [
                            {
                                "name": name,
                                "net_asset_ratio": str(ratio),
                            }
                            for name, ratio in top_three
                        ],
                        "basis": "net_asset_ratio",
                    },
                    **common,
                )
            )
        return values


def _scale_change_values(
    *,
    asset_id: str,
    as_of_date: str,
    records: tuple[dict[str, Any], ...],
    computed_at: str,
) -> list[FactorValue]:
    dated = []
    for record in records:
        value_date = _record_date(record)
        value = _first_number(record, "item_value", "scale")
        if value_date and value is not None and value_date <= as_of_date:
            dated.append((value_date, value, record))
    if len(dated) < 2:
        return []
    dated.sort(key=lambda item: (item[0], str(item[2].get("source") or "")))
    first = dated[0]
    latest = dated[-1]
    if first[0] == latest[0]:
        return []
    parameters = {
        "start_value": first[1],
        "end_value": latest[1],
        "item_name": latest[2].get("item_name"),
        "unit": "upstream_record_unit",
        "sources": sorted(
            {
                str(item[2].get("source"))
                for item in (first, latest)
                if item[2].get("source")
            }
        ),
    }
    common = {
        "asset_id": asset_id,
        "as_of_date": latest[0],
        "calculation_version": OPEN_FUND_HISTORY_SUMMARY_VERSION,
        "parameters": parameters,
        "input_start_date": first[0],
        "input_end_date": latest[0],
        "source_dataset": "fund_scale",
        "computed_at": computed_at,
    }
    values = [
        FactorValue(
            factor_name="open_fund_scale_change",
            value=latest[1] - first[1],
            **common,
        )
    ]
    if first[1] != 0:
        values.append(
            FactorValue(
                factor_name="open_fund_scale_change_rate",
                value=latest[1] / first[1] - 1.0,
                **common,
            )
        )
    return values


def _record_date(record: dict[str, Any]) -> str:
    for key in (
        "report_date",
        "as_of_date",
        "value_date",
        "date",
        "update_date",
    ):
        value = str(record.get(key) or "")[:10]
        if value:
            return value
    return ""


def _first_number(
    record: dict[str, Any],
    *names: str,
) -> float | None:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return float(value)
    return None


def _string_or_none(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
