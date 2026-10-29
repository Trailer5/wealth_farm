"""Implied expectations for one stock on demand, recorded as an objective ledger assessment."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.data_access import DATASET_STOCK_DAILY_BARS, AssetInput, DataAccessRequest, DataAccessService
from src.financial_data import DATASET_STOCK_FINANCIAL_INDICATORS
from src.fundamental_analysis import (
    FinancialInstitutionAnalysisService,
    FundamentalAnalysisService,
    ValuationModelService,
    valuation_model_lines,
)
from src.master_data import ASSET_TYPE_STOCK, MasterDataService
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    AssessmentDimension,
    Confidence,
    StateAssessment,
    SubjectType,
)
from src.research_ledger import LedgerScope, ResearchLedgerService
from src.research_workflow.valuation_context import collect_bank_valuation_model

from .request_planner import ResearchPlanningRequest, ResearchRequestPlanner

FUND_NOTE = "基金不估内在价值：看底层指数或行业的估值分位（sector-archive、run-single 的估值位置）"


class ValuationModelRunner:
    def __init__(
        self,
        *,
        planner: ResearchRequestPlanner,
        master_data: MasterDataService,
        data_access: DataAccessService,
        fundamental: FundamentalAnalysisService,
        financial: FinancialInstitutionAnalysisService,
        models: ValuationModelService,
        ledger: ResearchLedgerService,
    ) -> None:
        self.planner = planner
        self.master_data = master_data
        self.data_access = data_access
        self.fundamental = fundamental
        self.financial = financial
        self.models = models
        self.ledger = ledger

    def run(
        self,
        symbol: str | None,
        as_of_date: str,
        *,
        asset_id: str | None = None,
        equity_risk_premium: float | None = None,
        terminal_growth: float | None = None,
        record: bool = True,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        plan = self.planner.plan(
            ResearchPlanningRequest(
                as_of_date=as_of_date,
                asset_id=asset_id,
                symbol=None if asset_id else symbol,
                asset_type=None if asset_id else ASSET_TYPE_STOCK,
            )
        )
        if plan.identity is None or plan.profile_id is None:
            raise ValueError(f"无法唯一识别股票 {symbol or asset_id}：" + "；".join(plan.issues or plan.routing_notes))
        symbol = symbol or plan.identity.symbol
        if plan.profile_id in (PROFILE_OPEN_FUND, PROFILE_EXCHANGE_FUND):
            return {"symbol": symbol, "profile_id": plan.profile_id, "applicable": False, "note": FUND_NOTE}
        asset = self.master_data.get_asset(plan.identity.asset_id)
        price = self.last_close(asset.asset_id, as_of_date, allow_remote)
        overrides = {"equity_risk_premium": equity_risk_premium, "terminal_growth": terminal_growth}
        if plan.profile_id == PROFILE_FINANCIAL_INSTITUTION:
            model = self._bank(asset.asset_id, as_of_date, price, allow_remote, overrides)
        else:
            financials = self.data_access.get_financial_bundle(
                asset_id=asset.asset_id,
                start_date=(date.fromisoformat(as_of_date) - timedelta(days=1095)).isoformat(),
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
            )
            fundamental = self.fundamental.analyze(financials, market_price=price)
            model = self.models.evaluate_company(
                snapshot=fundamental.snapshot,
                asset=asset,
                profile_id=plan.profile_id,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                **overrides,
            )
        payload = {
            "symbol": symbol,
            "asset_id": asset.asset_id,
            "name": asset.display_name,
            "profile_id": plan.profile_id,
            "applicable": True,
            "price": str(price),
            "model": model,
            "lines": [{"name": name, "text": text} for name, text in valuation_model_lines(model)],
            "ledger_entry_id": None,
        }
        if record:
            assessment = _assessment(asset.asset_id, asset.display_name or symbol, model)
            (entry,) = self.ledger.record(
                (assessment,),
                scope=LedgerScope.OBJECTIVE,
                user_id=None,
                source_ref=f"valuation_model:{asset.asset_id}:{as_of_date}",
            )
            payload["ledger_entry_id"] = entry.entry_id
        return payload

    def last_close(self, asset_id: str, as_of_date: str, allow_remote: bool = True) -> Decimal:
        bars = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date=(date.fromisoformat(as_of_date) - timedelta(days=30)).isoformat(),
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
            )
        )
        closes = [r for r in bars.records if str(r.get("trade_date")) <= as_of_date and r.get("close")]
        if not closes:
            raise ValueError(f"{as_of_date} 前 30 天没有收盘价")
        return Decimal(str(max(closes, key=lambda r: str(r["trade_date"]))["close"]))

    def _bank(
        self, asset_id: str, as_of_date: str, price: Decimal, allow_remote: bool, overrides: dict[str, Any]
    ) -> dict[str, Any]:
        indicators = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset_id),
                dataset=DATASET_STOCK_FINANCIAL_INDICATORS,
                start_date=f"{int(as_of_date[:4]) - 6}-01-01",
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                remote_sources=("akshare",),
            )
        )
        records = [dict(record) for record in indicators.records]
        metrics = self.financial.analyze(
            asset_id=asset_id, as_of_date=as_of_date, market_price=price, indicator_records=records
        )
        model, gaps = collect_bank_valuation_model(
            self.models,
            as_of_date=as_of_date,
            metrics=metrics,
            indicator_records=records,
            allow_remote=allow_remote,
            **overrides,
        )
        if model is None:
            raise ValueError("；".join(gap.message for gap in gaps) or "缺市净率，无法计算 PB-ROE")
        return model


def _assessment(asset_id: str, name: str, model: dict[str, Any]) -> StateAssessment:
    lines = valuation_model_lines(model)
    refs = (f"valuation_model:{model['method']}:{model['report_period']}", "macro:cgb_10y")
    label = f"{name}：{lines[0][1]}"
    digest = hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return StateAssessment(
        assessment_id=f"valuation-model:{asset_id}:{model['as_of_date']}:{digest}",
        subject_type=SubjectType.ASSET,
        subject_id=asset_id,
        as_of_date=model["as_of_date"],
        dimensions=tuple(AssessmentDimension(n, t, refs) for n, t in lines),
        label=label,
        confidence=Confidence.MEDIUM if not model["gaps"] else Confidence.LOW,
        change_signals=tuple(model["notes"]) + tuple(model["gaps"]),
    )
