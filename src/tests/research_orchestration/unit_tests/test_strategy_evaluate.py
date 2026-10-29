import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.backtest.strategy_validation import neighbourhood_verdict, rolling_segments, sharpe_difference_z
from src.backtest.validation_evidence import assess_evidence
from src.research_ledger import ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.strategy_archive import StrategyArchiveService
from src.research_orchestration.strategy_backtest import StrategyBacktestService
from src.research_orchestration.strategy_evaluate import StrategyEvaluateService, _variants
from src.strategy import LifecycleState, SQLiteStrategyArchiveRepository
from src.strategy import templates
from src.strategy.signals import SIGNAL_GENERATORS, SignalGenerator

DAYS = [(date(2015, 1, 1) + timedelta(days=i)).isoformat() for i in range(2800)]


def _level(i):
    return 1.0005 ** i * (1 + 0.05 * math.sin(i / 15))


class _DataAccess:
    def get(self, request):
        rows = [
            {"trade_date": day, "open": _level(i), "close": _level(i), "preclose": _level(i), "amount": 1e8, "volume": 1}
            for i, day in enumerate(DAYS)
            if request.start_date <= day <= request.end_date
        ]
        return SimpleNamespace(records=rows, error=None)


class _Master:
    def find_assets(self, *, standard_symbol, asset_type):
        return [SimpleNamespace(asset_id=f"asset:{standard_symbol}", display_name=standard_symbol)]


class _Fetch:
    store = SimpleNamespace(get_exchange_listings=lambda code: [])


class _Macro:
    def get(self, series_id, **kwargs):
        return SimpleNamespace(
            to_dict=lambda limit=None: {"points": [{"period": "2010-01-01", "value": 2.0}]}, points=()
        )


def _spike(series, calendar, parameters, context):
    """Holds the asset only for k == 7: an artificially overfitted parameter."""

    (asset_id, _), = series.items()
    return [(calendar[0], {asset_id: 1.0} if parameters["k"] == "7" else {}, "spike")]


ARCHIVE = {
    "strategy_id": "spiky",
    "version": "v1",
    "name": "过拟合示例",
    "kind": "timing",
    "mechanism": "只在 k=7 时持有",
    "universe": "510300",
    "horizons": ["medium_short"],
    "parameters": {"k": "7"},
    "source_refs": ["docs/evidence/strategy_sources/spike.md"],
    "failure_conditions": ["跑输买入持有"],
    "limitations": [],
    "template_id": "spike_test",
    "universe_assets": ["510300:exchange_fund"],
}


class StrategyValidationHelpersTest(unittest.TestCase):
    def test_method_passes_the_source_gate(self):
        self.assertTrue(assess_evidence().implementation_ready, assess_evidence())

    def test_expanding_segments_leave_a_gap_and_merge_a_short_tail(self):
        # Seven and a half years: two full test segments and a half-year tail merged into the second.
        segments = rolling_segments(DAYS[:2757], train_years=3, test_years=2, gap_days=21)
        self.assertEqual(len(segments), 2)
        train_end, test_start, test_end = segments[0]
        self.assertEqual(DAYS.index(test_start) - DAYS.index(train_end), 22)
        self.assertEqual(segments[1][0], test_end)
        self.assertEqual(segments[-1][2], DAYS[2756])

    def test_neighbourhood_verdict(self):
        rng = random.Random(3)
        noise = [rng.gauss(0, 0.01) for _ in range(1000)]
        base = [0.001 + e for e in noise]
        close = [0.00099 + e + rng.gauss(0, 0.001) for e in noise]
        much_worse = [-0.0005 + e + rng.gauss(0, 0.001) for e in noise]
        self.assertTrue(neighbourhood_verdict(base, {"a": much_worse, "b": [0.0] * 1000, "c": close})["sensitive"])
        stable = neighbourhood_verdict(base, {"a": close, "b": [0.0011 + e for e in noise], "c": much_worse})
        self.assertFalse(stable["sensitive"])
        self.assertEqual(stable["worse"], ["c"])
        self.assertEqual(sharpe_difference_z(base, base), 0.0)

    def test_variants_respect_bounds(self):
        self.assertEqual(_variants({"window": "10"}, {"window": 20}), [("window", "30")])
        self.assertEqual(_variants({"min_profit_growth": "0"}, {"min_profit_growth": 5}),
                         [("min_profit_growth", "-5"), ("min_profit_growth", "5")])
        self.assertEqual(
            _variants({"buy_below": "0.3", "sell_above": "0.35"}, {"buy_below": 0.05, "sell_above": 0.05}),
            [("buy_below", "0.25"), ("sell_above", "0.4")],
        )


class StrategyEvaluateServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "docs/evidence/strategy_sources").mkdir(parents=True)
        (root / "docs/evidence/strategy_sources/spike.md").write_text("x", encoding="utf-8")
        db = root / "db.sqlite3"
        repository = SQLiteStrategyArchiveRepository(db)
        self.archives = StrategyArchiveService(
            repository=repository, ledger=ResearchLedgerService(SQLiteResearchLedgerRepository(db)), project_root=root
        )
        backtest = StrategyBacktestService(
            archives=self.archives, repository=repository, data_access=_DataAccess(), data_fetch=_Fetch(),
            master_data=_Master(), macro_data=_Macro(),
        )
        self.service = StrategyEvaluateService(backtest=backtest, archives=self.archives)
        self.archives.register(ARCHIVE, "2022-02-01")
        self.archives.transition("spiky", LifecycleState.VALIDATING, as_of_date="2022-02-01", reason="来源已登记")
        patches = [
            mock.patch.dict(SIGNAL_GENERATORS, {"spike_test": SignalGenerator("spike_test", "spike", lambda p: 0, _spike)}),
            mock.patch.dict(templates.NEIGHBOURHOOD_STEPS, {"spike_test": {"k": 1}}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_overfitted_parameter_is_flagged_and_validation_feeds_the_gate(self):
        before = self.archives.show("spiky", "2022-02-01")["next_blockers"]
        self.assertIn("没有完整验证记录（strategy-evaluate）", before)
        result = self.service.evaluate("spiky", as_of_date="2022-02-01", start_date="2015-01-01", end_date=DAYS[-1])
        self.assertTrue(result["neighbourhood"]["sensitive"])
        self.assertEqual(len(result["neighbourhood"]["results"]), 2)
        self.assertGreaterEqual(len(result["rolling_oos"]["segments"]), 2)
        self.assertTrue(result["rolling_oos"]["not_worse"])
        self.assertEqual(result["rolling_oos"]["benchmark"], "buy_and_hold")
        self.assertTrue(all(result["validation"].values()))
        self.assertEqual(result["next_state"], "forward_tracking")
        self.assertEqual(result["next_blockers"], [])
        self.assertEqual(result["full_sample"]["significance"]["trials"], 3)
        trend = {row["state"]: row for row in result["regimes"]["dimensions"]["trend"]}
        self.assertEqual(result["regimes"]["version"], "csi300-v1")
        self.assertTrue(trend)
        self.assertEqual(result["regimes"]["dimensions"]["valuation"], [])


if __name__ == "__main__":
    unittest.main()
