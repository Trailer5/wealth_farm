import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import (
    AggressivenessMeaning,
    InvestmentPreference,
    PortfolioRiskService,
    PreferenceHorizon,
    RiskProfile,
    SQLitePortfolioRepository,
    derive_preference,
    effective_cash_floor,
    initial_preference,
    mandate_for_asset,
    preference_changes,
)
from src.research_state import ResearchStateService, SQLiteResearchStateRepository
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


def _balanced(user_id: str = "user:a") -> InvestmentPreference:
    return initial_preference(
        user_id=user_id,
        recorded_on="2026-10-28",
        risk_profile=RiskProfile.BALANCED,
        horizon=PreferenceHorizon.ONE_TO_3Y,
        user_statement="能接受亏两成左右，打算放两三年",
    )


class PreferenceDerivationTest(unittest.TestCase):
    def test_initial_version_uses_preset_and_overrides(self):
        preference = initial_preference(
            user_id="user:a",
            recorded_on="2026-10-28",
            risk_profile=RiskProfile.BALANCED,
            horizon=PreferenceHorizon.ONE_TO_3Y,
            user_statement="最多亏 25%",
            overrides={"max_drawdown": "0.25"},
        )

        self.assertEqual(preference.version, 1)
        self.assertEqual(preference.equity_weight_high, Decimal("0.60"))
        self.assertEqual(preference.max_drawdown, Decimal("0.25"))
        self.assertEqual(preference.overrides, ("max_drawdown",))
        self.assertTrue(preference.preference_id.startswith("preference:"))
        self.assertEqual(InvestmentPreference.from_dict(preference.to_dict()), preference)

    def test_higher_equity_moves_equity_and_risk_budget_only(self):
        base = _balanced()
        derived = derive_preference(
            base,
            meanings=(AggressivenessMeaning.HIGHER_EQUITY,),
            recorded_on="2026-10-29",
            user_statement="想更激进一点，多放股票",
        )

        self.assertEqual(derived.version, 2)
        self.assertEqual(derived.supersedes_preference_id, base.preference_id)
        self.assertEqual(
            (derived.equity_weight_low, derived.equity_weight_high),
            (Decimal("0.60"), Decimal("0.80")),
        )
        self.assertEqual(derived.target_volatility, Decimal("0.15"))
        self.assertEqual(derived.max_drawdown, Decimal("0.30"))
        self.assertEqual(derived.max_single_weight, base.max_single_weight)
        self.assertEqual(derived.horizon, base.horizon)
        self.assertEqual(derived.risk_profile, RiskProfile.GROWTH)
        changed = {item["field"] for item in preference_changes(base, derived)}
        self.assertEqual(
            changed,
            {"equity_weight_low", "equity_weight_high", "target_volatility", "max_drawdown"},
        )

    def test_meaning_keeps_values_the_user_stated_earlier(self):
        base = initial_preference(
            user_id="user:a",
            recorded_on="2026-10-28",
            risk_profile=RiskProfile.BALANCED,
            horizon=PreferenceHorizon.OVER_3Y,
            user_statement="最多亏两成",
            overrides={"max_drawdown": "0.20"},
        )
        derived = derive_preference(
            base,
            meanings=(AggressivenessMeaning.HIGHER_EQUITY,),
            recorded_on="2026-10-29",
            user_statement="多放股票",
        )

        self.assertEqual(derived.max_drawdown, Decimal("0.20"))
        self.assertEqual(derived.target_volatility, Decimal("0.15"))
        self.assertEqual(derived.overrides, ("max_drawdown",))
        self.assertTrue(any("保留用户此前说出的 20%" in item for item in derived.derivation))
        self.assertTrue(any(item.startswith("风险预算升一档（目标年化波动）") for item in derived.derivation))

    def test_cash_floor_covers_near_term_spending(self):
        preference = initial_preference(
            user_id="user:a",
            recorded_on="2026-10-28",
            risk_profile=RiskProfile.BALANCED,
            horizon=PreferenceHorizon.ONE_TO_3Y,
            user_statement="明年 4 月要用 3 万",
            liquidity_need_amount=Decimal("30000"),
            liquidity_need_by="2027-04-30",
        )

        floor, note = effective_cash_floor(preference, total_value=200000.0, as_of_date="2026-10-28")
        self.assertAlmostEqual(floor, 0.15)
        self.assertIn("30000", note)
        far, far_note = effective_cash_floor(preference, total_value=200000.0, as_of_date="2024-01-01")
        self.assertEqual((far, far_note), (0.05, None))

    def test_longer_horizon_does_not_raise_loss_tolerance(self):
        derived = derive_preference(
            _balanced(),
            meanings=(AggressivenessMeaning.LONGER_HORIZON,),
            recorded_on="2026-10-29",
            user_statement="可以拿更久",
        )

        self.assertEqual(derived.horizon, PreferenceHorizon.OVER_3Y)
        self.assertEqual(derived.max_drawdown, Decimal("0.20"))
        self.assertEqual(derived.target_volatility, Decimal("0.10"))

    def test_more_concentrated_raises_single_and_industry_caps(self):
        derived = derive_preference(
            _balanced(),
            meanings=(AggressivenessMeaning.MORE_CONCENTRATED,),
            recorded_on="2026-10-29",
            user_statement="可以集中一些",
        )

        self.assertEqual(derived.max_single_weight, Decimal("0.20"))
        self.assertEqual(derived.max_industry_weight, Decimal("0.35"))
        self.assertEqual(derived.equity_weight_high, Decimal("0.60"))

    def test_top_tier_is_kept_and_noted(self):
        aggressive = initial_preference(
            user_id="user:a",
            recorded_on="2026-10-28",
            risk_profile=RiskProfile.AGGRESSIVE,
            horizon=PreferenceHorizon.OVER_3Y,
            user_statement="进取",
        )
        derived = derive_preference(
            aggressive,
            meanings=(AggressivenessMeaning.HIGHER_EQUITY,),
            recorded_on="2026-10-29",
            user_statement="再激进些",
        )

        self.assertEqual(derived.equity_weight_high, aggressive.equity_weight_high)
        self.assertTrue(any("最高档" in item for item in derived.derivation))

    def test_first_version_cannot_carry_meanings(self):
        with self.assertRaises(ValueError):
            InvestmentPreference(
                user_id="user:a",
                version=1,
                recorded_on="2026-10-28",
                risk_profile=RiskProfile.BALANCED,
                horizon=PreferenceHorizon.ONE_TO_3Y,
                equity_weight_low=Decimal("0.3"),
                equity_weight_high=Decimal("0.6"),
                target_volatility=Decimal("0.1"),
                max_drawdown=Decimal("0.2"),
                max_single_weight=Decimal("0.15"),
                max_industry_weight=Decimal("0.25"),
                min_cash_weight=Decimal("0.05"),
                user_statement="更激进",
                meanings=(AggressivenessMeaning.HIGHER_EQUITY,),
            )

    def test_mandate_for_stock_uses_single_cap(self):
        preference = _balanced()
        stock = mandate_for_asset(
            preference,
            implementation_asset_id="asset:600519",
            benchmark_asset_id="asset:000300",
            single_stock=True,
        )
        fund = mandate_for_asset(
            preference,
            implementation_asset_id="asset:510300",
            benchmark_asset_id="asset:000300",
            single_stock=False,
        )

        self.assertEqual(stock.max_risk_weight, Decimal("0.15"))
        self.assertEqual(fund.max_risk_weight, Decimal("0.60"))
        self.assertEqual(stock.max_drawdown, Decimal("0.20"))
        self.assertNotEqual(stock.mandate_id, fund.mandate_id)


class PreferencePersistenceTest(unittest.TestCase):
    def test_versions_append_in_order_and_are_user_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "portfolio.sqlite3"
            users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
            context = users.default_context()
            master = MasterDataService(SQLiteMasterDataRepository(db_path))
            research = ResearchStateService(SQLiteResearchStateRepository(db_path), users, master)
            service = PortfolioRiskService(SQLitePortfolioRepository(db_path), research)

            first = _balanced(context.user_id)
            service.record_preference(context, first)
            service.record_preference(context, first)
            second = derive_preference(
                first,
                meanings=(AggressivenessMeaning.HIGHER_VOLATILITY,),
                recorded_on="2026-10-29",
                user_statement="波动大点没关系",
            )
            with self.assertRaises(ValueError):
                service.record_preference(
                    context,
                    derive_preference(
                        second,
                        meanings=(AggressivenessMeaning.HIGHER_EQUITY,),
                        recorded_on="2026-10-30",
                        user_statement="跳过了一个版本",
                    ),
                )
            service.record_preference(context, second)

            history = service.list_preferences(context)
            self.assertEqual([item.version for item in history], [1, 2])
            self.assertEqual(service.latest_preference(context), second)
            other = users.context_for_user(users.create_user("Other").user_id)
            self.assertEqual(service.list_preferences(other), [])


if __name__ == "__main__":
    unittest.main()
