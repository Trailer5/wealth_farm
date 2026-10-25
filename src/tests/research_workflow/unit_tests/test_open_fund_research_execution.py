import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.benchmark_analysis import (
    BenchmarkComponent,
    BenchmarkComponentKind,
    BenchmarkComponentUnavailableError,
    CompositeBenchmarkDefinition,
)
from src.factor_engineering import (
    FactorValue,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.fund_research import (
    DisclosureExtractionStatus,
    FundResearchBundle,
    OpenFundProfileSnapshot,
)
from src.master_data import (
    ASSET_TYPE_OPEN_FUND,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_visualization import ChartArtifact
from src.research_workflow import (
    OpenFundResearchExecutionService,
    PROFILE_OPEN_FUND,
    ResearchExecutionOptions,
    ResearchExecutionStatus,
    ResearchHorizon,
    ResearchPhaseStatus,
    ResearchQuestionType,
    ResearchTaskFactory,
)


class OpenFundResearchExecutionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "open-fund.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.asset = self.master.register_asset(
            Asset(
                asset_id="fund:163417",
                asset_type=ASSET_TYPE_OPEN_FUND,
                standard_symbol="163417",
                display_name="兴全合宜混合(LOF)A",
                market="cn_fund",
                exchange=None,
            ),
            source="unit-test",
            raw_symbol="163417",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_nav_is_normalized_for_risk_and_charts(self):
        bundle = _bundle(self.asset)
        service = self._service(bundle)

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.PARTIAL,
        )
        self.assertIs(result.fund_bundle, bundle)
        self.assertEqual(len(result.chart_artifacts), 3)
        self.assertEqual(
            result.bar_response.records[0]["trade_date"],
            "2026-08-20",
        )
        self.assertEqual(result.bar_response.records[0]["close"], 1.5)
        self.assertIsNotNone(result.open_fund_standard_metrics)
        fund_research = service.fund_research
        self.assertEqual(
            fund_research.get_open_fund_bundle.call_args.kwargs[
                "start_date"
            ],
            "1900-01-01",
        )
        strategy_phase = next(
            phase
            for phase in result.phases
            if phase.phase_id == "strategy"
        )
        self.assertIs(
            strategy_phase.status,
            ResearchPhaseStatus.NOT_APPLICABLE,
        )
        benchmark_phase = next(
            phase
            for phase in result.phases
            if phase.phase_id == "benchmark"
        )
        self.assertIs(
            benchmark_phase.status,
            ResearchPhaseStatus.SUCCESS,
        )
        self.assertIsNotNone(result.benchmark_result)
        self.assertIn(
            "open_fund_benchmark_cash_rate_history_approximated",
            {gap.code for gap in result.gaps},
        )

    def test_missing_allocation_is_a_nonblocking_gap(self):
        bundle = _bundle(self.asset, allocation_records=())
        service = self._service(bundle)

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.PARTIAL,
        )
        allocation_gap = next(
            gap
            for gap in result.gaps
            if gap.code == "fund_allocation_empty"
        )
        self.assertFalse(allocation_gap.blocking)

    def test_missing_platform_estimate_does_not_downgrade_baseline(self):
        service = self._service(
            _bundle(self.asset, estimate_records=())
        )

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.PARTIAL,
        )
        self.assertNotIn(
            "fund_estimates_empty",
            {gap.code for gap in result.gaps},
        )

    def test_benchmark_source_failure_is_nonblocking_and_not_degraded(self):
        service = self._service(
            _bundle(self.asset),
            benchmark_error=BenchmarkComponentUnavailableError(
                "required component unavailable"
            ),
        )

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.status,
            ResearchExecutionStatus.PARTIAL,
        )
        self.assertIn(
            "open_fund_benchmark_source_unavailable",
            {gap.code for gap in result.gaps},
        )
        self.assertIsNone(result.benchmark_result)

    def test_empty_nav_distinguishes_business_no_data(self):
        service = self._service(
            replace(_bundle(self.asset), nav_records=(), errors={})
        )

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(result.status, ResearchExecutionStatus.BLOCKED)
        self.assertIn(
            "open_fund_nav_business_empty",
            {gap.code for gap in result.gaps},
        )

    def test_empty_nav_distinguishes_remote_source_failure(self):
        service = self._service(
            replace(
                _bundle(self.asset),
                nav_records=(),
                errors={"open_fund_nav": "provider timeout"},
            )
        )

        result = service.execute(
            self.task,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(result.status, ResearchExecutionStatus.BLOCKED)
        self.assertIn(
            "open_fund_nav_source_unavailable",
            {gap.code for gap in result.gaps},
        )

    def _service(self, bundle, *, benchmark_error=None):
        fund_research = Mock()
        fund_research.get_open_fund_bundle.return_value = bundle
        factors = Mock()
        factors.compute_from_response.return_value = []
        factors.compute_open_fund_standard_from_response.return_value = (
            SimpleNamespace(
                values=(),
                unavailable_windows=(),
                nav_basis="unit_nav",
                return_semantics=(
                    "unit_nav_change_excludes_cash_distributions"
                ),
            )
        )
        factors.compute_risk_from_response.return_value = _risk(
            self.asset.asset_id
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
        definition = _definition(self.asset.asset_id)
        benchmark_resolver = Mock()
        benchmark_resolver.resolve.return_value = definition
        benchmark_service = Mock()
        if benchmark_error is not None:
            benchmark_service.compare_composite.side_effect = (
                benchmark_error
            )
        else:
            benchmark_service.compare_composite.return_value = (
                SimpleNamespace(
                    values=(),
                    metrics=(),
                    aligned=SimpleNamespace(
                        dates=("2026-08-21",)
                    ),
                    benchmark_definition=definition,
                )
            )
        return OpenFundResearchExecutionService(
            master_data=self.master,
            fund_research=fund_research,
            quality_service=Mock(
                evaluate_data_access_response=Mock(
                    return_value=SimpleNamespace(can_publish=True)
                )
            ),
            factor_service=factors,
            benchmark_service=benchmark_service,
            benchmark_resolver=benchmark_resolver,
            chart_renderer=renderer,
        )


def _bundle(
    asset,
    *,
    allocation_records=None,
    estimate_records=None,
):
    return FundResearchBundle(
        asset=asset,
        start_date="2016-08-24",
        end_date="2026-08-22",
        as_of_date="2026-08-22",
        nav_records=(
            {
                "symbol": "163417",
                "nav_date": "2026-08-20",
                "unit_nav": 1.5,
                "source": "unit-test:nav",
            },
            {
                "symbol": "163417",
                "nav_date": "2026-08-21",
                "unit_nav": 1.52,
                "source": "unit-test:nav",
            },
        ),
        estimate_records=(
            ({"estimated_nav": 1.52},)
            if estimate_records is None
            else estimate_records
        ),
        status_records=({"purchase_status": "开放申购"},),
        scale_records=({"scale": 100},),
        allocation_records=(
            ({"stock_ratio": 0.8},)
            if allocation_records is None
            else allocation_records
        ),
        relations=(),
        sources=("unit-test:nav",),
        complete=True,
        profile_snapshot=_profile(asset.asset_id),
        errors={},
    )


def _profile(asset_id):
    return OpenFundProfileSnapshot(
        fund_asset_id=asset_id,
        effective_date="2026-06-11",
        announcement_date="2026-06-12",
        fund_name="测试基金",
        share_class_name="测试基金A",
        share_class_code="163417",
        manager_name="测试基金公司",
        custodian_name="测试银行",
        fund_managers=(),
        benchmark_text="测试指数收益率×95%+银行活期存款利率(税后)×5%",
        management_fee_rate=Decimal("0.01"),
        custody_fee_rate=Decimal("0.002"),
        sales_service_fee_rate=Decimal("0"),
        source_document_id="profile-doc",
        source_title="测试基金产品资料概要",
        source_url="https://example.test/profile.pdf",
        source="official-unit-test",
        parser_version="unit-test",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
    )


def _definition(asset_id):
    return CompositeBenchmarkDefinition(
        asset_id=asset_id,
        effective_date="2026-06-11",
        source="official-unit-test",
        source_document_id="profile-doc",
        benchmark_text=(
            "测试指数收益率×95%+银行活期存款利率(税后)×5%"
        ),
        components=(
            BenchmarkComponent(
                label="测试指数",
                kind=BenchmarkComponentKind.INDEX,
                weight=Decimal("0.95"),
                source="https://example.test/index",
                index_asset_id="index:test",
            ),
            BenchmarkComponent(
                label="银行活期存款利率(税后)",
                kind=BenchmarkComponentKind.CASH_RATE,
                weight=Decimal("0.05"),
                source="https://example.test/rate",
                annual_rate=Decimal("0.0035"),
                rate_effective_date="2015-10-24",
            ),
        ),
    )


def _risk(asset_id):
    series = []
    for value_date, value in (
        ("2026-08-20", 0.0),
        ("2026-08-21", 0.01),
    ):
        for name in (
            "cumulative_return",
            "drawdown_series",
            "rolling_annualized_volatility_5d",
            "rolling_sharpe_5d",
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


def _factor(asset_id, name, value, value_date="2026-08-21"):
    return FactorValue(
        asset_id=asset_id,
        factor_name=name,
        as_of_date=value_date,
        value=value,
        calculation_version="unit-v1",
        parameters={"window": "input"},
        input_start_date="2026-08-20",
        input_end_date=value_date,
        source_dataset="open_fund_nav",
        computed_at="2026-08-22T00:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
