import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    DataAccessResponse,
)
from src.factor_engineering import (
    FactorValue,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_visualization import ChartArtifact
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import (
    CycleResearchExecutionOptions,
    CyclicalResourceResearchExecutionService,
    ResearchExecutionStatus,
    ResearchTaskFactory,
)
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
    default_strategy_registry,
)


class CycleResearchExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "cycle-execution.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.asset = self._register(
            "stock:zijin",
            ASSET_TYPE_STOCK,
            "601899",
        )
        self.index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            question_type=ResearchQuestionType.CYCLE_READING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.index.asset_id,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cycle_execution_uses_explicit_three_year_weights(self):
        service, commodity = self._service()
        options = CycleResearchExecutionOptions(
            allow_remote=False,
            commodity_weights=(
                ("sina:CU0", 0.7),
                ("sge:Au99.99", 0.3),
            ),
        )

        result = service.execute(self.task, options=options)

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.COMPLETE,
        )
        self.assertEqual(result.windows.strategy_window_days, 1095)
        self.assertEqual(len(result.chart_artifacts), 4)
        call = commodity.calculate_and_store.call_args.kwargs
        self.assertEqual(
            call["weights"],
            {"sina:CU0": 0.7, "sge:Au99.99": 0.3},
        )

    def test_cycle_options_reject_invalid_weights(self):
        with self.assertRaisesRegex(
            ValueError,
            "sum to 1",
        ):
            CycleResearchExecutionOptions(
                commodity_weights=(
                    ("sina:CU0", 0.8),
                    ("sge:Au99.99", 0.3),
                )
            )

    def _service(self):
        bars = _bars(self.asset)
        data_access = Mock()
        data_access.get.side_effect = lambda request: replace(
            bars,
            dataset=request.dataset,
        )
        data_access.get_financial_bundle.return_value = (
            SimpleNamespace(sources=("akshare",))
        )
        factors = Mock()
        factors.compute_from_response.return_value = []
        factors.compute_risk_from_response.return_value = _risk(
            self.asset.asset_id
        )
        commodity = Mock()
        commodity.calculate_and_store.return_value = _factor(
            self.asset.asset_id,
            "commodity_sensitivity",
            0.9,
        )
        fetch = Mock()
        fetch.fetch_copper_continuous_history.return_value = (
            _commodity_records(80000)
        )
        fetch.fetch_gold_spot_history.return_value = (
            _commodity_records(800)
        )
        fundamental = SimpleNamespace(
            metrics=(
                _metric(self.asset.asset_id, "pb", "2"),
                _metric(self.asset.asset_id, "roic", "0.1"),
                _metric(
                    self.asset.asset_id,
                    "net_debt",
                    "100",
                ),
            )
        )
        fundamental_service = Mock()
        fundamental_service.analyze.return_value = fundamental
        benchmark = Mock()
        benchmark.compare.return_value = SimpleNamespace(
            aligned=SimpleNamespace(dates=("2026-08-01",)),
            values=(),
        )
        evidence = Mock()
        evidence.get_bundle.return_value = SimpleNamespace(
            complete=True,
            evidence=(),
            sources=("cninfo",),
            errors={},
        )
        renderer = Mock()
        renderer.render.side_effect = lambda spec: ChartArtifact(
            chart_type=spec.chart_type,
            input_digest=spec.chart_type.value,
            svg_path=Path("x.svg"),
            png_path=Path("x.png"),
            data_path=Path("x.json"),
            metadata_path=Path("x.meta.json"),
        )
        builder = Mock()
        builder.build.return_value = object()
        executor = Mock()
        executor.execute.return_value = _strategy(
            self.asset.asset_id
        )
        service = CyclicalResourceResearchExecutionService(
            master_data=self.master,
            data_access=data_access,
            data_fetch=fetch,
            quality_service=Mock(
                evaluate_data_access_response=Mock(
                    return_value=SimpleNamespace(can_publish=True)
                )
            ),
            factor_service=factors,
            fundamental_service=fundamental_service,
            commodity_service=commodity,
            benchmark_service=benchmark,
            evidence_service=evidence,
            strategy_registry=default_strategy_registry(),
            chart_renderer=renderer,
            strategy_builder=builder,
            strategy_executor=executor,
        )
        return service, commodity

    def _register(self, asset_id, asset_type, symbol):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=symbol,
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol=symbol,
        )


def _bars(asset):
    return DataAccessResponse(
        asset=asset,
        dataset=DATASET_STOCK_DAILY_BARS,
        records=tuple(
            {
                "trade_date": f"2026-08-0{index}",
                "open": 10 + index,
                "high": 11 + index,
                "low": 9 + index,
                "close": 10 + index,
                "volume": 1000,
            }
            for index in range(1, 4)
        ),
        sources=("unit-test",),
        coverage_start="2026-08-01",
        coverage_end="2026-08-03",
        complete=True,
    )


def _factor(asset_id, name, value, value_date="2026-08-03"):
    return FactorValue(
        asset_id=asset_id,
        factor_name=name,
        as_of_date=value_date,
        value=value,
        calculation_version="unit-v1",
        parameters={"window": "input"},
        input_start_date="2026-08-01",
        input_end_date=value_date,
        source_dataset="unit-test",
        computed_at="2026-08-03T00:00:00+00:00",
    )


def _risk(asset_id):
    series = []
    for index in range(1, 4):
        value_date = f"2026-08-0{index}"
        for name, value in (
            ("cumulative_return", index * 0.01),
            ("drawdown_series", -index * 0.01),
            ("rolling_annualized_volatility_5d", 0.2),
            ("rolling_sharpe_5d", 1.0),
        ):
            series.append(_factor(asset_id, name, value, value_date))
    return RiskCalculationResult(
        asset_id=asset_id,
        parameters=RiskMetricParameters(
            rolling_windows=(5, 10),
            price_adjustment="none",
        ),
        series=tuple(series),
        metrics=(_factor(asset_id, "max_drawdown", -0.1),),
        drawdown_episode=None,
    )


def _commodity_records(base):
    return [
        {"date": f"2026-08-0{index}", "close": base + index}
        for index in range(1, 4)
    ]


def _metric(asset_id, name, value):
    from src.fundamental_analysis import (
        FUNDAMENTAL_CALCULATION_VERSION,
        FundamentalMetricResult,
        MetricStatus,
    )

    return FundamentalMetricResult(
        asset_id=asset_id,
        metric_name=name,
        as_of_date="2026-08-03",
        report_period="2025-12-31",
        status=MetricStatus.AVAILABLE,
        value=Decimal(value),
        unit="ratio",
        formula="unit-test",
        numerator=Decimal(value),
        denominator=Decimal("1"),
        reason=None,
        calculation_version=FUNDAMENTAL_CALCULATION_VERSION,
        parameters={},
        inputs=(),
        computed_at="2026-08-03T00:00:00+00:00",
    )


def _strategy(asset_id):
    return StrategyResult(
        strategy_id="resource_cycle_readiness",
        strategy_version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        asset_id=asset_id,
        horizon=ResearchHorizon.LONG,
        as_of_date="2026-08-03",
        valid_until="2027-02-01",
        status=StrategyRunStatus.PASS,
        signal="research_pass",
        score=None,
        confidence=None,
        input_completeness=Decimal("1"),
        matched_conditions=(),
        failed_conditions=(),
        vetoed_conditions=(),
        missing_metrics=(),
        missing_evidence_types=(),
        metric_versions={},
        counterevidence=(),
        limitations=(),
    )


if __name__ == "__main__":
    unittest.main()
