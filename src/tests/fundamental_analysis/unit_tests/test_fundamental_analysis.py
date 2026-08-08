import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.financial_data import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FinancialResearchBundle,
)
from src.fundamental_analysis import (
    FINANCIAL_INSTITUTION_VERSION,
    FinancialInstitutionAnalysisService,
    FundamentalAnalysisService,
    FundamentalCalculationError,
    FundamentalMetricCalculator,
    FundamentalMetricParameters,
    FundamentalSnapshotAssembler,
    FundamentalSnapshotError,
    MetricStatus,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class FundamentalAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "fundamental.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master_data.register_asset(
            Asset(
                asset_id="asset:company",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="600519",
        )
        self.etf = self.master_data.register_asset(
            Asset(
                asset_id="asset:etf",
                asset_type=ASSET_TYPE_EXCHANGE_FUND,
                standard_symbol="518880",
                display_name="黄金ETF",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="518880",
        )
        self.repository = SQLiteFundamentalMetricRepository(
            self.db_path
        )
        self.service = FundamentalAnalysisService(
            master_data=self.master_data,
            repository=self.repository,
        )
        self.bundle = _annual_bundle(self.asset)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_financial_institution_metrics_use_reported_fields(self):
        fields = {
            "book_value_per_share": ("20", "CNY_per_share"),
            "roe": ("0.10", "ratio"),
            "capital_adequacy": ("0.1346", "ratio"),
            "net_interest_margin": ("0.0179", "ratio"),
            "non_performing_loan_ratio": ("0.0105", "ratio"),
        }
        results = FinancialInstitutionAnalysisService(
            master_data=self.master_data,
            repository=self.repository,
        ).analyze(
            asset_id=self.asset.asset_id,
            as_of_date="2026-08-03",
            market_price=Decimal("10"),
            indicator_records=[
                {
                    "field_name": name,
                    "report_period": "2026-03-31",
                    "published_at": "2026-04-25",
                    "normalized_value": value,
                    "unit": unit,
                    "currency": (
                        "CNY" if unit == "CNY_per_share" else None
                    ),
                    "source": "akshare:financial_indicators",
                }
                for name, (value, unit) in fields.items()
            ],
        )
        by_name = {
            result.metric_name: result for result in results
        }

        self.assertEqual(
            set(by_name),
            {
                "pb",
                "roe",
                "capital_adequacy",
                "net_interest_margin",
                "non_performing_loan_ratio",
            },
        )
        self.assertEqual(by_name["pb"].value, Decimal("0.5"))
        self.assertEqual(by_name["roe"].value, Decimal("0.10"))
        self.assertEqual(
            by_name["capital_adequacy"].value,
            Decimal("0.1346"),
        )
        self.assertEqual(
            by_name["net_interest_margin"].value,
            Decimal("0.0179"),
        )
        self.assertEqual(
            by_name["non_performing_loan_ratio"].value,
            Decimal("0.0105"),
        )
        self.assertTrue(
            all(
                result.calculation_version
                == FINANCIAL_INSTITUTION_VERSION
                for result in results
            )
        )
        self.assertEqual(
            len(
                self.repository.list_results(
                    self.asset.asset_id,
                    as_of_date="2026-08-03",
                )
            ),
            5,
        )

    def test_financial_institution_enforces_cutoff_period_and_units(self):
        old_records = _bank_records(
            report_period="2025-12-31",
            published_at="2026-03-31",
        )
        future_records = _bank_records(
            report_period="2026-03-31",
            published_at="2026-06-01",
        )
        service = FinancialInstitutionAnalysisService(
            master_data=self.master_data,
            repository=self.repository,
        )

        results = service.analyze(
            asset_id=self.asset.asset_id,
            as_of_date="2026-05-01",
            market_price=Decimal("10"),
            indicator_records=old_records + future_records,
        )

        self.assertTrue(
            all(
                result.report_period == "2025-12-31"
                for result in results
            )
        )
        invalid_units = [dict(record) for record in old_records]
        next(
            record
            for record in invalid_units
            if record["field_name"] == "roe"
        )["unit"] = "percent"
        with self.assertRaisesRegex(ValueError, "roe requires unit ratio"):
            service.analyze(
                asset_id=self.asset.asset_id,
                as_of_date="2026-05-01",
                market_price=Decimal("10"),
                indicator_records=invalid_units,
            )

    def test_ordinary_company_metrics_are_exact_and_traceable(self):
        result = self.service.analyze(
            self.bundle,
            market_price=Decimal("15"),
        )
        metrics = result.by_name()

        self.assertEqual(
            set(metrics),
            {
                "market_cap",
                "enterprise_value",
                "pe_ttm",
                "pb",
                "ps_ttm",
                "pcf_ttm",
                "ev_ebitda",
                "ev_fcf",
                "earnings_yield",
                "fcf_yield",
                "dividend_yield",
                "roe",
                "roa",
                "roic",
                "gross_margin",
                "operating_margin",
                "net_margin",
                "revenue_growth",
                "net_profit_growth",
                "operating_cash_flow_growth",
                "free_cash_flow_growth",
                "debt_to_assets",
                "net_debt",
                "interest_coverage",
            },
        )
        self.assertEqual(metrics["market_cap"].value, Decimal("1500"))
        self.assertEqual(metrics["enterprise_value"].value, Decimal("1500"))
        self.assertEqual(metrics["pe_ttm"].value, Decimal("15"))
        self.assertEqual(metrics["pb"].value, Decimal("1.25"))
        self.assertEqual(metrics["ps_ttm"].value, Decimal("1.5"))
        self.assertEqual(
            metrics["pcf_ttm"].value,
            Decimal("1500") / Decimal("180"),
        )
        self.assertEqual(metrics["ev_ebitda"].value, Decimal("7.5"))
        self.assertEqual(
            metrics["ev_fcf"].value,
            Decimal("1500") / Decimal("130"),
        )
        self.assertEqual(
            metrics["earnings_yield"].value,
            Decimal("100") / Decimal("1500"),
        )
        self.assertEqual(
            metrics["fcf_yield"].value,
            Decimal("130") / Decimal("1500"),
        )
        self.assertEqual(
            metrics["dividend_yield"].value,
            Decimal("0.02"),
        )
        self.assertEqual(
            metrics["gross_margin"].value,
            Decimal("0.4"),
        )
        self.assertEqual(
            metrics["operating_margin"].value,
            Decimal("0.15"),
        )
        self.assertEqual(
            metrics["net_margin"].value,
            Decimal("0.1"),
        )
        self.assertEqual(
            metrics["revenue_growth"].value,
            Decimal("0.25"),
        )
        self.assertEqual(
            metrics["net_profit_growth"].value,
            Decimal("0.25"),
        )
        self.assertAlmostEqual(
            float(metrics["operating_cash_flow_growth"].value),
            40 / 140,
        )
        self.assertEqual(
            metrics["debt_to_assets"].value,
            Decimal("0.4"),
        )
        self.assertEqual(
            metrics["free_cash_flow_growth"].value,
            Decimal("0.3"),
        )
        self.assertEqual(metrics["net_debt"].value, Decimal("0"))
        self.assertAlmostEqual(
            float(metrics["roe"].value),
            100 / 1100,
        )
        self.assertAlmostEqual(
            float(metrics["roa"].value),
            100 / 1900,
        )
        self.assertAlmostEqual(
            float(metrics["roic"].value),
            120 / 1150,
        )
        self.assertEqual(
            metrics["interest_coverage"].status,
            MetricStatus.MISSING_INPUT,
        )
        self.assertGreater(len(metrics["pe_ttm"].inputs), 2)
        for metric_name in ("ev_ebitda", "ev_fcf", "fcf_yield"):
            input_names = {
                item.field_name
                for item in metrics[metric_name].inputs
            }
            self.assertIn("market_price", input_names)
            self.assertIn("shares_outstanding", input_names)
        self.assertEqual(
            metrics["pe_ttm"].status,
            MetricStatus.AVAILABLE,
        )

    def test_snapshot_ignores_records_published_after_as_of_date(self):
        income_records = list(
            self.bundle.records_by_dataset[
                DATASET_STOCK_INCOME_STATEMENTS
            ]
        )
        current_revenue = next(
            record
            for record in income_records
            if record["report_period"] == "2025-12-31"
            and record["field_name"] == "revenue"
        )
        income_records.append(
            {
                **current_revenue,
                "normalized_value": "9999",
                "published_at": "2026-04-01",
                "revision": 2,
            }
        )
        records_by_dataset = dict(self.bundle.records_by_dataset)
        records_by_dataset[
            DATASET_STOCK_INCOME_STATEMENTS
        ] = tuple(income_records)

        snapshot = FundamentalSnapshotAssembler().assemble(
            replace(
                self.bundle,
                records_by_dataset=records_by_dataset,
            ),
            market_price=Decimal("15"),
        )

        self.assertEqual(snapshot.fields["revenue"], Decimal("1000"))

    def test_negative_earnings_make_pe_not_applicable(self):
        snapshot = FundamentalSnapshotAssembler().assemble(
            self.bundle,
            market_price=Decimal("15"),
        )
        fields = dict(snapshot.fields)
        fields["earnings"] = Decimal("-10")
        metric_set = FundamentalMetricCalculator().calculate(
            replace(snapshot, fields=fields)
        )
        metrics = metric_set.by_name()

        self.assertEqual(
            metrics["pe_ttm"].status,
            MetricStatus.NOT_APPLICABLE,
        )
        self.assertIsNone(metrics["pe_ttm"].value)
        self.assertEqual(
            metrics["earnings_yield"].value,
            Decimal("-10") / Decimal("1500"),
        )

    def test_missing_shares_propagates_clear_status(self):
        bundle = _annual_bundle(
            self.asset,
            include_shares=False,
        )
        result = self.service.analyze(
            bundle,
            market_price=Decimal("15"),
        )
        metrics = result.by_name()

        self.assertEqual(
            metrics["market_cap"].status,
            MetricStatus.MISSING_INPUT,
        )
        self.assertEqual(
            metrics["pe_ttm"].status,
            MetricStatus.MISSING_INPUT,
        )

    def test_market_cap_cross_check_fails_outside_tolerance(self):
        with self.assertRaisesRegex(
            FundamentalCalculationError,
            "differs from reported",
        ):
            self.service.analyze(
                self.bundle,
                market_price=Decimal("15"),
                parameters=FundamentalMetricParameters(
                    reported_market_cap=Decimal("2000"),
                    market_cap_tolerance=Decimal("0.01"),
                ),
            )

    def test_results_persist_exact_values_and_statuses(self):
        first = self.service.analyze(
            self.bundle,
            market_price=Decimal("15"),
        )
        second = self.service.analyze(
            self.bundle,
            market_price=Decimal("15"),
        )
        stored = self.service.list_results(
            self.asset.asset_id,
            as_of_date="2026-03-31",
        )

        self.assertEqual(len(first.metrics), len(second.metrics))
        self.assertEqual(len(stored), len(first.metrics))
        pe = next(
            metric
            for metric in stored
            if metric.metric_name == "pe_ttm"
        )
        self.assertEqual(pe.value, Decimal("15"))
        self.assertEqual(pe.numerator, Decimal("1500"))
        self.assertEqual(pe.denominator, Decimal("100"))
        self.assertEqual(pe.parameters["currency"], "CNY")
        self.assertEqual(pe.parameters["flow_basis"], "annual")

    def test_history_filters_publications_at_each_cutoff(self):
        summary = self.bundle.period_summary()
        history = self.service.analyze_history(
            self.bundle,
            market_prices_by_cutoff={
                "2025-03-31": Decimal("10"),
                "2026-03-31": Decimal("15"),
            },
            shares_override=Decimal("100"),
        )

        self.assertEqual(
            summary.latest_annual_period,
            "2025-12-31",
        )
        self.assertIsNone(summary.latest_interim_period)
        self.assertEqual(
            [item.snapshot.report_period for item in history],
            ["2024-12-31", "2025-12-31"],
        )
        self.assertTrue(
            all(
                item.snapshot.flow_basis == "annual"
                for item in history
            )
        )
        self.assertEqual(
            len(
                self.repository.list_results(
                    self.asset.asset_id,
                    metric_name="pe_ttm",
                )
            ),
            2,
        )

    def test_ttm_snapshot_uses_three_explicit_periods(self):
        bundle = _ttm_bundle(self.asset)
        snapshot = FundamentalSnapshotAssembler().assemble(
            bundle,
            market_price=Decimal("10"),
            shares_override=Decimal("100"),
        )

        self.assertEqual(snapshot.report_period, "2025-03-31")
        self.assertEqual(snapshot.flow_basis, "ttm")
        self.assertEqual(snapshot.fields["revenue"], Decimal("1100"))
        periods = {
            item.report_period
            for item in snapshot.evidence["revenue"]
        }
        self.assertEqual(
            periods,
            {"2024-03-31", "2024-12-31", "2025-03-31"},
        )

    def test_currency_mismatch_is_rejected(self):
        records = {
            dataset: tuple(
                {
                    **record,
                    "currency": (
                        "USD"
                        if record["unit"] == "CNY"
                        else record.get("currency")
                    ),
                }
                for record in rows
            )
            for dataset, rows in self.bundle.records_by_dataset.items()
        }
        with self.assertRaisesRegex(
            FundamentalSnapshotError,
            "do not match market currency",
        ):
            FundamentalSnapshotAssembler().assemble(
                replace(
                    self.bundle,
                    records_by_dataset=records,
                ),
                market_price=Decimal("15"),
                currency="CNY",
            )

    def test_etf_is_rejected_by_ordinary_company_service(self):
        with self.assertRaisesRegex(
            ValueError,
            "does not support asset_type",
        ):
            self.service.analyze(
                replace(self.bundle, asset=self.etf),
                market_price=Decimal("15"),
            )


def _bank_records(*, report_period, published_at):
    fields = {
        "book_value_per_share": ("20", "CNY_per_share"),
        "roe": ("0.10", "ratio"),
        "capital_adequacy": ("0.1346", "ratio"),
        "net_interest_margin": ("0.0179", "ratio"),
        "non_performing_loan_ratio": ("0.0105", "ratio"),
    }
    return [
        {
            "field_name": name,
            "report_period": report_period,
            "published_at": published_at,
            "normalized_value": value,
            "unit": unit,
            "currency": (
                "CNY" if unit == "CNY_per_share" else None
            ),
            "source": "unit-test:bank",
            "revision": 1,
        }
        for name, (value, unit) in fields.items()
    ]


def _annual_bundle(asset, include_shares=True):
    current = "2025-12-31"
    prior = "2024-12-31"
    income = []
    for period, values in (
        (
            prior,
            {
                "revenue": "800",
                "earnings": "80",
                "operating_profit": "120",
                "ebit": "120",
                "ebitda": "160",
                "pretax_profit": "110",
                "income_tax": "22",
            },
        ),
        (
            current,
            {
                "revenue": "1000",
                "earnings": "100",
                "operating_profit": "150",
                "ebit": "150",
                "ebitda": "200",
                "pretax_profit": "140",
                "income_tax": "28",
                "operating_cost": "600",
            },
        ),
    ):
        income.extend(
            _points(
                DATASET_STOCK_INCOME_STATEMENTS,
                period,
                values,
            )
        )
    cash = []
    for period, values in (
        (
            prior,
            {
                "operating_cash_flow": "140",
                "capital_expenditure": "40",
                "annual_dividends": "20",
            },
        ),
        (
            current,
            {
                "operating_cash_flow": "180",
                "capital_expenditure": "50",
                "annual_dividends": "30",
            },
        ),
    ):
        cash.extend(
            _points(
                DATASET_STOCK_CASH_FLOW_STATEMENTS,
                period,
                values,
            )
        )
    balance_values = {
        prior: {
            "total_assets": "1800",
            "total_liabilities": "800",
            "total_equity": "1000",
            "cash_and_equivalents": "250",
            "short_term_debt": "150",
            "long_term_debt": "200",
        },
        current: {
            "total_assets": "2000",
            "total_liabilities": "800",
            "total_equity": "1200",
            "cash_and_equivalents": "300",
            "short_term_debt": "100",
            "long_term_debt": "200",
            **(
                {"shares_outstanding": "100"}
                if include_shares
                else {}
            ),
        },
    }
    balance = [
        point
        for period, values in balance_values.items()
        for point in _points(
            DATASET_STOCK_BALANCE_SHEETS,
            period,
            values,
        )
    ]
    return FinancialResearchBundle(
        asset=asset,
        as_of_date="2026-03-31",
        start_date="2024-01-01",
        end_date="2025-12-31",
        records_by_dataset={
            DATASET_STOCK_INCOME_STATEMENTS: tuple(income),
            DATASET_STOCK_CASH_FLOW_STATEMENTS: tuple(cash),
            DATASET_STOCK_BALANCE_SHEETS: tuple(balance),
            DATASET_STOCK_FINANCIAL_INDICATORS: (),
        },
        sources=("unit-test",),
        complete=True,
    )


def _ttm_bundle(asset):
    periods = {
        "2023-03-31": "100",
        "2023-12-31": "800",
        "2024-03-31": "200",
        "2024-12-31": "1000",
        "2025-03-31": "300",
    }
    income = [
        point
        for period, value in periods.items()
        for point in _points(
            DATASET_STOCK_INCOME_STATEMENTS,
            period,
            {"revenue": value},
        )
    ]
    return FinancialResearchBundle(
        asset=asset,
        as_of_date="2025-05-01",
        start_date="2023-01-01",
        end_date="2025-03-31",
        records_by_dataset={
            DATASET_STOCK_INCOME_STATEMENTS: tuple(income),
            DATASET_STOCK_CASH_FLOW_STATEMENTS: (),
            DATASET_STOCK_BALANCE_SHEETS: (),
            DATASET_STOCK_FINANCIAL_INDICATORS: (),
        },
        sources=("unit-test",),
        complete=True,
    )


def _points(dataset, period, values):
    return [
        {
            "asset_id": "asset:company",
            "dataset": dataset,
            "report_period": period,
            "published_at": (
                (
                    f"{int(period[:4]) + 1}-03-31"
                    if period.endswith("12-31")
                    else f"{period[:4]}-04-30"
                )
            ),
            "period_kind": (
                "annual"
                if period.endswith("12-31")
                else "q1"
            ),
            "value_scope": (
                "point_in_time"
                if dataset == DATASET_STOCK_BALANCE_SHEETS
                else "cumulative"
            ),
            "field_name": field_name,
            "field_label": field_name,
            "normalized_value": value,
            "raw_value": value,
            "currency": (
                None
                if field_name == "shares_outstanding"
                else "CNY"
            ),
            "unit": (
                "shares"
                if field_name == "shares_outstanding"
                else "CNY"
            ),
            "source": "unit-test",
            "revision": 1,
            "fetched_at": "2026-03-31T00:00:00+00:00",
            "raw": {},
        }
        for field_name, value in values.items()
    ]


if __name__ == "__main__":
    unittest.main()
