"""Run the discovery funnel over the real current CSI 300 universe."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import (
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_universe import (
        MembershipStatus,
        ResearchUniverseService,
        SQLiteResearchUniverseRepository,
    )
    from src.research_contracts import PROFILE_ORDINARY_COMPANY
    from src.research_workflow import ResearchTaskFactory
    from src.security_discovery import (
        DiscoveryMemberInput,
        SQLiteSecurityDiscoveryRepository,
        SecurityDiscoveryService,
        default_quality_value_discovery_spec,
    )
    from src.strategy import StrategyMetricInput

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "csi300-discovery.sqlite3"
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = master.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_index",
                exchange="csi",
            ),
            source="project:index_catalog",
            raw_symbol="000300",
        )
        universe_service = ResearchUniverseService(
            master_data=master,
            data_fetch=DataFetchService(
                store=SQLiteDataStore(db_path),
                master_data_service=master,
            ),
            repository=SQLiteResearchUniverseRepository(db_path),
        )
        snapshot = universe_service.refresh_index_universe(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id=index.asset_id,
            provider_timeout_seconds=120,
        )
        resolved = [
            member
            for member in snapshot.memberships
            if member.status is MembershipStatus.RESOLVED
            and member.asset_id is not None
        ]
        if len(resolved) != 300:
            raise AssertionError(
                f"Expected 300 resolved members, got {len(resolved)}"
            )

        fixture_values = _fixture_values()
        inputs = {
            member.asset_id: DiscoveryMemberInput(
                asset_id=member.asset_id,
                profile_id=PROFILE_ORDINARY_COMPANY,
                metrics={
                    name: StrategyMetricInput(
                        metric_name=name,
                        value=Decimal(value),
                        as_of_date="2026-08-03",
                        calculation_version=(
                            "discovery_smoke_fixture_v1"
                        ),
                    )
                    for name, value in fixture_values[index].items()
                },
            )
            for index, member in enumerate(resolved[:6])
        }
        spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version=snapshot.universe.version,
            as_of_date="2026-08-03",
        )
        repository = SQLiteSecurityDiscoveryRepository(db_path)
        service = SecurityDiscoveryService(
            universe_service=universe_service,
            task_factory=ResearchTaskFactory(master),
            repository=repository,
        )
        run = service.run(spec, member_inputs=inputs)
        if len(run.outcomes) != len(snapshot.memberships):
            raise AssertionError(
                "Every universe member must have an outcome"
            )
        if len(run.candidates) != 3:
            raise AssertionError(
                f"Expected 3 fixture candidates, got "
                f"{len(run.candidates)}"
            )
        if repository.get(run.run_id) is None:
            raise AssertionError(
                "Persisted discovery run was not found"
            )
        factory = ResearchTaskFactory(master)
        for candidate in run.candidates:
            factory.restore(
                candidate.research_task_draft.to_dict()
            )

        counts = Counter(
            outcome.status.value for outcome in run.outcomes
        )
        print(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "universe_version": run.universe_version,
                    "universe_members": len(snapshot.memberships),
                    "metric_mode": (
                        "six deterministic fixture members; "
                        "not investment evidence"
                    ),
                    "status_counts": dict(sorted(counts.items())),
                    "candidate_tracks": [
                        {
                            "symbol": candidate.source_symbol,
                            "track": candidate.selected_track,
                            "task_id": (
                                candidate.research_task_draft.task_id
                            ),
                        }
                        for candidate in run.candidates
                    ],
                    "is_buy_recommendation": (
                        run.is_buy_recommendation
                    ),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


def _fixture_values() -> list[dict[str, str]]:
    return [
        {
            "roe": "0.20",
            "roic": "0.15",
            "debt_to_assets": "0.30",
            "pe_ttm": "25",
            "fcf_yield": "0.02",
            "excess_total_return": "0",
            "max_drawdown": "-0.20",
            "annualized_volatility": "0.30",
        },
        {
            "roe": "0.10",
            "roic": "0.06",
            "debt_to_assets": "0.60",
            "pe_ttm": "12",
            "fcf_yield": "0.05",
            "excess_total_return": "0",
            "max_drawdown": "-0.40",
            "annualized_volatility": "0.50",
        },
        {
            "roe": "0.10",
            "roic": "0.06",
            "debt_to_assets": "0.60",
            "pe_ttm": "30",
            "fcf_yield": "0.01",
            "excess_total_return": "0",
            "max_drawdown": "-0.10",
            "annualized_volatility": "0.20",
        },
        {
            "roe": "0.04",
            "roic": "0.03",
            "debt_to_assets": "0.60",
            "pe_ttm": "20",
            "fcf_yield": "0.01",
            "excess_total_return": "0",
            "max_drawdown": "-0.20",
            "annualized_volatility": "0.30",
        },
        {
            "roe": "0.12",
            "roic": "0.08",
            "debt_to_assets": "0.60",
            "pe_ttm": "20",
            "excess_total_return": "0",
            "max_drawdown": "-0.20",
            "annualized_volatility": "0.30",
        },
        {
            "roe": "0.10",
            "roic": "0.05",
            "debt_to_assets": "0.65",
            "pe_ttm": "30",
            "fcf_yield": "0.01",
            "excess_total_return": "-0.20",
            "max_drawdown": "-0.40",
            "annualized_volatility": "0.50",
        },
    ]


if __name__ == "__main__":
    raise SystemExit(main())
