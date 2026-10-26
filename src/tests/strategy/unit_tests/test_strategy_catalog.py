import unittest
from decimal import Decimal

from src.factor_engineering import FactorValue
from src.fundamental_analysis import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    MetricStatus,
)
from src.master_data import ASSET_TYPE_STOCK, Asset
from src.research_evidence import (
    EvidenceTier,
    ResearchEvidenceBundle,
    ResearchEvidenceItem,
)
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
)
from src.research_workflow import default_profile_registry
from src.strategy import (
    StrategyExecutor,
    StrategyInputBuilder,
    StrategyMetricInput,
    StrategyRunStatus,
    default_strategy_registry,
    default_strategy_specs,
    exchange_fund_strategy_specs,
    execute_horizon_catalog,
    financial_institution_strategy_specs,
)


class StrategyCatalogTest(unittest.TestCase):
    def setUp(self):
        self.asset = Asset(
            asset_id="asset:test",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600519",
            display_name="贵州茅台",
            market="cn_a",
            exchange="sh",
        )
        self.builder = StrategyInputBuilder()
        self.executor = StrategyExecutor()
        self.specs = {
            spec.horizons[0]: spec
            for spec in default_strategy_specs()
        }

    def test_strategy_metrics_do_not_conflict_with_profile_contracts(
        self,
    ):
        profiles = default_profile_registry()
        for spec in default_strategy_registry().list_specs():
            for profile_id in spec.supported_profiles:
                profile = profiles.require(profile_id)
                overlap = set(spec.required_metrics) & set(
                    profile.forbidden_metrics
                )
                self.assertFalse(
                    overlap,
                    (
                        f"{spec.strategy_id} requires forbidden "
                        f"metrics {sorted(overlap)} for {profile_id}"
                    ),
                )

    def test_input_builder_combines_factors_fundamentals_and_evidence(
        self,
    ):
        strategy_input = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-03-31",
            factor_values=_factor_values(),
            fundamental_results=_fundamental_results(),
            evidence_bundle=_evidence_bundle(self.asset),
        )

        self.assertIn("max_drawdown", strategy_input.metrics)
        self.assertIn("roic", strategy_input.metrics)
        self.assertIn(
            "financial_statement",
            strategy_input.evidence_types,
        )
        self.assertIn(
            "research_event",
            strategy_input.evidence_types,
        )

    def test_short_and_medium_long_rules_can_disagree(self):
        short_input = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.SHORT,
            as_of_date="2026-03-31",
            factor_values=_factor_values(
                momentum="-0.05",
                daily_return="-0.02",
                excess="-0.03",
            ),
            fundamental_results=_fundamental_results(),
        )
        medium_long_input = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-03-31",
            factor_values=_factor_values(),
            fundamental_results=_fundamental_results(),
            evidence_bundle=_evidence_bundle(self.asset),
        )
        report = execute_horizon_catalog(
            inputs={
                ResearchHorizon.SHORT: short_input,
                ResearchHorizon.MEDIUM_LONG: medium_long_input,
            }
        )
        statuses = {
            result.horizon: result.status
            for result in report.results
        }

        self.assertEqual(
            statuses[ResearchHorizon.SHORT],
            StrategyRunStatus.FAIL,
        )
        self.assertEqual(
            statuses[ResearchHorizon.MEDIUM_LONG],
            StrategyRunStatus.PASS,
        )
        self.assertIsNone(report.aggregate_score)
        self.assertTrue(
            all(result.input_effective_dates for result in report.results)
        )
        self.assertEqual(len(report.conflicts), 1)
        self.assertIn(
            "Do not aggregate scores",
            report.conflicts[0],
        )

    def test_catalyst_requires_event_evidence(self):
        without_event = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.MEDIUM_SHORT,
            as_of_date="2026-03-31",
            factor_values=_factor_values(),
            fundamental_results=_fundamental_results(),
        )
        result = self.executor.execute(
            self.specs[ResearchHorizon.MEDIUM_SHORT],
            without_event,
        )

        self.assertEqual(
            result.status,
            StrategyRunStatus.INSUFFICIENT_DATA,
        )
        self.assertEqual(
            result.missing_evidence_types,
            ("research_event",),
        )

    def test_long_rule_requires_qualitative_evidence(self):
        partial = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-03-31",
            factor_values=_factor_values(),
            fundamental_results=_fundamental_results(),
            evidence_bundle=_evidence_bundle(self.asset),
        )
        insufficient = self.executor.execute(
            self.specs[ResearchHorizon.LONG],
            partial,
        )
        complete = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-03-31",
            factor_values=_factor_values(),
            fundamental_results=_fundamental_results(),
            evidence_bundle=_evidence_bundle(self.asset),
            extra_evidence_types=(
                "business_model_evidence",
                "management_evidence",
            ),
        )
        evaluated = self.executor.execute(
            self.specs[ResearchHorizon.LONG],
            complete,
        )

        self.assertEqual(
            insufficient.status,
            StrategyRunStatus.INSUFFICIENT_DATA,
        )
        self.assertEqual(evaluated.status, StrategyRunStatus.PASS)
        self.assertEqual(
            evaluated.signal,
            "thesis_evidence_ready",
        )

    def test_strategy_input_builder_rejects_ambiguous_factor_windows(
        self,
    ):
        duplicated = [
            _factor("max_drawdown", "-0.1", {"window": 20}),
            _factor("max_drawdown", "-0.2", {"window": 60}),
        ]
        with self.assertRaisesRegex(
            ValueError,
            "Ambiguous factor parameters",
        ):
            self.builder.build(
                asset_id=self.asset.asset_id,
                profile_id=PROFILE_EXCHANGE_FUND,
                horizon=ResearchHorizon.SHORT,
                as_of_date="2026-03-31",
                factor_values=duplicated,
            )
        resolved = self.builder.build(
            asset_id=self.asset.asset_id,
            profile_id=PROFILE_EXCHANGE_FUND,
            horizon=ResearchHorizon.SHORT,
            as_of_date="2026-03-31",
            factor_values=duplicated,
            factor_parameter_filters={
                "max_drawdown": {"window": 60}
            },
        )
        self.assertEqual(
            resolved.metrics["max_drawdown"].value,
            Decimal("-0.2"),
        )

    def test_medium_long_etf_strategy_uses_tracking_and_costs(self):
        strategy_input = self.builder.build(
            asset_id="asset:etf",
            profile_id=PROFILE_EXCHANGE_FUND,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-03-31",
            factor_values=[
                _factor("tracking_error", "0.03"),
                _factor("premium_discount", "0.001"),
                _factor("expense_ratio", "0.002"),
                _factor("max_drawdown", "-0.12"),
                _factor("excess_total_return", "0.01"),
            ],
        )
        result = self.executor.execute(
            exchange_fund_strategy_specs()[0],
            strategy_input,
        )

        self.assertEqual(result.status, StrategyRunStatus.PASS)
        self.assertEqual(
            result.signal,
            "etf_tracking_research_supportive",
        )
        self.assertIn(
            "不判断标的资产方向",
            " ".join(result.limitations),
        )

    def test_medium_long_bank_strategy_uses_bank_metrics(self):
        strategy_input = self.builder.build(
            asset_id="asset:bank",
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-03-31",
            factor_values=[
                _factor("pb", "0.8"),
                _factor("roe", "0.10"),
                _factor("capital_adequacy", "0.13"),
                _factor("net_interest_margin", "0.018"),
                _factor(
                    "non_performing_loan_ratio",
                    "0.01",
                ),
                _factor("max_drawdown", "-0.12"),
            ],
            extra_evidence_types=("financial_statement",),
        )
        result = self.executor.execute(
            financial_institution_strategy_specs()[0],
            strategy_input,
        )

        self.assertEqual(result.status, StrategyRunStatus.PASS)
        self.assertNotIn(
            "fcf_yield",
            financial_institution_strategy_specs()[
                0
            ].required_metrics,
        )


def _factor_values(
    *,
    momentum="0.08",
    daily_return="0.01",
    excess="0.05",
):
    return [
        _factor("momentum_5", momentum),
        _factor("return_1d", daily_return),
        _factor("annualized_volatility", "0.25"),
        _factor("max_drawdown", "-0.12"),
        _factor("excess_total_return", excess),
    ]


def _factor(name, value, parameters=None):
    return FactorValue(
        asset_id="asset:test",
        factor_name=name,
        as_of_date="2026-03-31",
        value=float(value),
        calculation_version="unit-factor-v1",
        parameters=parameters or {"window": "input"},
        input_start_date="2025-01-01",
        input_end_date="2026-03-31",
        source_dataset="unit-test",
        computed_at="2026-03-31T00:00:00+00:00",
    )


def _fundamental_results():
    values = {
        "pe_ttm": "20",
        "roe": "0.25",
        "roic": "0.22",
        "fcf_yield": "0.06",
        "debt_to_assets": "0.3",
        "revenue_growth": "0.08",
    }
    return [
        FundamentalMetricResult(
            asset_id="asset:test",
            metric_name=name,
            as_of_date="2026-03-31",
            report_period="2025-12-31",
            status=MetricStatus.AVAILABLE,
            value=Decimal(value),
            unit=(
                "multiple" if name == "pe_ttm" else "ratio"
            ),
            formula="unit-test",
            numerator=Decimal(value),
            denominator=Decimal("1"),
            reason=None,
            calculation_version=FUNDAMENTAL_CALCULATION_VERSION,
            parameters={"currency": "CNY"},
            inputs=(),
            computed_at="2026-03-31T00:00:00+00:00",
        )
        for name, value in values.items()
    ]


def _evidence_bundle(asset):
    return ResearchEvidenceBundle(
        asset=asset,
        start_date="2025-01-01",
        end_date="2026-03-31",
        as_of_date="2026-03-31",
        evidence=(
            ResearchEvidenceItem(
                evidence_id="evidence:disclosure:annual",
                asset_id=asset.asset_id,
                evidence_type="disclosure",
                tier=EvidenceTier.OFFICIAL,
                title="2025年年度报告",
                event_date="2026-03-20",
                source="cninfo",
                source_record_id="doc:annual",
                source_url="https://example.com/annual.pdf",
                content="2025年年度报告",
                metadata={"category": "annual_report"},
            ),
            ResearchEvidenceItem(
                evidence_id="evidence:event:annual",
                asset_id=asset.asset_id,
                evidence_type="research_event",
                tier=EvidenceTier.DERIVED,
                title="年度报告事件",
                event_date="2026-03-20",
                source="cninfo",
                source_record_id="doc:annual",
                source_url="https://example.com/annual.pdf",
                content="年度报告",
                metadata={"event_type": "financial_report"},
            ),
        ),
        document_states=(),
        events=(),
        sources=("cninfo",),
        complete=True,
    )


if __name__ == "__main__":
    unittest.main()
