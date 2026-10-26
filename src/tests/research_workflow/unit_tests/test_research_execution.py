import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    DataAccessResponse,
)
from src.factor_engineering import (
    FactorValue,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.fundamental_analysis import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    FundamentalMetricSet,
    FundamentalSnapshot,
    MetricStatus,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_visualization import (
    ChartArtifact,
    ChartType,
)
from src.research_contracts import (
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import (
    ResearchExecutionOptions,
    ResearchExecutionService,
    ResearchExecutionStatus,
    ResearchTaskFactory,
)
from src.research_workflow.return_bars import fetch_total_return_bars
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
    default_strategy_registry,
)


class ResearchExecutionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "execution.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.asset = self._register(
            "asset:company",
            ASSET_TYPE_STOCK,
            "600519",
            "贵州茅台",
        )
        self.index = self._register(
            "asset:index",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.index.asset_id,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bar_failure_returns_blocked_with_skipped_phases(self):
        data_access = Mock()
        data_access.get.side_effect = RuntimeError("provider down")
        service = self._service(data_access=data_access)

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.BLOCKED,
        )
        self.assertEqual(result.gaps[0].code, "daily_bars_unavailable")
        self.assertEqual(len(result.phases), 9)
        self.assertEqual(result.phases[-1].status.value, "skipped")

    def test_happy_path_returns_four_charts_and_strategy_result(self):
        bars = _bar_response(self.asset)
        data_access = Mock()
        data_access.get.side_effect = lambda request: replace(
            bars,
            dataset=request.dataset,
        )
        data_access.get_financial_bundle.return_value = (
            SimpleNamespace(sources=("akshare",))
        )
        factor_service = Mock()
        factor_service.compute_from_response.return_value = []
        risk = _risk_result(self.asset.asset_id)
        factor_service.compute_risk_from_response.return_value = risk
        fundamental = _fundamental(self.asset.asset_id)
        fundamental_service = Mock()
        fundamental_service.analyze.return_value = fundamental
        benchmark_service = Mock()
        benchmark_service.compare.return_value = SimpleNamespace(
            aligned=SimpleNamespace(dates=("2026-08-01",)),
            values=(),
        )
        evidence = SimpleNamespace(
            complete=True,
            evidence=(),
            sources=("cninfo",),
            errors={},
        )
        evidence_service = Mock()
        evidence_service.get_bundle.return_value = evidence
        strategy_builder = Mock()
        strategy_builder.build.return_value = object()
        strategy_executor = Mock()
        strategy_executor.execute.return_value = _strategy_result(
            self.asset.asset_id
        )
        renderer = Mock()
        renderer.render.side_effect = lambda spec: ChartArtifact(
            chart_type=spec.chart_type,
            input_digest=f"digest-{spec.chart_type.value}",
            svg_path=Path("x.svg"),
            png_path=Path("x.png"),
            data_path=Path("x.json"),
            metadata_path=Path("x.meta.json"),
        )
        service = self._service(
            data_access=data_access,
            factor_service=factor_service,
            fundamental_service=fundamental_service,
            benchmark_service=benchmark_service,
            evidence_service=evidence_service,
            chart_renderer=renderer,
            strategy_builder=strategy_builder,
            strategy_executor=strategy_executor,
        )

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.COMPLETE,
        )
        self.assertEqual(len(result.chart_artifacts), 4)
        self.assertEqual(
            {item.chart_type for item in result.chart_artifacts},
            {
                ChartType.PRICE_VOLUME,
                ChartType.CUMULATIVE_RETURN,
                ChartType.DRAWDOWN,
                ChartType.ROLLING_RISK,
            },
        )
        self.assertEqual(
            result.strategy_result.strategy_id,
            "quality_value",
        )
        self.assertEqual(result.windows.strategy_window_days, 730)
        risk_call = factor_service.compute_risk_from_response.call_args
        self.assertEqual(
            risk_call.args[0].dataset,
            DATASET_STOCK_TOTAL_RETURN_BARS,
        )
        self.assertEqual(
            risk_call.kwargs["parameters"].price_adjustment,
            "total_return",
        )
        self.assertEqual(result.bar_response.dataset, DATASET_STOCK_DAILY_BARS)

    def test_total_return_failure_falls_back_with_visible_gap(self):
        bars = _bar_response(self.asset)
        data_access = Mock()
        data_access.get.side_effect = RuntimeError("baostock down")

        response, gap = fetch_total_return_bars(
            data_access,
            bars,
            start_date="2026-08-01",
            end_date="2026-08-03",
            as_of_date="2026-08-03",
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertIs(response, bars)
        self.assertEqual(gap.code, "total_return_bars_unavailable")
        self.assertFalse(gap.blocking)
        self.assertIn("未复权", gap.message)

    def _service(self, **overrides):
        defaults = {
            "data_access": Mock(),
            "quality_service": Mock(
                evaluate_data_access_response=Mock(
                    return_value=SimpleNamespace(can_publish=True)
                )
            ),
            "factor_service": Mock(),
            "fundamental_service": Mock(),
            "benchmark_service": Mock(),
            "evidence_service": Mock(),
            "chart_renderer": Mock(),
            "strategy_builder": Mock(),
            "strategy_executor": Mock(),
        }
        defaults.update(overrides)
        return ResearchExecutionService(
            master_data=self.master,
            strategy_registry=default_strategy_registry(),
            **defaults,
        )

    def _register(self, asset_id, asset_type, symbol, name):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=name,
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol=symbol,
        )


def _bar_response(asset):
    records = tuple(
        {
            "trade_date": f"2026-08-0{index}",
            "open": 10 + index,
            "high": 11 + index,
            "low": 9 + index,
            "close": 10 + index,
            "volume": 1000,
        }
        for index in range(1, 4)
    )
    return DataAccessResponse(
        asset=asset,
        dataset=DATASET_STOCK_DAILY_BARS,
        records=records,
        sources=("unit-test",),
        coverage_start="2026-08-01",
        coverage_end="2026-08-03",
        complete=True,
    )


def _risk_result(asset_id):
    values = []
    for index in range(1, 4):
        value_date = f"2026-08-0{index}"
        for name, value in (
            ("cumulative_return", index * 0.01),
            ("drawdown_series", -index * 0.01),
            ("rolling_annualized_volatility_5d", 0.2),
            ("rolling_sharpe_5d", 1.0),
        ):
            values.append(
                FactorValue(
                    asset_id=asset_id,
                    factor_name=name,
                    as_of_date=value_date,
                    value=value,
                    calculation_version="risk_daily_v1",
                    parameters={"window": "input"},
                    input_start_date="2026-08-01",
                    input_end_date=value_date,
                    source_dataset=DATASET_STOCK_DAILY_BARS,
                    computed_at="2026-08-03T00:00:00+00:00",
                )
            )
    return RiskCalculationResult(
        asset_id=asset_id,
        parameters=RiskMetricParameters(
            rolling_windows=(5, 10),
            price_adjustment="none",
        ),
        series=tuple(values),
        metrics=(),
        drawdown_episode=None,
    )


def _fundamental(asset_id):
    values = {
        "pe_ttm": "20",
        "pb": "4",
        "ps_ttm": "7",
        "roe": "0.20",
        "roic": "0.18",
        "fcf_yield": "0.05",
        "debt_to_assets": "0.30",
        "revenue_growth": "0.08",
    }
    metrics = tuple(
        FundamentalMetricResult(
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
        for name, value in values.items()
    )
    snapshot = FundamentalSnapshot(
        asset_id=asset_id,
        as_of_date="2026-08-03",
        report_period="2025-12-31",
        currency="CNY",
        market_price=Decimal("13"),
        shares_outstanding=Decimal("100"),
        fields={
            "revenue": Decimal("100"),
            "earnings": Decimal("20"),
            "operating_cash_flow": Decimal("30"),
            "free_cash_flow": Decimal("25"),
        },
        prior_fields={},
    )
    return FundamentalMetricSet(snapshot=snapshot, metrics=metrics)


def _strategy_result(asset_id):
    return StrategyResult(
        strategy_id="quality_value",
        strategy_version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        asset_id=asset_id,
        horizon=ResearchHorizon.MEDIUM_LONG,
        as_of_date="2026-08-03",
        valid_until="2026-11-01",
        status=StrategyRunStatus.PASS,
        signal="research_pass",
        score=Decimal("1"),
        confidence=Decimal("1"),
        input_completeness=Decimal("1"),
        matched_conditions=(),
        failed_conditions=(),
        vetoed_conditions=(),
        missing_metrics=(),
        missing_evidence_types=(),
        metric_versions={"roe": "fundamental_v1"},
        counterevidence=(),
        limitations=(),
    )


if __name__ == "__main__":
    unittest.main()
