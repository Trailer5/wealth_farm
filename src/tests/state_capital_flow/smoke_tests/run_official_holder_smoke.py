"""Validate official SSE report discovery, backfill, and weekly ETF proxies."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CORE_SMOKE_SYMBOLS = {
    "510300": "asset:etf:510300",
    "510050": "asset:etf:510050",
}


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.document_processing import PDFTableExtractor
    from src.state_capital_flow import (
        CORE_BROAD_ETF_CANDIDATES,
        ETFDailyObservation,
        OfficialHolderTableParser,
        SQLiteStateCapitalFlowRepository,
        SseFundReportDiscovery,
        StateCapitalFlowTracker,
        StateETFCoverageService,
        StateHolderBackfillService,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-catalog",
        action="store_true",
        help="Backfill every curated broad ETF candidate.",
    )
    parser.add_argument(
        "--calibrate-history",
        action="store_true",
        help="Fetch 2025 weekly shares and calibrate proxy direction.",
    )
    args = parser.parse_args()
    candidates = (
        CORE_BROAD_ETF_CANDIDATES
        if args.full_catalog or args.calibrate_history
        else tuple(
            candidate
            for candidate in CORE_BROAD_ETF_CANDIDATES
            if candidate.symbol in CORE_SMOKE_SYMBOLS
        )
    )
    symbol_to_asset_id = {
        candidate.symbol: candidate.asset_id
        for candidate in candidates
    }
    report_start_date = (
        "2023-01-01" if args.calibrate_history else "2025-01-01"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        repository = SQLiteStateCapitalFlowRepository(
            root / "state-flow.sqlite3"
        )
        repository.initialize()
        backfill = StateHolderBackfillService(
            discovery=SseFundReportDiscovery(),
            parser=OfficialHolderTableParser(),
            table_extractor=PDFTableExtractor(),
            repository=repository,
        )
        backfill_result = backfill.run(
            symbol_to_asset_id=symbol_to_asset_id,
            start_date=report_start_date,
            end_date="2026-04-01",
            root_dir=root / "reports",
            max_pages=max(
                20,
                len(candidates)
                * (5 if args.calibrate_history else 2),
            ),
            max_reports=max(
                10,
                len(candidates)
                * (8 if args.calibrate_history else 3),
            ),
        )
        evidence = repository.list_holder_evidence(
            source_tier="official",
            published_through="2026-08-09",
        )
        if not evidence:
            raise RuntimeError(
                "Official report backfill returned no state holders"
            )

        observations = _weekly_observations(
            AkShareDataSource(),
            symbol_to_asset_id=symbol_to_asset_id,
            previous_date="2026-07-31",
            current_date="2026-08-07",
        )
        coverage = StateETFCoverageService().evaluate(
            candidates,
            evidence,
            observations,
            "2026-08-09",
        )
        calibration = None
        stop_assessment = None
        historical_observation_count = 0
        historical_metric_count = 0
        if args.calibrate_history:
            from src.state_capital_flow import (
                StateProxyCalibrationService,
                assess_tracking_stop,
            )

            historical_observations = _historical_weekly_observations(
                AkShareDataSource(),
                symbol_to_asset_id=symbol_to_asset_id,
                start_date="2023-01-06",
                end_date="2025-12-26",
            )
            historical_observation_count = len(
                historical_observations
            )
            historical_metrics = _historical_weekly_metrics(
                StateCapitalFlowTracker(),
                historical_observations,
                evidence,
            )
            historical_metric_count = len(historical_metrics)
            changes = StateCapitalFlowTracker().confirmed_holding_changes(
                evidence,
                "2026-08-09",
                all_intervals=True,
            )
            calibration = StateProxyCalibrationService().calibrate(
                changes,
                historical_metrics,
                "2026-08-09",
            )
            stop_assessment = assess_tracking_stop(
                coverage,
                calibration,
                min_confirmed_etfs=6,
                min_calibration_cases=4,
                min_direction_accuracy=0.6,
            )
        metrics = StateCapitalFlowTracker().weekly_metrics(
            basket_id="basket:state-held-broad-etf",
            observations=observations,
            holder_evidence=evidence,
            announcements=[],
            as_of_date="2026-08-09",
        )
        repository.upsert_weekly_flow_metrics(metrics)
        stored_metrics = repository.list_weekly_flow_metrics(
            basket_id="basket:state-held-broad-etf",
            available_through="2026-08-09",
        )

    if not stored_metrics:
        raise RuntimeError("No weekly state-held ETF metrics")
    print(
        json.dumps(
            {
                "backfill": {
                    "discovered_count": (
                        backfill_result.discovered_count
                    ),
                    "processed_count": backfill_result.processed_count,
                    "evidence_count": backfill_result.evidence_count,
                    "failed_count": backfill_result.failed_count,
                    "errors": backfill_result.errors,
                },
                "holders": [
                    {
                        "instrument_asset_id": item.instrument_asset_id,
                        "entity_id": item.entity_id,
                        "report_period": item.report_period,
                        "holding_shares": str(item.holding_shares),
                        "holding_ratio": (
                            str(item.holding_ratio)
                            if item.holding_ratio is not None
                            else None
                        ),
                        "published_at": item.published_at,
                        "source_url": item.source_url,
                        "page_number": item.parameters["page_number"],
                    }
                    for item in evidence
                ],
                "coverage": {
                    "candidate_count": coverage.candidate_count,
                    "confirmed_count": coverage.confirmed_count,
                    "weekly_ready_count": coverage.weekly_ready_count,
                    "holder_coverage_ratio": (
                        coverage.holder_coverage_ratio
                    ),
                    "weekly_coverage_ratio": (
                        coverage.weekly_coverage_ratio
                    ),
                    "statuses": {
                        item.candidate.symbol: item.status
                        for item in coverage.statuses
                    },
                },
                "weekly_metrics": {
                    item.factor_name: {
                        "value": item.value,
                        "week_end": item.week_end,
                        "available_at": item.available_at,
                        "evidence_tier": item.evidence_tier,
                        "coverage_ratio": item.parameters[
                            "coverage_ratio"
                        ],
                    }
                    for item in stored_metrics
                },
                "calibration": (
                    None
                    if calibration is None
                    else {
                        "case_count": calibration.case_count,
                        "match_count": calibration.match_count,
                        "mismatch_count": calibration.mismatch_count,
                        "unknown_count": calibration.unknown_count,
                        "direction_accuracy": (
                            calibration.direction_accuracy
                        ),
                        "historical_observation_count": (
                            historical_observation_count
                        ),
                        "historical_metric_count": (
                            historical_metric_count
                        ),
                        "cases": [
                            {
                                "instrument": item.instrument,
                                "interval": item.interval,
                                "confirmed_delta_shares": str(
                                    item.confirmed_delta_shares
                                ),
                                "proxy_delta_shares": str(
                                    item.proxy_delta_shares
                                ),
                                "outcome": item.outcome,
                            }
                            for item in calibration.cases
                        ],
                    }
                ),
                "stop_assessment": (
                    None
                    if stop_assessment is None
                    else {
                        "ready_to_stop": (
                            stop_assessment.ready_to_stop
                        ),
                        "failure_reasons": (
                            stop_assessment.failure_reasons
                        ),
                        "parameters": stop_assessment.parameters,
                    }
                ),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _weekly_observations(
    provider,
    *,
    symbol_to_asset_id,
    previous_date: str,
    current_date: str,
):
    from src.state_capital_flow import ETFDailyObservation

    shares_by_date = {
        value_date: {
            record["symbol"]: record
            for record in provider.get_exchange_fund_share_records(
                exchange="sh",
                start_date=value_date,
                end_date=value_date,
            )
        }
        for value_date in (previous_date, current_date)
    }
    nav_by_symbol = {
        record["symbol"]: record
        for record in provider.get_exchange_fund_nav_records()
        if record["nav_date"] == current_date
    }
    observations = []
    for symbol, asset_id in symbol_to_asset_id.items():
        if any(
            symbol not in shares_by_date[value_date]
            for value_date in (previous_date, current_date)
        ):
            continue
        for value_date in (previous_date, current_date):
            share = shares_by_date[value_date][symbol]
            nav = nav_by_symbol.get(symbol)
            observations.append(
                ETFDailyObservation(
                    instrument_asset_id=asset_id,
                    trade_date=value_date,
                    available_at=(
                        date.fromisoformat(value_date)
                        + timedelta(days=1)
                    ).isoformat(),
                    total_shares=Decimal(share["shares"]),
                    nav=(
                        Decimal(nav["unit_nav"])
                        if nav is not None
                        and value_date == current_date
                        else None
                    ),
                    source=share["source"],
                    share_unit_continuity_confirmed=False,
                )
            )
    return observations


def _historical_weekly_observations(
    provider,
    *,
    symbol_to_asset_id,
    start_date: str,
    end_date: str,
):
    from src.data_src.common import DataSourceError
    from src.state_capital_flow import ETFDailyObservation

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    current = start
    observations = []
    used_dates = set()
    while current <= end:
        if current.weekday() == 4:
            records = []
            value_date = current
            for offset in range(0, 5):
                candidate = current - timedelta(days=offset)
                try:
                    records = provider.get_exchange_fund_share_records(
                        exchange="sh",
                        start_date=candidate.isoformat(),
                        end_date=candidate.isoformat(),
                    )
                except DataSourceError:
                    records = []
                if records:
                    value_date = candidate
                    break
            if records and value_date not in used_dates:
                used_dates.add(value_date)
                by_symbol = {
                    record["symbol"]: record for record in records
                }
                for symbol, asset_id in symbol_to_asset_id.items():
                    record = by_symbol.get(symbol)
                    if record is None:
                        continue
                    observations.append(
                        ETFDailyObservation(
                            instrument_asset_id=asset_id,
                            trade_date=value_date.isoformat(),
                            available_at=(
                                value_date + timedelta(days=1)
                            ).isoformat(),
                            total_shares=Decimal(record["shares"]),
                            nav=None,
                            source=record["source"],
                            share_unit_continuity_confirmed=False,
                        )
                    )
        current += timedelta(days=1)
    return observations


def _historical_weekly_metrics(
    tracker,
    observations,
    evidence,
):
    week_ends = sorted(
        {
            (
                date.fromisoformat(item.trade_date)
                - timedelta(
                    days=date.fromisoformat(item.trade_date).weekday()
                )
                + timedelta(days=6)
            )
            for item in observations
        }
    )
    metrics = []
    for week_end in week_ends:
        metrics.extend(
            tracker.weekly_metrics(
                basket_id="basket:state-held-broad-etf",
                observations=observations,
                holder_evidence=evidence,
                announcements=[],
                as_of_date=week_end.isoformat(),
            )
        )
    return metrics


if __name__ == "__main__":
    raise SystemExit(main())
