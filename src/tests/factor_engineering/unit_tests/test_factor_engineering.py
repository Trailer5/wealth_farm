import math
import tempfile
import unittest
from pathlib import Path

from src.factor_engineering import (
    FactorService,
    MarketFactorCalculator,
    SQLiteFactorRepository,
)
from src.master_data import MasterDataService, SQLiteMasterDataRepository


class MarketFactorCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = MarketFactorCalculator()

    def test_baseline_factor_formulas(self):
        closes = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0]
        values = self.calculator.calculate(
            asset_id="asset:test",
            records=_records(closes),
            source_dataset="stock_daily_bars",
        )
        by_name = {value.factor_name: value.value for value in values}
        returns = [
            closes[index] / closes[index - 1] - 1.0
            for index in range(1, len(closes))
        ]
        mean = sum(returns) / len(returns)
        expected_volatility = (
            math.sqrt(
                sum((value - mean) ** 2 for value in returns)
                / len(returns)
            )
            * math.sqrt(252.0)
        )

        self.assertAlmostEqual(by_name["return_1d"], 14.0 / 13.0 - 1.0)
        self.assertAlmostEqual(by_name["sma_5"], 12.2)
        self.assertAlmostEqual(by_name["momentum_5"], 0.4)
        self.assertAlmostEqual(by_name["volatility_5"], expected_volatility)
        self.assertAlmostEqual(by_name["drawdown"], 0.0)

    def test_insufficient_windows_are_skipped(self):
        values = self.calculator.calculate(
            asset_id="asset:test",
            records=_records([10.0]),
            source_dataset="stock_daily_bars",
        )

        self.assertEqual(
            [value.factor_name for value in values],
            ["drawdown"],
        )

    def test_drawdown_uses_input_peak(self):
        values = self.calculator.calculate(
            asset_id="asset:test",
            records=_records([10.0, 12.0, 9.0]),
            source_dataset="stock_daily_bars",
        )
        by_name = {value.factor_name: value.value for value in values}

        self.assertAlmostEqual(by_name["drawdown"], -0.25)

    def test_duplicate_trade_date_is_rejected(self):
        records = _records([10.0, 11.0])
        records.append(dict(records[-1]))

        with self.assertRaises(ValueError):
            self.calculator.calculate(
                asset_id="asset:test",
                records=records,
                source_dataset="stock_daily_bars",
            )


class FactorServiceTest(unittest.TestCase):
    def test_values_are_persisted_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "factors.sqlite3"
            master = MasterDataService(SQLiteMasterDataRepository(db_path))
            asset = master.resolve_identifier(
                source="unit-test",
                raw_symbol="518880",
                name="黄金ETF",
                asset_type_hint="ETF",
            ).asset
            service = FactorService(
                SQLiteFactorRepository(db_path),
                master,
            )

            first = service.compute_and_store(
                asset_id=asset.asset_id,
                records=_records([10, 11, 12, 11, 13, 14]),
                source_dataset="fund_exchange_daily_bars",
            )
            second = service.compute_and_store(
                asset_id=asset.asset_id,
                records=_records([10, 11, 12, 11, 13, 14]),
                source_dataset="fund_exchange_daily_bars",
            )
            stored = service.list_values(asset.asset_id)

            self.assertEqual(len(first), 5)
            self.assertEqual(len(second), 5)
            self.assertEqual(len(stored), 5)
            self.assertEqual(
                service.list_values(
                    asset.asset_id,
                    factor_name="sma_5",
                )[0].parameters,
                {"window": 5},
            )


def _records(closes):
    return [
        {
            "symbol": "TEST",
            "trade_date": f"2026-07-{index:02d}",
            "close": close,
            "source": "unit-test",
        }
        for index, close in enumerate(closes, start=1)
    ]


if __name__ == "__main__":
    unittest.main()
