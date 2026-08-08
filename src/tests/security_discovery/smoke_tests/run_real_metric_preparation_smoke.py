"""Prepare real discovery metrics for a bounded CSI 300 sample."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.benchmark_analysis import (
        BenchmarkAnalysisService,
        SQLiteBenchmarkRepository,
    )
    from src.data_access import (
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.factor_engineering import (
        FactorService,
        SQLiteFactorRepository,
    )
    from src.financial_data import SQLiteFinancialDataRepository
    from src.fundamental_analysis import (
        FundamentalAnalysisService,
        SQLiteFundamentalMetricRepository,
    )
    from src.master_data import (
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_universe import (
        ResearchUniverseService,
        SQLiteResearchUniverseRepository,
    )
    from src.research_workflow import (
        ResearchProfileAssignmentService,
        ResearchExecutionOptions,
        ResearchTaskFactory,
        SQLiteResearchProfileAssignmentRepository,
    )
    from src.security_discovery import (
        DiscoveryMetricPreparationService,
        DiscoveryPreparationJobService,
        SecurityDiscoveryService,
        SQLiteDiscoveryPreparationJobRepository,
        SQLiteSecurityDiscoveryRepository,
        StoredDiscoveryMetricLoader,
        default_quality_value_discovery_spec,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "real-preparation.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = master.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_a",
                exchange="sh",
            ),
            source="smoke:catalog",
            raw_symbol="000300",
        )
        fetch = DataFetchService(
            store=store,
            master_data_service=master,
        )
        universe = ResearchUniverseService(
            master_data=master,
            data_fetch=fetch,
            repository=SQLiteResearchUniverseRepository(db_path),
        )
        snapshot = universe.refresh_index_universe(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id=index.asset_id,
            provider_timeout_seconds=120,
        )
        asset_by_symbol = {
            item.source_symbol: item.asset_id
            for item in snapshot.memberships
            if item.asset_id is not None
        }
        industries = fetch.fetch_stock_industries(
            list(asset_by_symbol)
        )
        profile_service = ResearchProfileAssignmentService(
            master_data=master,
            repository=SQLiteResearchProfileAssignmentRepository(
                db_path
            ),
        )
        profile_service.classify_industries(
            industries,
            asset_by_symbol=asset_by_symbol,
            effective_date=snapshot.universe.effective_date,
        )
        profile_map = profile_service.profile_map(
            list(asset_by_symbol.values()),
            as_of_date="2026-08-03",
        )
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch,
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        factors = SQLiteFactorRepository(db_path)
        fundamentals = SQLiteFundamentalMetricRepository(db_path)
        loader = StoredDiscoveryMetricLoader(
            fundamental_repository=fundamentals,
            factor_repository=factors,
            factor_parameter_filters={
                "max_drawdown": {"window": "input"},
                "annualized_volatility": {"window": "input"},
            },
        )
        benchmark = BenchmarkAnalysisService(
            master_data=master,
            data_access=data_access,
            factor_repository=factors,
            benchmark_repository=SQLiteBenchmarkRepository(db_path),
        )
        spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version=snapshot.universe.version,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        preparation_service = DiscoveryMetricPreparationService(
            master_data=master,
            universe_service=universe,
            metric_loader=loader,
            data_access=data_access,
            factor_service=FactorService(factors, master),
            fundamental_service=FundamentalAnalysisService(
                master_data=master,
                repository=fundamentals,
            ),
            benchmark_service=benchmark,
        )
        job_service = DiscoveryPreparationJobService(
            preparation_service=preparation_service,
            repository=SQLiteDiscoveryPreparationJobRepository(
                db_path
            ),
        )
        job_service.create_job(
            job_id="job:csi300:2026-08-03",
            spec=spec,
            max_attempts_per_member=1,
        )
        first_batch = job_service.run_batch(
            job_id="job:csi300:2026-08-03",
            spec=spec,
            profile_by_asset_id=profile_map,
            batch_size=2,
            options=ResearchExecutionOptions(
                provider_timeout_seconds=120,
            ),
        )
        second_batch = job_service.run_batch(
            job_id="job:csi300:2026-08-03",
            spec=spec,
            profile_by_asset_id=profile_map,
            batch_size=2,
            options=ResearchExecutionOptions(
                provider_timeout_seconds=120,
            ),
        )
        states = job_service.repository.member_states(
            "job:csi300:2026-08-03"
        )
        selected_symbols = tuple(
            str(state["source_symbol"])
            for state in states.values()
            if state["status"] == "ready"
        )
        if len(selected_symbols) < 2:
            raise AssertionError(
                f"Expected resumable real progress: {states}"
            )
        discovery = SecurityDiscoveryService(
            universe_service=universe,
            task_factory=ResearchTaskFactory(master),
            repository=SQLiteSecurityDiscoveryRepository(db_path),
            metric_loader=loader,
        ).run(spec)
        outcomes = {
            item.source_symbol: item
            for item in discovery.outcomes
        }
        if any(
            outcomes[symbol].status.value == "insufficient_data"
            for symbol in selected_symbols
        ):
            raise AssertionError(
                "Prepared real members remained insufficient"
            )
        status_counts = Counter(
            item.status.value for item in discovery.outcomes
        )
        print(
            json.dumps(
                {
                    "universe_members": len(snapshot.memberships),
                    "real_prepared_symbols": list(
                        selected_symbols
                    ),
                    "batch_remote_attempted": [
                        first_batch.last_batch_remote_attempted,
                        second_batch.last_batch_remote_attempted,
                    ],
                    "job_status": second_batch.job.status,
                    "job_counts": second_batch.counts,
                    "discovery_status_counts": dict(
                        sorted(status_counts.items())
                    ),
                    "selected_outcomes": {
                        symbol: outcomes[symbol].status.value
                        for symbol in selected_symbols
                    },
                    "fixture_metrics_used": False,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
