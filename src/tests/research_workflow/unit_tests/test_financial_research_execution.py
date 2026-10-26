import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    DataAccessResponse,
)
from src.factor_engineering import (
    FactorValue,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.fundamental_analysis import (
    FINANCIAL_INSTITUTION_VERSION,
    FundamentalMetricResult,
    MetricStatus,
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
    PROFILE_FINANCIAL_INSTITUTION,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import (
    FinancialInstitutionResearchExecutionService,
    ResearchExecutionOptions,
    ResearchExecutionStatus,
    ResearchTaskFactory,
)
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
    default_strategy_registry,
)


class FinancialResearchExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "bank-execution.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.bank = self._register(
            "stock:bank",
            ASSET_TYPE_STOCK,
            "000001",
        )
        self.index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=self.bank.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            question_type=ResearchQuestionType.RISK_REVIEW,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.index.asset_id,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bank_execution_returns_profile_metrics_and_charts(self):
        service, _ = self._service()

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.COMPLETE,
        )
        self.assertEqual(len(result.profile_metrics), 5)
        self.assertEqual(len(result.chart_artifacts), 4)
        self.assertEqual(
            result.strategy_result.strategy_id,
            "bank_quality_risk",
        )

    def test_missing_bank_metrics_blocks_later_phases(self):
        financial = Mock()
        financial.analyze.side_effect = ValueError(
            "No report period contains all required bank metrics"
        )
        service, _ = self._service(financial_service=financial)

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.BLOCKED,
        )
        self.assertEqual(
            result.gaps[0].code,
            "bank_metric_calculation_failed",
        )

    def _service(self, financial_service=None):
        bars = _response(self.bank, DATASET_STOCK_DAILY_BARS)
        indicators = _response(
            self.bank,
            DATASET_STOCK_FINANCIAL_INDICATORS,
        )
        data_access = Mock()
        data_access.get.side_effect = [
            bars,
            _response(self.bank, DATASET_STOCK_TOTAL_RETURN_BARS),
            indicators,
        ]
        factors = Mock()
        factors.compute_from_response.return_value = []
        factors.compute_risk_from_response.return_value = _risk(
            self.bank.asset_id
        )
        financial = financial_service or Mock(
            analyze=Mock(return_value=_metrics(self.bank.asset_id))
        )
        benchmark = Mock()
        benchmark.compare.return_value = SimpleNamespace(
            aligned=SimpleNamespace(dates=("2026-08-01",)),
            values=(),
        )
        evidence_service = Mock()
        evidence_service.get_bundle.return_value = SimpleNamespace(
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
            self.bank.asset_id
        )
        return (
            FinancialInstitutionResearchExecutionService(
                master_data=self.master,
                data_access=data_access,
                quality_service=Mock(
                    evaluate_data_access_response=Mock(
                        return_value=SimpleNamespace(
                            can_publish=True
                        )
                    )
                ),
                factor_service=factors,
                financial_service=financial,
                benchmark_service=benchmark,
                evidence_service=evidence_service,
                strategy_registry=default_strategy_registry(),
                chart_renderer=renderer,
                strategy_builder=builder,
                strategy_executor=executor,
            ),
            data_access,
        )

    def _register(self, asset_id, asset_type, symbol):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=symbol,
                market="cn_a",
                exchange=(
                    "sz" if symbol == "000001" else "sh"
                ),
            ),
            source="unit-test",
            raw_symbol=symbol,
        )


def _response(asset, dataset):
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
    if dataset == DATASET_STOCK_FINANCIAL_INDICATORS:
        records = ({"field_name": "roe"},)
    return DataAccessResponse(
        asset=asset,
        dataset=dataset,
        records=records,
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


def _metrics(asset_id):
    return tuple(
        FundamentalMetricResult(
            asset_id=asset_id,
            metric_name=name,
            as_of_date="2026-08-03",
            report_period="2026-03-31",
            status=MetricStatus.AVAILABLE,
            value=Decimal(value),
            unit="ratio",
            formula="unit-test",
            numerator=Decimal(value),
            denominator=Decimal("1"),
            reason=None,
            calculation_version=FINANCIAL_INSTITUTION_VERSION,
            parameters={},
            inputs=(),
            computed_at="2026-08-03T00:00:00+00:00",
        )
        for name, value in (
            ("pb", "0.8"),
            ("roe", "0.1"),
            ("capital_adequacy", "0.13"),
            ("net_interest_margin", "0.018"),
            ("non_performing_loan_ratio", "0.01"),
        )
    )


def _strategy(asset_id):
    return StrategyResult(
        strategy_id="bank_quality_risk",
        strategy_version="v1",
        maturity=StrategyMaturity.RESEARCH_RULE,
        asset_id=asset_id,
        horizon=ResearchHorizon.MEDIUM_LONG,
        as_of_date="2026-08-03",
        valid_until="2026-11-01",
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
