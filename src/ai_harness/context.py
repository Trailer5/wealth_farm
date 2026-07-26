"""Assemble compact, source-backed evidence for one asset analysis."""

from __future__ import annotations

import json

from src.event_knowledge import EventKnowledgeService
from src.factor_engineering import FactorService
from src.master_data import MasterDataService
from src.research_state import ResearchStateService
from src.user_space import UserContext

from .models import EvidenceReference


class ResearchContextAssembler:
    """Collect traceable evidence without model-specific prompt formatting."""

    def __init__(
        self,
        master_data: MasterDataService,
        factors: FactorService,
        events: EventKnowledgeService,
        research_state: ResearchStateService,
    ) -> None:
        self.master_data = master_data
        self.factors = factors
        self.events = events
        self.research_state = research_state

    def assemble(
        self,
        context: UserContext,
        asset_id: str,
    ) -> tuple[EvidenceReference, ...]:
        """Return stable evidence references for one user and asset."""

        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        evidence = [
            EvidenceReference(
                evidence_id=f"asset:{asset.asset_id}",
                evidence_type="asset",
                source_id=asset.asset_id,
                title=asset.display_name or asset.standard_symbol,
                content=_json(
                    {
                        "asset_type": asset.asset_type,
                        "symbol": asset.standard_symbol,
                        "market": asset.market,
                        "exchange": asset.exchange,
                    }
                ),
            )
        ]
        for factor in self.factors.list_values(asset_id):
            source_id = (
                f"{asset_id}:{factor.factor_name}:"
                f"{factor.as_of_date}:{factor.calculation_version}"
            )
            evidence.append(
                EvidenceReference(
                    evidence_id=f"factor:{source_id}",
                    evidence_type="factor",
                    source_id=source_id,
                    title=factor.factor_name,
                    content=_json(
                        {
                            "value": factor.value,
                            "as_of_date": factor.as_of_date,
                            "parameters": factor.parameters,
                            "version": factor.calculation_version,
                        }
                    ),
                )
            )
        for event in self.events.list_events(asset_id):
            evidence.append(
                EvidenceReference(
                    evidence_id=f"event:{event.event_id}",
                    evidence_type="event",
                    source_id=event.event_id,
                    title=event.title,
                    content=_json(
                        {
                            "event_type": event.event_type,
                            "event_date": event.event_date,
                            "summary": event.summary,
                            "source_url": event.source_url,
                        }
                    ),
                )
            )
        for report in self.research_state.list_reports(
            context,
            asset_id=asset_id,
        ):
            evidence.append(
                EvidenceReference(
                    evidence_id=f"report:{report.report_id}",
                    evidence_type="report",
                    source_id=report.report_id,
                    title=report.title,
                    content=report.content_markdown,
                    metadata={
                        "summary": report.summary,
                        "status": report.status,
                    },
                )
            )
        holding = next(
            (
                position
                for position in self.research_state.list_holdings(context)
                if position.asset_id == asset_id
            ),
            None,
        )
        if holding is not None:
            evidence.append(
                EvidenceReference(
                    evidence_id=(
                        f"holding:{context.user_id}:{asset_id}"
                    ),
                    evidence_type="holding",
                    source_id=f"{context.user_id}:{asset_id}",
                    title="当前持仓",
                    content=_json(
                        {
                            "quantity": holding.quantity,
                            "average_cost": holding.average_cost,
                            "currency": holding.currency,
                            "note": holding.note,
                        }
                    ),
                )
            )
        return tuple(evidence)


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
