import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.factor_engineering import FactorValue
from src.fundamental_analysis import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    FundamentalSnapshot,
    MetricStatus,
)
from src.master_data import ASSET_TYPE_STOCK, Asset
from src.research_visualization import (
    ChartDataPoint,
    ChartRenderer,
    ChartSpec,
    ChartType,
    ReportArtifactService,
    build_factor_series_spec,
    build_financial_trends_spec,
    build_price_volume_spec,
    build_valuation_history_spec,
    chart_types_for_profile,
)
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)


class ResearchVisualizationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name) / "research"
        self.renderer = ChartRenderer(self.output_root)
        self.asset = Asset(
            asset_id="asset:test",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600519",
            display_name="Maotai",
            market="cn_a",
            exchange="sh",
        )
        self.task = ResearchTaskSpec(
            task_id="research:test/chart",
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-01-10",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_price_chart_outputs_same_source_artifacts(self):
        records = _price_records(11)
        spec = build_price_volume_spec(
            task=self.task,
            asset=self.asset,
            records=records,
            source_dataset="stock_daily_bars",
            price_adjustment="none",
            moving_averages=(3, 5),
        )
        first = self.renderer.render(spec)
        first_metadata = first.metadata_path.read_text(
            encoding="utf-8"
        )
        second = self.renderer.render(spec)

        self.assertEqual(first, second)
        self.assertEqual(
            first_metadata,
            second.metadata_path.read_text(encoding="utf-8"),
        )
        self.assertGreater(first.svg_path.stat().st_size, 100)
        self.assertGreater(first.png_path.stat().st_size, 100)
        data = json.loads(
            first.data_path.read_text(encoding="utf-8")
        )
        metadata = json.loads(first_metadata)
        self.assertEqual(len(data), 10)
        self.assertEqual(metadata["as_of_date"], "2026-01-10")
        self.assertEqual(metadata["price_adjustment"], "none")
        self.assertEqual(metadata["point_count"], 10)

    def test_report_service_publishes_markdown_and_auditable_charts(self):
        service = ReportArtifactService(
            self.output_root / "report"
        )
        spec = build_price_volume_spec(
            task=self.task,
            asset=self.asset,
            records=_price_records(11),
            source_dataset="stock_daily_bars",
            price_adjustment="none",
            moving_averages=(3, 5),
        )

        charts = service.render_charts(
            "maotai-report",
            {"price": spec},
        )
        alias_path = service.publish_chart_alias(
            charts[0],
            filename="茅台价格图.png",
        )
        published = service.publish_markdown(
            filename="茅台报告.md",
            content=(
                "# 茅台报告\n\n"
                f"![价格图]({charts[0].markdown_path})"
            ),
            charts=charts,
        )

        self.assertEqual(
            published.report_path.parent,
            self.output_root / "report",
        )
        self.assertTrue(published.report_path.exists())
        self.assertTrue(charts[0].artifact.png_path.exists())
        self.assertTrue(charts[0].artifact.metadata_path.exists())
        self.assertEqual(
            alias_path.parent,
            self.output_root / "report",
        )
        self.assertEqual(
            alias_path.read_bytes(),
            charts[0].artifact.png_path.read_bytes(),
        )
        self.assertIn(
            charts[0].markdown_path,
            published.report_path.read_text(encoding="utf-8"),
        )

        escaped = replace(
            charts[0],
            markdown_path="assets/D:escape.png",
        )
        with self.assertRaisesRegex(ValueError, "relative PNG"):
            service.publish_markdown(
                filename="安全报告.md",
                content="# 安全报告",
                charts=(escaped,),
            )
        with self.assertRaisesRegex(ValueError, "filename"):
            service.publish_markdown(
                filename="D:escape.md",
                content="# 安全报告",
            )
        with self.assertRaisesRegex(
            ValueError,
            "reviewed publication",
        ):
            service.publish_markdown(
                filename="未复查-完整决策报告-2026-01-10.md",
                content="# 未复查报告",
            )

    def test_all_chart_types_render(self):
        factor_values = _factor_values()
        specs = [
            build_factor_series_spec(
                task=self.task,
                asset=self.asset,
                chart_type=ChartType.CUMULATIVE_RETURN,
                values=factor_values,
                factor_names=("cumulative_return",),
                source_dataset="risk_daily_v1",
            ),
            build_factor_series_spec(
                task=self.task,
                asset=self.asset,
                chart_type=ChartType.DRAWDOWN,
                values=factor_values,
                factor_names=("drawdown_series",),
                source_dataset="risk_daily_v1",
            ),
            build_factor_series_spec(
                task=self.task,
                asset=self.asset,
                chart_type=ChartType.ROLLING_RISK,
                values=factor_values,
                factor_names=(
                    "rolling_annualized_volatility_5d",
                    "rolling_sharpe_5d",
                ),
                source_dataset="risk_daily_v1",
            ),
            build_valuation_history_spec(
                task=self.task,
                asset=self.asset,
                results=_fundamental_results(),
            ),
            build_financial_trends_spec(
                task=self.task,
                asset=self.asset,
                snapshots=_fundamental_snapshots(),
                metric_results=_fundamental_results(),
            ),
        ]
        specs.insert(
            0,
            build_price_volume_spec(
                task=self.task,
                asset=self.asset,
                records=_price_records(10),
                source_dataset="stock_daily_bars",
                price_adjustment="none",
            ),
        )
        specs.append(
            ChartSpec(
                task_id=self.task.task_id,
                asset_id=self.asset.asset_id,
                asset_name=self.asset.display_name,
                chart_type=ChartType.CATEGORICAL_BAR,
                title="Scenario returns",
                as_of_date=self.task.as_of_date,
                x_label="Scenario",
                y_label="Return",
                points=(
                    ChartDataPoint(
                        "2026-01-01",
                        {"Strategy": 0.1, "Benchmark": 0.2},
                    ),
                    ChartDataPoint(
                        "2026-01-02",
                        {"Strategy": -0.1, "Benchmark": 0.0},
                    ),
                ),
                source_datasets=("unit-test",),
                unit="ratio",
                parameters={
                    "category_labels": ["Risk-on", "Risk-off"],
                },
            )
        )
        artifacts = [self.renderer.render(spec) for spec in specs]
        valuation_spec = next(
            spec
            for spec in specs
            if spec.chart_type is ChartType.VALUATION_HISTORY
        )
        self.assertEqual(
            valuation_spec.parameters["history_statistics"][
                "pe_ttm"
            ]["observation_count"],
            1,
        )
        cumulative_spec = next(
            spec
            for spec in specs
            if spec.chart_type is ChartType.CUMULATIVE_RETURN
        )
        self.assertIn("2026-01-01 to 2026-01-10", cumulative_spec.title)
        self.assertEqual(
            cumulative_spec.parameters["input_start_date"],
            "2026-01-01",
        )

        self.assertEqual(
            {artifact.chart_type for artifact in artifacts},
            set(ChartType),
        )
        self.assertTrue(
            all(artifact.svg_path.exists() for artifact in artifacts)
        )

    def test_cumulative_return_supports_benchmark_overlay(self):
        values = []
        for value_date, fund_value, benchmark_value in (
            ("2026-01-01", 0.01, 0.02),
            ("2026-01-02", 0.03, 0.025),
        ):
            for name, value in (
                ("asset_cumulative_return", fund_value),
                ("benchmark_cumulative_return", benchmark_value),
            ):
                values.append(
                    FactorValue(
                        asset_id=self.asset.asset_id,
                        factor_name=name,
                        as_of_date=value_date,
                        value=value,
                        calculation_version="benchmark-v1",
                        parameters={},
                        input_start_date="2025-12-31",
                        input_end_date=value_date,
                        source_dataset=(
                            "open_fund_nav|composite_benchmark"
                        ),
                        computed_at="2026-01-03T00:00:00+00:00",
                    )
                )

        spec = build_factor_series_spec(
            task=self.task,
            asset=self.asset,
            chart_type=ChartType.CUMULATIVE_RETURN,
            values=values,
            factor_names=(
                "asset_cumulative_return",
                "benchmark_cumulative_return",
            ),
            source_dataset="open_fund_nav|composite_benchmark",
            benchmark_asset_id="composite:fund:test",
        )

        self.assertEqual(
            set(spec.points[0].values),
            {
                "asset_cumulative_return",
                "benchmark_cumulative_return",
            },
        )
        self.assertEqual(
            spec.benchmark_asset_id,
            "composite:fund:test",
        )

    def test_chart_spec_rejects_future_and_duplicate_dates(self):
        with self.assertRaisesRegex(
            ValueError,
            "must not exceed as_of_date",
        ):
            _simple_spec(
                self.task,
                (
                    ChartDataPoint(
                        date="2026-01-11",
                        values={"drawdown": -0.1},
                    ),
                ),
            )
        duplicate = ChartDataPoint(
            date="2026-01-01",
            values={"drawdown": -0.1},
        )
        with self.assertRaisesRegex(
            ValueError,
            "duplicate dates",
        ):
            _simple_spec(self.task, (duplicate, duplicate))

    def test_missing_required_candle_field_fails_render(self):
        spec = build_price_volume_spec(
            task=self.task,
            asset=self.asset,
            records=[
                {
                    "trade_date": "2026-01-01",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": None,
                }
            ],
            source_dataset="stock_daily_bars",
            price_adjustment="none",
        )
        with self.assertRaisesRegex(
            ValueError,
            "requires fields: volume",
        ):
            self.renderer.render(spec)

    def test_asset_profiles_select_different_chart_sets(self):
        ordinary = chart_types_for_profile(
            PROFILE_ORDINARY_COMPANY
        )
        etf = chart_types_for_profile(PROFILE_EXCHANGE_FUND)
        open_fund = chart_types_for_profile(PROFILE_OPEN_FUND)

        self.assertEqual(
            set(ordinary),
            set(ChartType) - {ChartType.CATEGORICAL_BAR},
        )
        self.assertNotIn(ChartType.FINANCIAL_TRENDS, etf)
        self.assertNotIn(ChartType.PRICE_VOLUME, open_fund)


def _simple_spec(task, points):
    return ChartSpec(
        task_id=task.task_id,
        asset_id=task.asset_id,
        asset_name="Test",
        chart_type=ChartType.DRAWDOWN,
        title="Drawdown",
        as_of_date=task.as_of_date,
        x_label="Date",
        y_label="Drawdown",
        points=points,
        source_datasets=("risk_daily_v1",),
        unit="ratio",
    )


def _price_records(count):
    return [
        {
            "trade_date": f"2026-01-{index:02d}",
            "open": 10 + index * 0.1,
            "high": 10.5 + index * 0.1,
            "low": 9.5 + index * 0.1,
            "close": 10.2 + index * 0.1,
            "volume": 1000 + index * 10,
        }
        for index in range(1, count + 1)
    ]


def _factor_values():
    values = []
    for index in range(1, 11):
        trade_date = f"2026-01-{index:02d}"
        for name, value in (
            ("cumulative_return", index * 0.01),
            ("drawdown_series", -index * 0.005),
            (
                "rolling_annualized_volatility_5d",
                0.2 + index * 0.001,
            ),
            ("rolling_sharpe_5d", 1.0 + index * 0.01),
        ):
            values.append(
                FactorValue(
                    asset_id="asset:test",
                    factor_name=name,
                    as_of_date=trade_date,
                    value=value,
                    calculation_version="risk_daily_v1",
                    parameters={"window": 5},
                    input_start_date="2026-01-01",
                    input_end_date=trade_date,
                    source_dataset="stock_daily_bars",
                    computed_at="2026-01-10T00:00:00+00:00",
                )
            )
    return values


def _fundamental_results():
    results = []
    for year, pe, pb, ps, roe, roic in (
        (2024, "20", "5", "8", "0.25", "0.3"),
        (2025, "18", "4.5", "7", "0.27", "0.32"),
    ):
        for name, value in (
            ("pe_ttm", pe),
            ("pb", pb),
            ("ps_ttm", ps),
            ("roe", roe),
            ("roic", roic),
        ):
            results.append(
                FundamentalMetricResult(
                    asset_id="asset:test",
                    metric_name=name,
                    as_of_date=f"{year + 1}-03-31",
                    report_period=f"{year}-12-31",
                    status=MetricStatus.AVAILABLE,
                    value=Decimal(value),
                    unit=(
                        "ratio"
                        if name in {"roe", "roic"}
                        else "multiple"
                    ),
                    formula="test",
                    numerator=Decimal(value),
                    denominator=Decimal("1"),
                    reason=None,
                    calculation_version=(
                        FUNDAMENTAL_CALCULATION_VERSION
                    ),
                    parameters={},
                    inputs=(),
                    computed_at="2026-03-31T00:00:00+00:00",
                )
            )
    return results


def _fundamental_snapshots():
    return [
        FundamentalSnapshot(
            asset_id="asset:test",
            as_of_date=f"{year + 1}-03-31",
            report_period=f"{year}-12-31",
            currency="CNY",
            market_price=Decimal("10"),
            shares_outstanding=Decimal("100"),
            fields={
                "revenue": Decimal(revenue),
                "earnings": Decimal(earnings),
                "operating_cash_flow": Decimal(ocf),
                "free_cash_flow": Decimal(fcf),
                "gross_margin": Decimal(margin),
            },
            prior_fields={},
        )
        for year, revenue, earnings, ocf, fcf, margin in (
            (2024, "800", "80", "120", "100", "0.4"),
            (2025, "1000", "100", "180", "130", "0.42"),
        )
    ]


if __name__ == "__main__":
    unittest.main()
