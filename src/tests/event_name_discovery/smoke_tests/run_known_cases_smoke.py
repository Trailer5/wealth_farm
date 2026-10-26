"""Quick validation of known event-name attention cases."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.event_name_discovery import (
        REQUIRED_CATALYST_CHECKS,
        REQUIRED_RELATION_CHECKS,
        AssetNameSnapshot,
        AlternativeCatalystAssessment,
        AlternativeCatalystEvidence,
        AttentionMatchChannel,
        AttentionOutcomeLayer,
        AttentionOutcomeObservation,
        BusinessRelationAssessment,
        BusinessRelationEvidence,
        ConsensusConditionEvidence,
        ConsensusDimension,
        ConsensusFormationAssessment,
        EventDirection,
        EventNameMatcher,
        EventNameDiscoveryService,
        EventNameDiscoverySpec,
        EventPhrase,
        ExternalEventAnchor,
        NameAssociationCaseAssessment,
        ReactionBar,
        RelationCheckResult,
        SQLiteEventNameDiscoveryRepository,
    )
    from src.master_data import (
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_orchestration import (
        EventNameDiscoveryOrchestrationPipeline,
        EventNameDiscoveryRunRequest,
    )
    from src.research_contracts import PROFILE_ORDINARY_COMPANY
    from src.research_workflow import ResearchTaskFactory

    source = TencentExchangeFundDataSource()
    cases = (
        _trump_case(
            source,
            AssetNameSnapshot,
            BusinessRelationAssessment,
            BusinessRelationEvidence,
            ConsensusFormationAssessment,
            ConsensusConditionEvidence,
            ConsensusDimension,
            AlternativeCatalystAssessment,
            AlternativeCatalystEvidence,
            RelationCheckResult,
            REQUIRED_RELATION_CHECKS,
            REQUIRED_CATALYST_CHECKS,
            EventDirection,
            EventPhrase,
            ExternalEventAnchor,
            EventNameDiscoverySpec,
            ReactionBar,
            PROFILE_ORDINARY_COMPANY,
        ),
        _spacex_case(
            source,
            AssetNameSnapshot,
            BusinessRelationAssessment,
            BusinessRelationEvidence,
            ConsensusFormationAssessment,
            ConsensusConditionEvidence,
            ConsensusDimension,
            AlternativeCatalystAssessment,
            AlternativeCatalystEvidence,
            RelationCheckResult,
            REQUIRED_RELATION_CHECKS,
            REQUIRED_CATALYST_CHECKS,
            EventDirection,
            EventPhrase,
            ExternalEventAnchor,
            EventNameDiscoverySpec,
            ReactionBar,
            PROFILE_ORDINARY_COMPANY,
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "known-cases.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(database_path)
        )
        for case in cases:
            asset = case["asset"]
            master_data.register_asset(
                Asset(
                    asset_id=asset.asset_id,
                    asset_type=ASSET_TYPE_STOCK,
                    standard_symbol=asset.standard_symbol,
                    display_name=asset.name,
                    market="cn_a",
                    exchange="sz",
                ),
                source="known-case",
                raw_symbol=asset.standard_symbol,
            )
        pipeline = EventNameDiscoveryOrchestrationPipeline(
            discovery_service=EventNameDiscoveryService(
                task_factory=ResearchTaskFactory(master_data),
                repository=SQLiteEventNameDiscoveryRepository(
                    database_path
                ),
            )
        )
        output = []
        for case in cases:
            asset = case["asset"]
            result = pipeline.run(
                EventNameDiscoveryRunRequest(
                    event=case["event"],
                    spec=case["spec"],
                    assets=(asset,),
                    relation_assessments={
                        asset.asset_id: case["relation"]
                    },
                    catalyst_assessments={
                        asset.asset_id: case["catalyst"]
                    },
                    consensus_assessments={
                        asset.asset_id: case["consensus"]
                    },
                    asset_bars_by_id={
                        asset.asset_id: case["asset_bars"]
                    },
                    benchmark_bars=case["benchmark_bars"],
                    trading_calendar=tuple(
                        bar.trade_date
                        for bar in case["benchmark_bars"]
                    ),
                    calendar_id="tencent:sz399001",
                )
            )
            outcome = result.discovery_run.outcomes[0]
            output.append(
                {
                    "case": case["case"],
                    "status": outcome.status.value,
                    "candidate_count": len(
                        result.discovery_run.candidates
                    ),
                    "match_type": (
                        outcome.name_matches[0].match_type.value
                        if outcome.name_matches
                        else None
                    ),
                    "match_explanation": (
                        outcome.name_matches[0].explanation
                        if outcome.name_matches
                        else None
                    ),
                    "relation_status": (
                        outcome.relation.status.value
                        if outcome.relation
                        else None
                    ),
                    "catalyst_status": (
                        outcome.catalyst.status.value
                        if outcome.catalyst
                        else None
                    ),
                    "consensus_status": (
                        outcome.consensus.status.value
                        if outcome.consensus
                        else None
                    ),
                    "event_trade_date": (
                        outcome.reaction.event_trade_date
                        if outcome.reaction
                        else None
                    ),
                    "abnormal_return": (
                        float(outcome.reaction.abnormal_return)
                        if outcome.reaction
                        else None
                    ),
                    "volume_zscore": (
                        float(outcome.reaction.volume_zscore)
                        if outcome.reaction
                        and outcome.reaction.volume_zscore is not None
                        else None
                    ),
                    "reversal_abnormal_return": (
                        float(
                            outcome.reaction.reversal_abnormal_return
                        )
                        if outcome.reaction
                        and outcome.reaction.reversal_abnormal_return
                        is not None
                        else None
                    ),
                    "reasons": outcome.reasons,
                }
            )
    output.extend(
        _extension_checks(
            AssetNameSnapshot=AssetNameSnapshot,
            AttentionMatchChannel=AttentionMatchChannel,
            AttentionOutcomeLayer=AttentionOutcomeLayer,
            AttentionOutcomeObservation=AttentionOutcomeObservation,
            EventDirection=EventDirection,
            EventNameMatcher=EventNameMatcher,
            EventPhrase=EventPhrase,
            NameAssociationCaseAssessment=(
                NameAssociationCaseAssessment
            ),
            RelationCheckResult=RelationCheckResult,
            profile_id=PROFILE_ORDINARY_COMPANY,
        )
    )
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


def _extension_checks(
    *,
    AssetNameSnapshot,
    AttentionMatchChannel,
    AttentionOutcomeLayer,
    AttentionOutcomeObservation,
    EventDirection,
    EventNameMatcher,
    EventPhrase,
    NameAssociationCaseAssessment,
    RelationCheckResult,
    profile_id,
):
    llm_asset = AssetNameSnapshot(
        asset_id="asset:stock:hk:06658",
        standard_symbol="06658",
        name="溜溜梅",
        effective_from="2026-06-15",
        effective_to=None,
        aliases=(),
        profile_id=profile_id,
        source="hkex:listing",
    )
    llm_phrase = EventPhrase(
        phrase_id="large-language-model",
        text="大语言模型",
        direction=EventDirection.POSITIVE,
        components=("大语言模型",),
        initials="llm",
        channel=AttentionMatchChannel.CROSS_LANGUAGE_ACRONYM,
    )
    llm_match = EventNameMatcher().match(
        phrases=(llm_phrase,),
        assets=(llm_asset,),
        event_date="2026-06-15",
    )[llm_asset.asset_id][0]

    corona_event = "external-event:covid-2020"
    corona_target = "brand:corona-extra"
    corona = NameAssociationCaseAssessment.evaluate(
        event_id=corona_event,
        target_id=corona_target,
        target_name="Corona Extra",
        channel=AttentionMatchChannel.BRAND_NAME,
        observations=(
            AttentionOutcomeObservation(
                corona_target,
                "brand",
                "Corona Extra",
                corona_event,
                AttentionOutcomeLayer.SEARCH_ATTENTION,
                EventDirection.NEGATIVE,
                RelationCheckResult.PRESENT,
                None,
                "search_attention",
                "2020-01-01",
                "2020-03-31",
                "https://www.snopes.com/fact-check/"
                "corona-beer-fear-coronavirus/",
                "2020-04-01T00:00:00+00:00",
            ),
            AttentionOutcomeObservation(
                corona_target,
                "brand",
                "Corona Extra",
                corona_event,
                AttentionOutcomeLayer.REALIZED_SALES,
                EventDirection.NEGATIVE,
                RelationCheckResult.ABSENT,
                Decimal("0.50"),
                "weekly_sales_ratio",
                "2020-03-01",
                "2020-03-31",
                "https://doi.org/10.1002/jaa2.18",
                "2022-01-01T00:00:00+00:00",
            ),
        ),
    )
    return [
        {
            "case": "Liuliumei IPO -> LLM",
            "status": "mixed_attribution",
            "match_type": llm_match.match_type.value,
            "channel": llm_match.attention_channel.value,
        },
        {
            "case": "Coronavirus -> Corona beer",
            "status": corona.conclusion.value,
            "sales_decline_supported": False,
        },
    ]


def _trump_case(
    source,
    AssetNameSnapshot,
    BusinessRelationAssessment,
    BusinessRelationEvidence,
    ConsensusFormationAssessment,
    ConsensusConditionEvidence,
    ConsensusDimension,
    AlternativeCatalystAssessment,
    AlternativeCatalystEvidence,
    RelationCheckResult,
    REQUIRED_RELATION_CHECKS,
    REQUIRED_CATALYST_CHECKS,
    EventDirection,
    EventPhrase,
    ExternalEventAnchor,
    EventNameDiscoverySpec,
    ReactionBar,
    profile_id,
):
    asset_id = "asset:stock:002253"
    start, end = "2024-06-01", "2024-07-31"
    asset_bars, benchmark_bars = _bars(
        source,
        "002253",
        start,
        end,
        Decimal("0.10"),
        ReactionBar,
    )
    event_url = "https://www.tmtpost.com/nictation/7167950.html"
    review_url = (
        "https://www.guancha.cn/economy/2024_07_16_741579.shtml"
    )
    return {
        "case": "Trump assassination attempt -> 川大智胜",
        "asset": AssetNameSnapshot(
            asset_id,
            "002253",
            "川大智胜",
            "2008-06-23",
            None,
            (),
            profile_id,
            "exchange:current_name",
        ),
        "event": ExternalEventAnchor(
            event_id="external-event:trump-attempt-2024",
            title="特朗普竞选集会遇袭后胜选预期上升",
            event_kind="election",
            occurred_at="2024-07-14T06:11:00+08:00",
            first_public_at="2024-07-14T06:11:00+08:00",
            source="public-news",
            source_url=event_url,
            phrases=(
                EventPhrase(
                    "trump-win",
                    "川普胜选",
                    EventDirection.POSITIVE,
                    ("川", "智胜"),
                ),
            ),
            evidence_refs=(event_url, review_url),
        ),
        "spec": EventNameDiscoverySpec(
            as_of_date=end,
            knowledge_cutoff_at="2024-07-31T23:59:59+08:00",
            retrospective_label=True,
        ),
        "relation": BusinessRelationAssessment.evaluate(
            asset_id=asset_id,
            evidence=tuple(
                BusinessRelationEvidence(
                    check_type=check_type,
                    result=RelationCheckResult.ABSENT,
                    detail=(
                        "公司主营空管、软件和三维视觉；"
                        "未见与特朗普的股权、商业或公告联系"
                    ),
                    source_ref=event_url,
                    available_at="2024-07-15T10:46:00+08:00",
                )
                for check_type in REQUIRED_RELATION_CHECKS
            ),
            assessed_at="2024-07-16T18:00:00+08:00",
        ),
        "catalyst": AlternativeCatalystAssessment.evaluate(
            asset_id=asset_id,
            evidence=tuple(
                AlternativeCatalystEvidence(
                    check_type=check_type,
                    result=RelationCheckResult.ABSENT,
                    detail=(
                        "已复核同期公司回应、业绩预告和板块表现；"
                        "未发现同窗口同方向的直接催化"
                    ),
                    source_ref=review_url,
                    available_at="2024-07-16T18:00:00+08:00",
                )
                for check_type in REQUIRED_CATALYST_CHECKS
            ),
            window_start_at="2024-07-10T00:00:00+08:00",
            window_end_at="2024-07-16T18:00:00+08:00",
            queried_sources=(
                "company_disclosures",
                "financial_media",
                "industry_news",
            ),
            query_refs=(event_url, review_url),
            coverage_complete=True,
            assessed_at="2024-07-16T18:00:00+08:00",
        ),
        "consensus": ConsensusFormationAssessment.evaluate(
            asset_id=asset_id,
            event_id="external-event:trump-attempt-2024",
            evidence=tuple(
                ConsensusConditionEvidence(
                    dimension=dimension,
                    result=RelationCheckResult.PRESENT,
                    detail=(
                        "事件高度显著、名称叙事简单，涨停制度与"
                        "低流通卖压使其成为天然协调点"
                    ),
                    source_ref=event_url,
                    available_at="2024-07-15T10:46:00+08:00",
                )
                for dimension in ConsensusDimension
            ),
            coverage_complete=True,
            assessed_at="2024-07-16T18:00:00+08:00",
        ),
        "asset_bars": asset_bars,
        "benchmark_bars": benchmark_bars,
    }


def _spacex_case(
    source,
    AssetNameSnapshot,
    BusinessRelationAssessment,
    BusinessRelationEvidence,
    ConsensusFormationAssessment,
    ConsensusConditionEvidence,
    ConsensusDimension,
    AlternativeCatalystAssessment,
    AlternativeCatalystEvidence,
    RelationCheckResult,
    REQUIRED_RELATION_CHECKS,
    REQUIRED_CATALYST_CHECKS,
    EventDirection,
    EventPhrase,
    ExternalEventAnchor,
    EventNameDiscoverySpec,
    ReactionBar,
    profile_id,
):
    asset_id = "asset:stock:300894"
    start, end = "2026-05-01", "2026-06-30"
    asset_bars, benchmark_bars = _bars(
        source,
        "300894",
        start,
        end,
        Decimal("0.20"),
        ReactionBar,
    )
    event_url = (
        "https://news.qq.com/rain/a/20260613A01OYT00"
    )
    annual_report = (
        "https://static.cninfo.com.cn/finalpage/"
        "2026-04-23/1225147804.PDF"
    )
    robot_news = (
        "https://finance.sina.com.cn/chanjing/gsnews/"
        "2026-06-01/doc-inhzwyqr2414618.shtml"
    )
    catalyst_evidence = []
    for check_type in REQUIRED_CATALYST_CHECKS:
        present = check_type in {"industry_theme", "market_narrative"}
        catalyst_evidence.append(
            AlternativeCatalystEvidence(
                check_type=check_type,
                result=(
                    RelationCheckResult.PRESENT
                    if present
                    else RelationCheckResult.ABSENT
                ),
                detail=(
                    "同期存在宇树科技IPO和机器人财务投资题材"
                    if present
                    else "未发现其他公司行动"
                ),
                source_ref=robot_news if present else annual_report,
                available_at=(
                    "2026-06-01T00:00:00+08:00"
                    if present
                    else "2026-04-23T00:00:00+08:00"
                ),
            )
        )
    return {
        "case": "SpaceX listing -> 火星人",
        "asset": AssetNameSnapshot(
            asset_id,
            "300894",
            "火星人",
            "2020-12-31",
            None,
            (),
            profile_id,
            "exchange:current_name",
        ),
        "event": ExternalEventAnchor(
            event_id="external-event:spacex-ipo-2026",
            title="SpaceX正式登陆纳斯达克",
            event_kind="space",
            occurred_at="2026-06-12T23:47:00+08:00",
            first_public_at="2026-06-12T23:47:00+08:00",
            source="public-news",
            source_url=event_url,
            phrases=(
                EventPhrase(
                    "mars",
                    "火星",
                    EventDirection.POSITIVE,
                    ("火星",),
                ),
            ),
            evidence_refs=(event_url,),
        ),
        "spec": EventNameDiscoverySpec(
            as_of_date=end,
            knowledge_cutoff_at="2026-06-30T23:59:59+08:00",
            retrospective_label=True,
        ),
        "relation": BusinessRelationAssessment.evaluate(
            asset_id=asset_id,
            evidence=tuple(
                BusinessRelationEvidence(
                    check_type=check_type,
                    result=RelationCheckResult.ABSENT,
                    detail=(
                        "主营为集成厨电，未见与SpaceX的股权、"
                        "商业或公告联系"
                    ),
                    source_ref=annual_report,
                    available_at="2026-04-23T00:00:00+08:00",
                )
                for check_type in REQUIRED_RELATION_CHECKS
            ),
            assessed_at="2026-06-13T18:00:00+08:00",
        ),
        "consensus": ConsensusFormationAssessment.evaluate(
            asset_id=asset_id,
            event_id="external-event:spacex-ipo-2026",
            evidence=tuple(
                ConsensusConditionEvidence(
                    dimension=dimension,
                    result=RelationCheckResult.PRESENT,
                    detail=(
                        "SpaceX事件显著且“火星”叙事简单，"
                        "市场存在可传播的协调焦点"
                    ),
                    source_ref=event_url,
                    available_at="2026-06-13T18:00:00+08:00",
                )
                for dimension in ConsensusDimension
            ),
            coverage_complete=True,
            assessed_at="2026-06-13T18:00:00+08:00",
        ),
        "catalyst": AlternativeCatalystAssessment.evaluate(
            asset_id=asset_id,
            evidence=tuple(catalyst_evidence),
            window_start_at="2026-06-01T00:00:00+08:00",
            window_end_at="2026-06-13T18:00:00+08:00",
            queried_sources=(
                "company_disclosures",
                "financial_media",
                "industry_news",
            ),
            query_refs=(annual_report, robot_news),
            coverage_complete=True,
            assessed_at="2026-06-13T18:00:00+08:00",
        ),
        "asset_bars": asset_bars,
        "benchmark_bars": benchmark_bars,
    }


def _bars(source, symbol, start, end, limit_rate, ReactionBar):
    stock = source.get_a_share_daily_bars(symbol, start, end)
    benchmark = source.get_index_daily_bars(
        "399001",
        start,
        end,
        "sz",
    )
    return (
        _reaction_bars(stock, limit_rate, ReactionBar),
        _reaction_bars(benchmark, None, ReactionBar),
    )


def _reaction_bars(bars, limit_rate, ReactionBar):
    result = []
    previous_close = None
    for bar in sorted(bars, key=lambda item: item.trade_date):
        if (
            bar.close is None
            or bar.volume is None
            or bar.close <= 0
            or bar.volume < 0
        ):
            continue
        limit_up = None
        limit_down = None
        if limit_rate is not None and previous_close is not None:
            limit_up = (
                Decimal(str(previous_close))
                * (Decimal("1") + limit_rate)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            limit_down = (
                Decimal(str(previous_close))
                * (Decimal("1") - limit_rate)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result.append(
            ReactionBar(
                trade_date=bar.trade_date,
                close=Decimal(str(bar.close)),
                volume=Decimal(str(bar.volume)),
                source=bar.source,
                limit_up=limit_up,
                limit_down=limit_down,
            )
        )
        previous_close = bar.close
    return tuple(result)


if __name__ == "__main__":
    raise SystemExit(main())
