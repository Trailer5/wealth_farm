import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.alerts import RULE_PRICE_ABOVE
from src.ai_harness import ModelOutput
from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
)
from src.data_src.models import DailyBar, DisclosureDocument
from src.web_api import create_app


class FakeModelAdapter:
    name = "web-api-fake"

    def generate(self, task, evidence):
        return ModelOutput(
            content="API 模型结果",
            citations=(evidence[0].evidence_id,),
        )


class LocalResearchApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            Path(self.temp_dir.name) / "web-api.sqlite3"
        )
        self.client = TestClient(self.app)
        self.services = self.app.state.services
        self.asset = self.services.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="518880",
            name="黄金ETF",
            asset_type_hint="ETF",
        ).asset

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_health_and_users(self):
        health = self.client.get("/health")
        created = self.client.post(
            "/users",
            json={"display_name": "Alice"},
        )
        users = self.client.get("/users")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["default_user_id"], "user:default")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(users.json()), 2)

    def test_research_dashboard_contains_primary_sections(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        for section_id in (
            'id="dashboard-watchlist"',
            'id="asset-search-form"',
            'id="asset-select"',
            'id="market-data-form"',
            'id="market-chart"',
            'id="watchlist-form"',
            'id="holding-form"',
            'id="report-form"',
            'id="valuation-form"',
            'id="price-rule-form"',
            'id="ai-task-form"',
            'id="quality-runs"',
        ):
            self.assertIn(section_id, response.text)
        styles = self.client.get("/static/styles.css")
        script = self.client.get("/static/app.js")
        self.assertEqual(styles.status_code, 200)
        self.assertEqual(script.status_code, 200)
        self.assertIn("/portfolio/valuations", script.text)
        self.assertIn("/alert-rules/price", script.text)
        self.assertIn("/data-quality/runs", script.text)
        self.assertIn("/daily-bars/query", script.text)
        self.assertIn("/factors/compute", script.text)

    def test_workbench_script_references_existing_unique_elements(self):
        html = self.client.get("/").text
        script = self.client.get("/static/app.js").text
        html_ids = re.findall(r'\bid="([^"]+)"', html)
        script_ids = set(re.findall(r'\$\("([^"]+)"\)', script))

        self.assertEqual(len(html_ids), len(set(html_ids)))
        self.assertEqual(script_ids - set(html_ids), set())

    def test_assets_can_be_searched_by_symbol_and_name(self):
        other_asset = self.services.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="600519",
            name="贵州茅台",
            asset_type_hint="股票",
        ).asset

        by_symbol = self.client.get(
            "/assets",
            params={"query": "5188"},
        )
        by_name = self.client.get(
            "/assets",
            params={"query": "茅台"},
        )
        limited = self.client.get(
            "/assets",
            params={"query": "", "limit": 1},
        )

        self.assertEqual(
            [item["asset_id"] for item in by_symbol.json()],
            [self.asset.asset_id],
        )
        self.assertEqual(by_name.json()[0]["asset_id"], other_asset.asset_id)
        self.assertEqual(len(limited.json()), 1)
        self.assertEqual(
            self.client.get("/assets", params={"limit": 101}).status_code,
            422,
        )

    def test_daily_bars_query_and_factor_compute_are_local_first(self):
        records = [
            DailyBar(
                symbol="518880",
                trade_date=f"2026-07-{day:02d}",
                open=8.0 + index * 0.1,
                high=8.2 + index * 0.1,
                low=7.9 + index * 0.1,
                close=8.1 + index * 0.1,
                volume=1000 + index * 100,
                amount=8100 + index * 900,
                source="unit:web",
            )
            for index, day in enumerate(range(20, 26))
        ]
        repository = self.services.data_access.repository
        repository.store.upsert_fund_exchange_daily_bars(records)
        repository.mark_coverage(
            asset_id=self.asset.asset_id,
            dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
            start_date="2026-07-20",
            end_date="2026-07-25",
            source="unit:web",
        )
        payload = {
            "dataset": DATASET_EXCHANGE_FUND_DAILY_BARS,
            "start_date": "2026-07-20",
            "end_date": "2026-07-25",
        }

        queried = self.client.post(
            f"/assets/{self.asset.asset_id}/daily-bars/query",
            json=payload,
        )
        computed = self.client.post(
            f"/assets/{self.asset.asset_id}/factors/compute",
            json=payload,
        )

        self.assertEqual(queried.status_code, 200)
        self.assertEqual(len(queried.json()["data"]["records"]), 6)
        self.assertTrue(queried.json()["data"]["complete"])
        self.assertFalse(queried.json()["data"]["fetched_remote"])
        self.assertTrue(queried.json()["quality"]["can_publish"])
        self.assertEqual(computed.status_code, 200)
        self.assertGreater(len(computed.json()["values"]), 0)
        mismatched = self.client.post(
            f"/assets/{self.asset.asset_id}/daily-bars/query",
            json={**payload, "dataset": DATASET_STOCK_DAILY_BARS},
        )
        self.assertEqual(mismatched.status_code, 400)

        empty_asset = self.services.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="000001",
            name="平安银行",
            asset_type_hint="股票",
        ).asset
        with patch.object(
            self.services.data_access.fetch_service,
            "fetch_and_store_a_share_daily_bars",
            side_effect=AssertionError("remote fetch must not run"),
        ):
            local_only = self.client.post(
                f"/assets/{empty_asset.asset_id}/daily-bars/query",
                json={
                    "dataset": DATASET_STOCK_DAILY_BARS,
                    "start_date": "2026-07-20",
                    "end_date": "2026-07-25",
                },
            )
        self.assertEqual(local_only.status_code, 200)
        self.assertFalse(local_only.json()["data"]["fetched_remote"])

    def test_watchlist_holding_and_report_flow(self):
        asset_id = self.asset.asset_id
        watchlist = self.client.put(
            f"/watchlist/{asset_id}",
            json={
                "reason": "观察黄金",
                "tags": ["黄金", "ETF"],
            },
        )
        holding = self.client.put(
            f"/holdings/{asset_id}",
            json={
                "quantity": 100,
                "average_cost": 8.1,
                "note": "本地 API 测试",
            },
        )
        report = self.client.post(
            "/reports",
            json={
                "asset_id": asset_id,
                "title": "黄金ETF观察",
                "summary": "测试",
                "content_markdown": "# 黄金ETF\n继续观察。",
                "status": "final",
            },
        )

        self.assertEqual(watchlist.status_code, 200)
        self.assertEqual(holding.json()["quantity"], 100)
        self.assertEqual(report.status_code, 201)
        self.assertEqual(len(self.client.get("/watchlist").json()), 1)
        self.assertEqual(len(self.client.get("/holdings").json()), 1)
        self.assertEqual(len(self.client.get("/reports").json()), 1)
        self.assertEqual(
            self.client.get(
                f"/reports/{report.json()['report_id']}"
            ).json()["title"],
            "黄金ETF观察",
        )

    def test_portfolio_alert_and_monitoring_workbench_flow(self):
        asset_id = self.asset.asset_id
        self.client.put(
            f"/holdings/{asset_id}",
            json={"quantity": 100, "average_cost": 8.0},
        )
        price_rule = self.client.post(
            "/alert-rules/price",
            json={
                "asset_id": asset_id,
                "rule_type": "price_above",
                "threshold": 8.2,
            },
        )
        event_rule = self.client.post(
            "/alert-rules/event",
            json={"event_type": "financial_report"},
        )
        weight_rule = self.client.post(
            "/alert-rules/max-weight",
            json={"threshold": 0.5},
        )
        observed = self.client.post(
            "/alerts/evaluate-price",
            json={
                "asset_id": asset_id,
                "price": 8.3,
                "observed_at": "2026-07-27T00:00:00+08:00",
            },
        )
        valuation = self.client.post(
            "/portfolio/valuations",
            json={
                "as_of_date": "2026-07-27",
                "prices": {asset_id: 8.3},
            },
        )
        run = self.services.data_quality.start_task(
            "web-monitoring-test",
            dataset="daily_bars",
            source="unit-test",
            asset_id=asset_id,
        )
        self.services.data_quality.complete_task(run.run_id, 1)
        runs = self.client.get("/data-quality/runs")
        detail = self.client.get(
            f"/data-quality/runs/{run.run_id}"
        )

        self.assertEqual(price_rule.status_code, 201)
        self.assertEqual(event_rule.status_code, 201)
        self.assertEqual(weight_rule.status_code, 201)
        self.assertEqual(len(self.client.get("/alert-rules").json()), 3)
        self.assertEqual(len(observed.json()), 1)
        self.assertEqual(valuation.status_code, 201)
        self.assertEqual(
            len(valuation.json()["generated_alerts"]),
            1,
        )
        self.assertEqual(len(runs.json()), 1)
        self.assertEqual(detail.json()["run"]["status"], "success")
        self.assertEqual(
            self.client.get("/master-data/unresolved").json(),
            [],
        )
        removed = self.client.delete(f"/holdings/{asset_id}")
        self.assertTrue(removed.json()["removed"])
        self.assertEqual(self.client.get("/holdings").json(), [])

    def test_user_header_selects_separate_context(self):
        user = self.client.post(
            "/users",
            json={"display_name": "Other"},
        ).json()
        self.client.put(
            f"/watchlist/{self.asset.asset_id}",
            json={"reason": "默认用户"},
        )

        other_watchlist = self.client.get(
            "/watchlist",
            headers={"X-User-Id": user["user_id"]},
        )

        self.assertEqual(other_watchlist.status_code, 200)
        self.assertEqual(other_watchlist.json(), [])

    def test_alerts_can_be_listed_and_acknowledged(self):
        context = self.services.user_space.default_context()
        self.services.alerts.create_price_rule(
            context,
            asset_id=self.asset.asset_id,
            rule_type=RULE_PRICE_ABOVE,
            threshold=8.0,
        )
        alert = self.services.alerts.evaluate_price(
            context,
            asset_id=self.asset.asset_id,
            price=8.385,
            observed_at="2026-07-24",
        )[0]

        listed = self.client.get("/alerts", params={"status": "pending"})
        acknowledged = self.client.post(
            f"/alerts/{alert.alert_id}/acknowledge"
        )

        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(
            acknowledged.json()["status"],
            "acknowledged",
        )

    def test_unknown_user_and_asset_return_clean_errors(self):
        missing_user = self.client.get(
            "/watchlist",
            headers={"X-User-Id": "user:missing"},
        )
        missing_asset = self.client.put(
            "/watchlist/asset:missing",
            json={"reason": "missing"},
        )

        self.assertEqual(missing_user.status_code, 404)
        self.assertEqual(missing_asset.status_code, 404)
        self.assertNotIn("traceback", missing_asset.text.lower())

    def test_factor_event_and_portfolio_queries(self):
        asset_id = self.asset.asset_id
        self.services.factors.compute_and_store(
            asset_id=asset_id,
            records=[
                {
                    "trade_date": f"2026-07-{day:02d}",
                    "close": close,
                }
                for day, close in zip(
                    range(19, 25),
                    [8.0, 8.1, 8.2, 8.3, 8.4, 8.385],
                )
            ],
            source_dataset="fund_exchange_daily_bars",
        )
        self.services.events.ingest_disclosure(
            asset_id,
            DisclosureDocument(
                document_id="event-doc-1",
                symbol="518880",
                title="黄金ETF2025年年度报告",
                announcement_date="2026-04-17",
                category="annual",
                source="cninfo",
                file_url="https://example.com/report.pdf",
            ),
        )
        context = self.services.user_space.default_context()
        self.services.research_state.upsert_holding(
            context,
            asset_id=asset_id,
            quantity=100,
            average_cost=8.1,
        )
        valuation = self.services.portfolio.value_current_holdings(
            context,
            as_of_date="2026-07-24",
            prices={asset_id: 8.385},
        )

        asset = self.client.get(f"/assets/{asset_id}")
        factors = self.client.get(f"/assets/{asset_id}/factors")
        events = self.client.get(f"/assets/{asset_id}/events")
        snapshots = self.client.get("/portfolio/snapshots")
        detail = self.client.get(
            f"/portfolio/snapshots/{valuation.snapshot.snapshot_id}"
        )

        self.assertEqual(asset.json()["asset_type"], "exchange_fund")
        self.assertEqual(len(factors.json()), 5)
        self.assertEqual(len(events.json()), 1)
        self.assertEqual(len(snapshots.json()), 1)
        self.assertEqual(len(detail.json()["positions"]), 1)

    def test_ai_task_context_and_unconfigured_execute(self):
        task = self.client.post(
            "/ai/tasks",
            json={
                "asset_id": self.asset.asset_id,
                "question": "分析黄金ETF",
            },
        )
        task_id = task.json()["task_id"]

        self.assertEqual(task.status_code, 201)
        self.assertEqual(len(self.client.get("/ai/tasks").json()), 1)
        self.assertGreaterEqual(
            len(self.client.get(f"/ai/tasks/{task_id}/evidence").json()),
            1,
        )
        other_user = self.client.post(
            "/users",
            json={"display_name": "另一个用户"},
        ).json()
        other_headers = {"X-User-Id": other_user["user_id"]}
        self.assertEqual(
            self.client.get(
                "/ai/tasks",
                headers=other_headers,
            ).json(),
            [],
        )
        self.assertEqual(
            self.client.get(
                f"/ai/tasks/{task_id}",
                headers=other_headers,
            ).status_code,
            404,
        )
        execute = self.client.post(
            f"/ai/tasks/{task_id}/execute"
        )
        self.assertEqual(execute.status_code, 503)

    def test_ai_task_can_execute_with_injected_adapter(self):
        app = create_app(
            Path(self.temp_dir.name) / "web-ai.sqlite3",
            model_adapter_factory=lambda: FakeModelAdapter(),
        )
        services = app.state.services
        asset = services.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="600519",
            name="贵州茅台",
            asset_type_hint="股票",
        ).asset
        with TestClient(app) as client:
            task = client.post(
                "/ai/tasks",
                json={
                    "asset_id": asset.asset_id,
                    "question": "总结证据",
                },
            ).json()
            completed = client.post(
                f"/ai/tasks/{task['task_id']}/execute"
            )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertEqual(
            completed.json()["model_name"],
            "web-api-fake",
        )


if __name__ == "__main__":
    unittest.main()
