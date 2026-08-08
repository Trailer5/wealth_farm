import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DataAccessResponse,
)
from src.factor_engineering import (
    FactorValue,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_visualization import ChartArtifact, ChartType
from src.research_workflow import (
    ETFResearchExecutionService,
    PROFILE_EXCHANGE_FUND,
    ResearchExecutionOptions,
    ResearchExecutionStatus,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskFactory,
)
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
    default_strategy_registry,
)


class ETFResearchExecutionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "etf-execution.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_medium_long_etf_executes_four_charts(self):
        etf = self._register(
            "fund:csi300",
            ASSET_TYPE_EXCHANGE_FUND,
            "510300",
        )
        task = self._task(
            etf.asset_id,
            ResearchHorizon.MEDIUM_LONG,
            ResearchQuestionType.COMPARISON,
        )
        service, commodity = self._service(etf)

        result = service.execute(
            task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.COMPLETE,
        )
        self.assertEqual(len(result.chart_artifacts), 4)
        self.assertEqual(
            next(
                phase
                for phase in result.phases
                if phase.phase_id == "fundamental"
            ).status.value,
            "not_applicable",
        )
        commodity.calculate_and_store.assert_not_called()

    def test_long_gold_etf_adds_commodity_sensitivity(self):
        etf = self._register(
            "fund:gold",
            ASSET_TYPE_EXCHANGE_FUND,
            "518880",
        )
        task = self._task(
            etf.asset_id,
            ResearchHorizon.LONG,
            ResearchQuestionType.CYCLE_READING,
        )
        service, commodity = self._service(etf)

        result = service.execute(
            task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.COMPLETE,
        )
        commodity.calculate_and_store.assert_called_once()
        self.assertIn(
            "commodity_sensitivity",
            {value.factor_name for value in result.factor_values},
        )

    def _service(self, etf):
        bars = _bars(etf)
        data_access = Mock()
        data_access.get.return_value = bars
        factors = Mock()
        factors.compute_from_response.return_value = []
        factors.compute_risk_from_response.return_value = _risk(
            etf.asset_id
        )
        etf_metrics = Mock()
        etf_metrics.calculate_and_store.return_value = SimpleNamespace(
            values=(
                _factor(etf.asset_id, "premium_discount", 0.001),
                _factor(etf.asset_id, "expense_ratio", 0.002),
            )
        )
        commodity = Mock()
        commodity.calculate_and_store.return_value = _factor(
            etf.asset_id,
            "commodity_sensitivity",
            0.9,
        )
        data_fetch = Mock()
        data_fetch.fetch_exchange_fund_spot.return_value = [
            {
                "代码": etf.standard_symbol,
                "最新价": 4.6,
                "IOPV实时估值": 4.61,
                "数据日期": "2026-08-03",
            }
        ]
        data_fetch.fetch_gold_spot_history.return_value = [
            {
                "date": f"2026-08-0{index}",
                "close": 800 + index,
            }
            for index in range(1, 4)
        ]
        benchmark = Mock()
        benchmark.compare.return_value = SimpleNamespace(
            aligned=SimpleNamespace(dates=("2026-08-01",)),
            values=(
                _factor(etf.asset_id, "tracking_error", 0.03),
                _factor(
                    etf.asset_id,
                    "excess_total_return",
                    0.01,
                ),
            ),
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
            etf.asset_id,
            (
                "gold_etf_cycle_vehicle"
                if etf.standard_symbol == "518880"
                else "etf_tracking_efficiency"
            ),
            (
                ResearchHorizon.LONG
                if etf.standard_symbol == "518880"
                else ResearchHorizon.MEDIUM_LONG
            ),
        )
        service = ETFResearchExecutionService(
            master_data=self.master,
            data_access=data_access,
            data_fetch=data_fetch,
            quality_service=Mock(
                evaluate_data_access_response=Mock(
                    return_value=SimpleNamespace(can_publish=True)
                )
            ),
            factor_service=factors,
            etf_metric_service=etf_metrics,
            commodity_service=commodity,
            benchmark_service=benchmark,
            strategy_registry=default_strategy_registry(),
            chart_renderer=renderer,
            strategy_builder=builder,
            strategy_executor=executor,
        )
        return service, commodity

    def _task(self, asset_id, horizon, question):
        return ResearchTaskFactory(self.master).create(
            asset_id=asset_id,
            profile_id=PROFILE_EXCHANGE_FUND,
            question_type=question,
            horizon=horizon,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.index.asset_id,
        )

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
        dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
        records=tuple(
            {
                "trade_date": f"2026-08-0{index}",
                "open": 4 + index * 0.1,
                "high": 4.2 + index * 0.1,
                "low": 3.9 + index * 0.1,
                "close": 4.1 + index * 0.1,
                "volume": 1000,
            }
            for index in range(1, 4)
        ),
        sources=("unit-test",),
        coverage_start="2026-08-01",
        coverage_end="2026-08-03",
        complete=True,
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
        metrics=(
            _factor(asset_id, "max_drawdown", -0.1),
        ),
        drawdown_episode=None,
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


def _strategy(asset_id, strategy_id, horizon):
    return StrategyResult(
        strategy_id=strategy_id,
        strategy_version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        asset_id=asset_id,
        horizon=horizon,
        as_of_date="2026-08-03",
        valid_until="2026-11-01",
        status=StrategyRunStatus.PASS,
        signal="research_pass",
        score=None,
        confidence=None,
        input_completeness=1,
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
