import io
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from datetime import datetime
from pathlib import Path

from src.master_data import ASSET_TYPE_STOCK, Asset
from src.portfolio_risk.weights import HoldingWeights, WeightItem
from src.user_space import UserContext
from src.research_contracts import (
    Personalization,
    ResearchIntent,
    ResearchRequest,
    SubjectType,
)
from src.research_orchestration import create_default_container
from src.research_orchestration.cli import main
from src.research_routing import RoutingStatus, StepOutcomeStatus


class UserContextCliTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.container = create_default_container(
            root / "context.sqlite3",
            root / "report",
        )
        self.container.master_data.register_asset(
            Asset(
                asset_id="stock:000001",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="000001",
                display_name="平安银行",
                market="cn_a",
                exchange="sz",
            ),
            source="unit-test",
            raw_symbol="000001",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *arguments):
        output = io.StringIO()
        code = main(
            list(arguments),
            container_factory=lambda **_: self.container,
            stdout=output,
        )
        return code, json.loads(output.getvalue())

    def test_recorded_holding_satisfies_only_its_own_asset(self):
        reader = self.container.user_context
        self.assertFalse(reader.has_positions("user:default", None))
        self.assertFalse(reader.has_positions("user:unknown", None))

        code, recorded = self._run(
            "record-holding",
            "--user-id",
            "user:default",
            "--asset-id",
            "stock:000001",
            "--quantity",
            "1000",
            "--average-cost",
            "12.5",
        )

        self.assertEqual(code, 0)
        self.assertEqual(recorded["holdings"][0]["asset_id"], "stock:000001")
        self.assertTrue(reader.has_positions("user:default", "stock:000001"))
        self.assertFalse(reader.has_positions("user:default", "stock:600519"))
        code, listed = self._run("user-context")
        self.assertEqual(code, 0)
        (user,) = listed["users"]
        self.assertEqual(user["user"]["user_id"], "user:default")
        self.assertEqual(user["mandates"], [])

    def test_confirmed_meaning_becomes_a_new_preference_version_for_q5(self):
        self._run(
            "record-holding", "--user-id", "user:default", "--asset-id", "stock:000001", "--quantity", "1000"
        )
        base = ["record-preference", "--user-id", "user:default", "--as-of-date", "2026-10-28"]
        code, preview = self._run(
            *base, "--profile", "balanced", "--horizon", "1_3y", "--statement", "亏两成能接受", "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertFalse(preview["recorded"])
        self.assertIsNone(self.container.user_context.describe("user:default")["preference"])
        code, first = self._run(*base, "--profile", "balanced", "--horizon", "1_3y", "--statement", "亏两成能接受")
        code, second = self._run(*base, "--meaning", "higher_equity", "--statement", "想更激进，多放股票")

        self.assertEqual(code, 0)
        self.assertEqual(second["preference"]["version"], 2)
        self.assertIn("equity_weight_high", {item["field"] for item in second["changes"]})
        described = self.container.user_context.describe("user:default")
        self.assertEqual(described["preference"]["preference_id"], second["personal_ref"])
        self.assertEqual([item["version"] for item in described["preference_history"]], [1, 2])

        request = ResearchRequest(
            request_id="q5",
            as_of_date="2026-10-28",
            subject_type=SubjectType.PORTFOLIO,
            subject_refs=(),
            intents=(ResearchIntent.EVALUATE, ResearchIntent.PLAN),
            personalization=Personalization.REQUIRED,
            user_id="user:default",
        )
        unconfirmed = self.container.recipe_executor.run(
            replace(request, personal_refs=(first["personal_ref"],))
        )
        self.assertEqual(unconfirmed.routing.status, RoutingStatus.NEEDS_USER_CONTEXT)
        # Diagnosis and allocation fetch prices remotely; this test only covers the preference step.
        self.container.recipe_executor.portfolio_diagnosis = None
        self.container.recipe_executor.allocation = None
        self.container.recipe_executor.adjustment = None
        run = self.container.recipe_executor.run(replace(request, personal_refs=(second["personal_ref"],)))
        outcome = next(item for item in run.step_outcomes if item.step_id == "confirm_preference")
        self.assertEqual(outcome.status, StepOutcomeStatus.COMPLETED)
        self.assertIn("权益比例上限", outcome.summary)

    def test_weight_only_context_is_a_position_without_invented_quantities(self):
        self.container.portfolio_risk.record_weights(UserContext(user_id="user:default"), HoldingWeights(
            user_id="user:default", as_of_date="2026-01-01", user_statement="Stock and cash shares only",
            items=(WeightItem(label="Bank", weight=Decimal("0.2"), asset_id="stock:000001"),
                   WeightItem(label="Cash", weight=Decimal("0.8"), asset_class="cash")),
        ))
        reader = self.container.user_context
        payload = reader.describe("user:default", as_of_date="2026-01-02")
        self.assertEqual(payload["holdings_basis"], "weight")
        self.assertEqual(payload["holdings"][0]["symbol"], "000001")
        self.assertTrue(all(item["quantity"] is None for item in payload["holdings"]))
        self.assertIsNone(payload["holdings"][1]["asset_id"])
        self.assertTrue(reader.has_positions("user:default", "stock:000001"))
        self.assertFalse(reader.has_positions("user:default", "stock:other"))
        self.assertEqual(reader.describe("user:default", as_of_date="2025-12-31")["holdings"], [])

    def test_context_includes_scoped_confirmations_without_future_leakage(self):
        registry = self.container.execution_plans.registry
        registry.clock = lambda: datetime.fromisoformat("2026-01-03T10:00:00+00:00")
        self.container.execution_plans.register(user_id="user:default", registration_key="scoped", plan={
            "title": "Scoped rules", "objective": "Keep the exception local", "status": "active",
            "reason": "User confirmed", "source_refs": ["confirmation:test"],
            "constraints": ["This index is reviewed instead of sold by the generic stop"],
            "tasks": [{"task_id": "check", "title": "Check", "instruction": "Inspect", "completion": "Evidence",
                       "schedule": {"start": "2026-01-03"}}],
        })
        current = self.container.user_context.describe("user:default", as_of_date="2026-01-03")
        self.assertIn("generic stop", current["execution_context"]["plans"][0]["constraints"][0])
        self.assertIsNone(current["preference"])
        past = self.container.user_context.describe("user:default", as_of_date="2026-01-02")
        self.assertEqual(past["execution_context"]["plans"], [])


if __name__ == "__main__":
    unittest.main()
