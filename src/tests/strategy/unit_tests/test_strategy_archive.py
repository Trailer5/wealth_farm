import unittest

from src.research_contracts import ResearchHorizon
from src.strategy import (
    ForwardRecord,
    LifecycleState,
    StrategyArchive,
    ValidationRecord,
    default_archives,
    transition_blockers,
)

SOURCE = "docs/evidence/strategy_sources/x.md"


def _archive(**overrides):
    payload = {
        "strategy_id": "ma_timing",
        "version": "v1",
        "name": "均线择时",
        "kind": "timing",
        "mechanism": "收盘价在 200 日均线之上持有，否则空仓",
        "universe": "沪深300",
        "horizons": ["medium_short"],
        "parameters": {"window": 200},
        "source_refs": [SOURCE],
        "failure_conditions": ["样本外连续两段跑输买入持有"],
        "limitations": [],
        **overrides,
    }
    return StrategyArchive.from_dict(payload)


def _blockers(current, target, archive=None, **kwargs):
    return transition_blockers(
        archive or _archive(), current, target, as_of_date="2026-10-29", sources_found=(SOURCE,), **kwargs
    )


class StrategyArchiveModelTest(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        archive = _archive()
        self.assertEqual(StrategyArchive.from_dict(archive.to_dict()), archive)
        self.assertEqual(archive.family, "ma_timing")
        self.assertEqual(archive.parameters, {"window": "200"})
        self.assertEqual(archive.horizons, (ResearchHorizon.MEDIUM_SHORT,))

    def test_a_strategy_needs_a_retirement_trigger_and_a_known_kind(self):
        with self.assertRaises(ValueError):
            _archive(failure_conditions=[])
        with self.assertRaises(ValueError):
            _archive(kind="magic")

    def test_built_in_archives_cover_the_six_research_rules(self):
        archives = {item.strategy_id: item for item in default_archives()}
        self.assertEqual(
            set(archives),
            {
                "trend_timing",
                "catalyst_repricing",
                "quality_value",
                "business_thesis_check",
                "etf_dual_momentum_trend",
                "sse_market_turning_points",
            },
        )
        self.assertEqual(archives["etf_dual_momentum_trend"].parameters["top_n"], "2")
        self.assertIn("区间回撤不超过 20%", archives["trend_timing"].mechanism)


class TransitionGateTest(unittest.TestCase):
    def test_states_move_one_step_at_a_time(self):
        self.assertEqual(_blockers(LifecycleState.RESEARCH, LifecycleState.VALIDATING), [])
        self.assertTrue(_blockers(LifecycleState.RESEARCH, LifecycleState.FORWARD_TRACKING))
        self.assertTrue(_blockers(LifecycleState.VALIDATING, LifecycleState.RESEARCH))

    def test_validating_needs_existing_sources(self):
        self.assertIn("没有来源登记", _blockers(LifecycleState.RESEARCH, LifecycleState.VALIDATING,
                                               archive=_archive(source_refs=[]))[0])
        blockers = transition_blockers(
            _archive(), LifecycleState.RESEARCH, LifecycleState.VALIDATING, as_of_date="2026-10-29"
        )
        self.assertIn("不存在", blockers[0])

    def test_forward_tracking_needs_a_complete_validation(self):
        self.assertEqual(
            _blockers(LifecycleState.VALIDATING, LifecycleState.FORWARD_TRACKING),
            ["没有完整验证记录（strategy-evaluate）"],
        )
        partial = ValidationRecord("eval:1", "2026-10-01", True, False, True, False)
        blockers = _blockers(LifecycleState.VALIDATING, LifecycleState.FORWARD_TRACKING, validation=partial)
        self.assertEqual(blockers, ["缺参数邻域结果", "样本外表现劣于简单基准"])

    def test_enabling_needs_six_months_and_twelve_settled_signals(self):
        young = ForwardRecord("2026-06-01", 8, True, True)
        blockers = _blockers(LifecycleState.FORWARD_TRACKING, LifecycleState.ENABLED, forward=young)
        self.assertEqual(len(blockers), 2)
        mature = ForwardRecord("2026-04-29", 12, True, True)
        self.assertEqual(_blockers(LifecycleState.FORWARD_TRACKING, LifecycleState.ENABLED, forward=mature), [])
        unknown = _blockers(
            LifecycleState.FORWARD_TRACKING, LifecycleState.ENABLED, forward=ForwardRecord("2026-04-29", 0, None, None)
        )
        self.assertIn("暂无已结算信号，命中率尚无法判断", unknown)
        self.assertNotIn("超额收益劣于基准", unknown)

    def test_any_state_can_retire_with_a_reason_and_retired_is_final(self):
        self.assertTrue(_blockers(LifecycleState.ENABLED, LifecycleState.RETIRED))
        self.assertEqual(_blockers(LifecycleState.ENABLED, LifecycleState.RETIRED, reason="失效条件 1 触发"), [])
        self.assertTrue(_blockers(LifecycleState.RETIRED, LifecycleState.RESEARCH, reason="x"))


if __name__ == "__main__":
    unittest.main()
