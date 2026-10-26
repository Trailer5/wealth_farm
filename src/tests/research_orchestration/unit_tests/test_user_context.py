import io
import json
import tempfile
import unittest
from pathlib import Path

from src.master_data import ASSET_TYPE_STOCK, Asset
from src.research_orchestration import create_default_container
from src.research_orchestration.cli import main


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


if __name__ == "__main__":
    unittest.main()
