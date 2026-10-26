"""Prepare comparable ordinary-company metrics on one report period."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.fundamental_analysis import FundamentalAnalysisService

from .models import (
    IndustryAssignment,
    PeerMetricExecutionResult,
    PeerSet,
)
from .service import PeerAnalysisService


class PeerMetricExecutionService:
    def __init__(
        self,
        *,
        data_access: DataAccessService,
        fundamental_service: FundamentalAnalysisService,
        peer_service: PeerAnalysisService,
    ) -> None:
        self.data_access = data_access
        self.fundamental_service = fundamental_service
        self.peer_service = peer_service

    def execute(
        self,
        *,
        peer_set: PeerSet,
        industry: IndustryAssignment,
        as_of_date: str,
        metric_names: tuple[str, ...],
        allow_remote: bool = True,
        bar_remote_sources: tuple[str, ...] = (
            "mootdx",
            "tencent",
            "sina",
        ),
        provider_timeout_seconds: float = 90.0,
    ) -> PeerMetricExecutionResult:
        if industry.asset_id != peer_set.target_asset_id:
            raise ValueError(
                "Industry assignment must match peer target"
            )
        asset_ids = (
            peer_set.target_asset_id,
            *tuple(
                member.asset_id
                for member in peer_set.members
                if member.included
            ),
        )
        end = date.fromisoformat(as_of_date)
        financial_start = (
            end - timedelta(days=1095)
        ).isoformat()
        bar_start = (end - timedelta(days=730)).isoformat()
        bundles = {}
        prices = {}
        period_sets = []
        for asset_id in asset_ids:
            bundle = self.data_access.get_financial_bundle(
                asset_id=asset_id,
                start_date=financial_start,
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                provider_timeout_seconds=provider_timeout_seconds,
            )
            bundles[asset_id] = bundle
            period_sets.append(
                set(bundle.period_summary().available_periods)
            )
            bars = self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=asset_id),
                    dataset=DATASET_STOCK_DAILY_BARS,
                    start_date=bar_start,
                    end_date=as_of_date,
                    as_of_date=as_of_date,
                    allow_remote=allow_remote,
                    remote_sources=bar_remote_sources,
                    provider_timeout_seconds=provider_timeout_seconds,
                )
            )
            eligible = [
                record
                for record in bars.records
                if str(record.get("trade_date")) <= as_of_date
            ]
            if not eligible:
                raise ValueError(
                    f"No market price for peer asset: {asset_id}"
                )
            prices[asset_id] = Decimal(
                str(eligible[-1]["close"])
            )
        common = set.intersection(*period_sets)
        if not common:
            raise ValueError(
                "Peer assets have no common report period"
            )
        report_period = max(common)
        for asset_id in asset_ids:
            self.fundamental_service.analyze(
                bundles[asset_id],
                market_price=prices[asset_id],
                target_report_period=report_period,
            )
        self.peer_service.assign_industry(industry)
        saved_set = self.peer_service.save_peer_set(peer_set)
        comparison = self.peer_service.compare(
            peer_set_id=saved_set.peer_set_id,
            as_of_date=as_of_date,
            metric_names=metric_names,
        )
        return PeerMetricExecutionResult(
            peer_set=saved_set,
            common_report_period=report_period,
            prepared_asset_ids=tuple(asset_ids),
            comparison=comparison,
        )
