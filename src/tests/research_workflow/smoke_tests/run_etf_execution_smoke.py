"""Run formal ETF research for index and gold ETFs."""

from __future__ import annotations

import json
import sys
import tempfile
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
    from src.data_quality import (
        DataQualityService,
        SQLiteDataQualityRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.factor_engineering import (
        CommoditySensitivityService,
        FactorService,
        SQLiteFactorRepository,
    )
    from src.fund_research import ExchangeFundMetricService
    from src.master_data import (
        ASSET_TYPE_EXCHANGE_FUND,
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_visualization import ChartRenderer
    from src.research_workflow import (
        ETFResearchExecutionService,
        PROFILE_EXCHANGE_FUND,
        ResearchExecutionOptions,
        ResearchHorizon,
        ResearchQuestionType,
        ResearchTaskFactory,
    )
    from src.strategy import (
        StrategyRunStatus,
        default_strategy_registry,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "etf-execution.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = _register(
            master,
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
        )
        funds = {
            "csi300": _register(
                master,
                "fund:csi300",
                ASSET_TYPE_EXCHANGE_FUND,
                "510300",
                "沪深300ETF",
            ),
            "gold": _register(
                master,
                "fund:gold",
                ASSET_TYPE_EXCHANGE_FUND,
                "518880",
                "黄金ETF",
            ),
        }
        fetch = DataFetchService(
            store=store,
            master_data_service=master,
        )
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch,
        )
        factors = SQLiteFactorRepository(db_path)
        service = ETFResearchExecutionService(
            master_data=master,
            data_access=data_access,
            data_fetch=fetch,
            quality_service=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            factor_service=FactorService(factors, master),
            etf_metric_service=ExchangeFundMetricService(
                master_data=master,
                factor_repository=factors,
            ),
            commodity_service=CommoditySensitivityService(
                factors
            ),
            benchmark_service=BenchmarkAnalysisService(
                master_data=master,
                data_access=data_access,
                factor_repository=factors,
                benchmark_repository=SQLiteBenchmarkRepository(
                    db_path
                ),
            ),
            strategy_registry=default_strategy_registry(),
            chart_renderer=ChartRenderer(root / "research"),
        )
        factory = ResearchTaskFactory(master)
        tasks = {
            "csi300": factory.create(
                asset_id=funds["csi300"].asset_id,
                profile_id=PROFILE_EXCHANGE_FUND,
                question_type=ResearchQuestionType.COMPARISON,
                horizon=ResearchHorizon.MEDIUM_LONG,
                as_of_date="2026-08-03",
                benchmark_asset_id=index.asset_id,
            ),
            "gold": factory.create(
                asset_id=funds["gold"].asset_id,
                profile_id=PROFILE_EXCHANGE_FUND,
                question_type=ResearchQuestionType.CYCLE_READING,
                horizon=ResearchHorizon.LONG,
                as_of_date="2026-08-03",
                benchmark_asset_id=index.asset_id,
            ),
        }
        output = {}
        for name, task in tasks.items():
            result = service.execute(
                task,
                options=ResearchExecutionOptions(
                    provider_timeout_seconds=120,
                ),
            )
            if result.status.value != "complete":
                raise AssertionError(
                    f"ETF execution incomplete: {result.gaps}"
                )
            if len(result.chart_artifacts) != 4:
                raise AssertionError("ETF requires four charts")
            if (
                result.strategy_result is None
                or result.strategy_result.status
                is StrategyRunStatus.INSUFFICIENT_DATA
            ):
                raise AssertionError("ETF strategy input incomplete")
            output[name] = {
                "status": result.status.value,
                "window_days": (
                    result.windows.strategy_window_days
                ),
                "chart_count": len(result.chart_artifacts),
                "strategy": result.strategy_result.strategy_id,
                "strategy_status": (
                    result.strategy_result.status.value
                ),
                "metric_versions": sorted(
                    {
                        value.calculation_version
                        for value in result.factor_values
                    }
                ),
            }
        print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


def _register(master, asset_id, asset_type, symbol, name):
    from src.master_data import Asset

    return master.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=symbol,
            display_name=name,
            market="cn_a",
            exchange="sh",
        ),
        source="etf-execution:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
