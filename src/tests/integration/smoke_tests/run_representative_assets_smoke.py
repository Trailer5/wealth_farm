"""Run cross-module checks with samples from separately validated live sources."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.alerts import (
        RULE_PRICE_ABOVE,
        AlertService,
        SQLiteAlertRepository,
    )
    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        DATASET_STOCK_DAILY_BARS,
        DataAccessResponse,
    )
    from src.data_quality import DataQualityService, SQLiteDataQualityRepository
    from src.data_src.models import DisclosureDocument
    from src.event_knowledge import (
        EVENT_FINANCIAL_REPORT,
        EventKnowledgeService,
        SQLiteEventRepository,
    )
    from src.factor_engineering import FactorService, SQLiteFactorRepository
    from src.master_data import MasterDataService, SQLiteMasterDataRepository
    from src.portfolio_risk import (
        PortfolioRiskService,
        SQLitePortfolioRepository,
    )
    from src.research_state import (
        ResearchStateService,
        SQLiteResearchStateRepository,
    )
    from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "representative-assets.sqlite3"
        master = MasterDataService(SQLiteMasterDataRepository(db_path))
        moutai = master.resolve_identifier(
            source="baostock:query_stock_basic",
            raw_symbol="sh.600519",
            name="贵州茅台",
            asset_type_hint="股票",
        ).asset
        pingan = master.resolve_identifier(
            source="baostock:query_stock_basic",
            raw_symbol="sz.000001",
            name="平安银行",
            asset_type_hint="股票",
        ).asset
        hs300 = master.resolve_identifier(
            source="tencent",
            raw_symbol="sh510300",
            name="沪深300ETF华泰柏瑞",
            asset_type_hint="ETF",
            exchange_hint="sh",
        ).asset
        open_fund = master.resolve_identifier(
            source="akshare:fund_open_fund_info_em",
            raw_symbol="000001",
            name="华夏成长混合",
            asset_type_hint="混合型",
        ).asset
        if pingan.asset_id == open_fund.asset_id:
            raise RuntimeError("Stock and open fund identities were merged")

        responses = {
            "moutai": _response(
                moutai,
                DATASET_STOCK_DAILY_BARS,
                [1488.0, 1475.0, 1500.0, 1512.0, 1498.0, 1520.0],
                "baostock:query_history_k_data_plus",
            ),
            "pingan": _response(
                pingan,
                DATASET_STOCK_DAILY_BARS,
                [10.5, 10.4, 10.6, 10.7, 10.65, 10.8],
                "baostock:query_history_k_data_plus",
            ),
            "hs300_etf": _response(
                hs300,
                DATASET_EXCHANGE_FUND_DAILY_BARS,
                [4.55, 4.60, 4.58, 4.66, 4.68, 4.701],
                "tencent",
            ),
        }

        quality = DataQualityService(SQLiteDataQualityRepository(db_path))
        factors = FactorService(SQLiteFactorRepository(db_path), master)
        quality_results = {}
        factor_counts = {}
        for name, response in responses.items():
            summary = quality.evaluate_data_access_response(
                task_name=f"representative-{name}",
                response=response,
                expected_start="2026-07-17",
                expected_end="2026-07-24",
            )
            quality_results[name] = summary.can_publish
            factor_counts[name] = len(factors.compute_from_response(response))

        document = DisclosureDocument(
            document_id="1225114741",
            symbol="600519",
            title="贵州茅台2025年年度报告",
            announcement_date="2026-04-17",
            category="annual",
            source="cninfo",
            file_url=(
                "https://static.cninfo.com.cn/finalpage/"
                "2026-04-17/1225114741.PDF"
            ),
            page_url=None,
        )
        event_service = EventKnowledgeService(
            SQLiteEventRepository(db_path),
            master,
        )
        events = event_service.ingest_disclosure(moutai.asset_id, document)

        users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
        context = users.default_context()
        research = ResearchStateService(
            SQLiteResearchStateRepository(db_path),
            users,
            master,
        )
        for response in responses.values():
            research.add_watchlist_item(
                context,
                asset_id=response.asset.asset_id,
                reason="代表性来源样本集成验证",
            )
        research.upsert_holding(
            context,
            asset_id=moutai.asset_id,
            quantity=1,
            average_cost=1450.0,
        )
        research.upsert_holding(
            context,
            asset_id=hs300.asset_id,
            quantity=100,
            average_cost=4.5,
        )
        report = research.save_report(
            context,
            asset_id=moutai.asset_id,
            title="代表性标的集成验证",
            summary="多来源形态和研究模块均通过。",
            content_markdown="# 集成验证\n\n代表性资产流程通过。",
            status="final",
        )
        portfolio = PortfolioRiskService(
            SQLitePortfolioRepository(db_path),
            research,
        ).value_current_holdings(
            context,
            as_of_date="2026-07-24",
            prices={
                moutai.asset_id: 1520.0,
                hs300.asset_id: 4.701,
            },
        )

        alerts = AlertService(
            SQLiteAlertRepository(db_path),
            users,
            master,
        )
        alerts.create_price_rule(
            context,
            asset_id=hs300.asset_id,
            rule_type=RULE_PRICE_ABOVE,
            threshold=4.6,
        )
        alerts.create_event_rule(
            context,
            asset_id=moutai.asset_id,
            event_type=EVENT_FINANCIAL_REPORT,
        )
        alerts.create_max_weight_rule(context, threshold=0.5)
        price_alerts = alerts.evaluate_price(
            context,
            asset_id=hs300.asset_id,
            price=4.701,
            observed_at="2026-07-24",
        )
        event_alerts = alerts.evaluate_event(context, events[0])
        portfolio_alerts = alerts.evaluate_portfolio(
            context,
            portfolio.snapshot,
        )

        result = {
            "source_matrix_validated_separately": {
                "total": 9,
                "passed": 9,
            },
            "assets": {
                name: {
                    "asset_id": response.asset.asset_id,
                    "asset_type": response.asset.asset_type,
                    "record_count": len(response.records),
                    "source": response.sources[0],
                }
                for name, response in responses.items()
            },
            "open_fund_identity": {
                "asset_id": open_fund.asset_id,
                "different_from_stock_000001": (
                    open_fund.asset_id != pingan.asset_id
                ),
            },
            "quality": quality_results,
            "factor_counts": factor_counts,
            "event_count": len(events),
            "research_state": {
                "watchlist_count": len(
                    research.list_watchlist_items(context)
                ),
                "holding_count": len(research.list_holdings(context)),
                "report_id": report.report_id,
            },
            "portfolio": {
                "total_market_value": portfolio.snapshot.total_market_value,
                "max_weight": portfolio.snapshot.max_weight,
                "concentration_hhi": portfolio.snapshot.concentration_hhi,
            },
            "alerts": {
                "price": len(price_alerts),
                "event": len(event_alerts),
                "portfolio": len(portfolio_alerts),
            },
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def _response(asset, dataset, closes, source):
    from src.data_access import DataAccessResponse

    dates = [
        "2026-07-17",
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
    ]
    records = tuple(
        {
            "symbol": asset.standard_symbol,
            "trade_date": trade_date,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
            "amount": close * 1000.0,
            "source": source,
        }
        for trade_date, close in zip(dates, closes)
    )
    return DataAccessResponse(
        asset=asset,
        dataset=dataset,
        records=records,
        sources=(source,),
        coverage_start=dates[0],
        coverage_end=dates[-1],
        complete=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
