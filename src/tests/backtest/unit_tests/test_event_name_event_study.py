import unittest
from datetime import date, timedelta
from decimal import Decimal

from src.backtest import (
    EventNameEventStudyEngine,
    EventNameEventStudySpec,
    discovery_rule_digest,
)
from src.event_name_discovery import (
    REQUIRED_CATALYST_CHECKS,
    REQUIRED_RELATION_CHECKS,
    AssetNameSnapshot,
    AlternativeCatalystAssessment,
    AlternativeCatalystEvidence,
    BusinessRelationAssessment,
    BusinessRelationEvidence,
    ConsensusConditionEvidence,
    ConsensusDimension,
    ConsensusFormationAssessment,
    EventDirection,
    EventNameDiscoveryService,
    EventNameDiscoveryMode,
    EventNameDiscoverySpec,
    EventPhrase,
    EventTimingQuality,
    ExternalEventAnchor,
    MarketReactionSnapshot,
    MATCHER_VERSION,
    RelationCheckResult,
)
from src.research_workflow import ResearchTaskSpec


class FakeTaskFactory:
    def create(self, **kwargs):
        return ResearchTaskSpec(
            task_id="temporary",
            asset_id=kwargs["asset_id"],
            profile_id=kwargs["profile_id"],
            question_type=kwargs["question_type"],
            horizon=kwargs["horizon"],
            as_of_date=kwargs["as_of_date"],
            requested_outputs=kwargs["requested_outputs"],
        )


class EventNameEventStudyEngineTest(unittest.TestCase):
    def test_event_family_holdout_and_overlap_clustering(self):
        first = _run(
            event_id="event:election-1",
            event_kind="election",
            symbol="002253",
            name="川大智胜",
            phrase="川大",
            event_date="2024-11-06",
            abnormal="0.10",
            reversal="-0.04",
        )
        overlap = _run(
            event_id="event:election-2",
            event_kind="election",
            symbol="002253",
            name="川大智胜",
            phrase="川大",
            event_date="2024-11-08",
            abnormal="0.06",
            reversal="-0.01",
        )
        holdout = _run(
            event_id="event:space-1",
            event_kind="space",
            symbol="300894",
            name="火星人",
            phrase="火星",
            event_date="2026-06-12",
            abnormal="0.08",
            reversal="-0.03",
        )

        result = EventNameEventStudyEngine().analyze(
            (first, overlap, holdout),
            EventNameEventStudySpec(
                manifest_id="manifest:unit-test",
                frozen_at="2020-01-01T00:00:00+00:00",
                training_event_ids=(
                    "event:election-1",
                    "event:election-2",
                ),
                holdout_event_ids=("event:space-1",),
                matcher_version=MATCHER_VERSION,
                discovery_rule_digest=discovery_rule_digest(first),
                cluster_cooldown_days=5,
            ),
        )

        self.assertEqual(len(result.cases), 2)
        self.assertEqual(len(result.excluded_overlaps), 1)
        self.assertEqual(result.training.observation_count, 1)
        self.assertEqual(result.holdout.observation_count, 1)
        self.assertEqual(
            result.overall.mean_aligned_abnormal_return,
            Decimal("0.09"),
        )
        self.assertEqual(result.overall.direction_hit_rate, Decimal("1"))
        self.assertEqual(result.overall.reversal_hit_rate, Decimal("1"))
        self.assertIn(
            "not executable PnL",
            result.limitations[0],
        )

    def test_prediction_evaluation_requires_exact_frozen_outcomes(self):
        prediction = _run(
            event_id="event:prediction",
            event_kind="sports",
            symbol="002253",
            name="川大智胜",
            phrase="川大",
            event_date="2024-11-06",
            abnormal="0.10",
            reversal="-0.04",
            predictive=True,
        )
        reaction = MarketReactionSnapshot(
            asset_id="asset:stock:002253",
            event_id="event:prediction",
            event_first_public_at="2024-11-06T08:00:00+08:00",
            event_trade_date="2024-11-06",
            observed_through="2024-11-11",
            abnormal_return=Decimal("0.10"),
            volume_zscore=Decimal("3"),
            reversal_abnormal_return=Decimal("-0.04"),
            timing_quality=EventTimingQuality.PREOPEN,
            limit_state="tradable_close",
            calendar_id="unit-test-calendar",
            calculation_version="event_name_reaction_v1",
            source_refs=("unit-test",),
        )
        manifest = EventNameEventStudySpec(
            manifest_id="manifest:prediction",
            frozen_at="2020-01-01T00:00:00+00:00",
            training_event_ids=("event:prediction",),
            holdout_event_ids=(),
            matcher_version=MATCHER_VERSION,
            discovery_rule_digest=discovery_rule_digest(prediction),
        )

        result = EventNameEventStudyEngine().analyze_predictions(
            (prediction,),
            {
                ("event:prediction", "asset:stock:002253"): (
                    reaction
                )
            },
            manifest,
        )

        self.assertEqual(result.overall.observation_count, 1)
        self.assertEqual(
            result.overall.mean_aligned_abnormal_return,
            Decimal("0.10"),
        )


def _run(
    *,
    event_id,
    event_kind,
    symbol,
    name,
    phrase,
    event_date,
    abnormal,
    reversal,
    predictive=False,
):
    asset_id = f"asset:stock:{symbol}"
    asset = AssetNameSnapshot(
        asset_id=asset_id,
        standard_symbol=symbol,
        name=name,
        effective_from="2020-01-01",
        effective_to=None,
        aliases=(),
        profile_id="ordinary_company",
        source="unit-test",
    )
    event = ExternalEventAnchor(
        event_id=event_id,
        title=event_id,
        event_kind=event_kind,
        occurred_at=f"{event_date}T08:00:00+08:00",
        first_public_at=f"{event_date}T08:00:00+08:00",
        source="unit-test",
        source_url="https://example.com/event",
        phrases=(
            EventPhrase(
                phrase_id=f"phrase:{event_id}",
                text=phrase,
                direction=EventDirection.POSITIVE,
                components=(phrase,),
            ),
        ),
        evidence_refs=("https://example.com/event",),
    )
    relation = BusinessRelationAssessment.evaluate(
        asset_id=asset_id,
        evidence=tuple(
            BusinessRelationEvidence(
                check_type=check_type,
                result=RelationCheckResult.ABSENT,
                detail="未发现关系",
                source_ref=f"source:{check_type}",
                available_at=f"{event_date}T08:30:00+08:00",
            )
            for check_type in REQUIRED_RELATION_CHECKS
        ),
        assessed_at=f"{event_date}T09:00:00+08:00",
    )
    reaction = MarketReactionSnapshot(
        asset_id=asset_id,
        event_id=event_id,
        event_first_public_at=f"{event_date}T08:00:00+08:00",
        event_trade_date=event_date,
        observed_through=(
            date.fromisoformat(event_date) + timedelta(days=5)
        ).isoformat(),
        abnormal_return=Decimal(abnormal),
        volume_zscore=Decimal("3"),
        reversal_abnormal_return=Decimal(reversal),
        timing_quality=EventTimingQuality.PREOPEN,
        limit_state="tradable_close",
        calendar_id="unit-test-calendar",
        calculation_version="event_name_reaction_v1",
        source_refs=("unit-test",),
    )
    as_of_date = (
        event_date
        if predictive
        else (
            date.fromisoformat(event_date) + timedelta(days=5)
        ).isoformat()
    )
    return EventNameDiscoveryService(
        task_factory=FakeTaskFactory()
    ).run(
        event=event,
        spec=EventNameDiscoverySpec(
            as_of_date=as_of_date,
            knowledge_cutoff_at=f"{event_date}T23:59:59+08:00",
            mode=(
                EventNameDiscoveryMode.PREDICTIVE
                if predictive
                else EventNameDiscoveryMode.CONFIRMATORY
            ),
        ),
        assets=(asset,),
        relation_assessments={asset_id: relation},
        catalyst_assessments={
            asset_id: AlternativeCatalystAssessment.evaluate(
                asset_id=asset_id,
                evidence=tuple(
                    AlternativeCatalystEvidence(
                        check_type=check_type,
                        result=RelationCheckResult.ABSENT,
                        detail="未发现替代催化",
                        source_ref=f"source:catalyst:{check_type}",
                        available_at=(
                            f"{event_date}T08:30:00+08:00"
                        ),
                    )
                    for check_type in REQUIRED_CATALYST_CHECKS
                ),
                window_start_at=f"{event_date}T00:00:00+08:00",
                window_end_at=f"{event_date}T09:00:00+08:00",
                queried_sources=("disclosures", "news"),
                query_refs=("query:disclosures", "query:news"),
                coverage_complete=True,
                assessed_at=f"{event_date}T09:00:00+08:00",
            )
        },
        consensus_assessments={
            asset_id: ConsensusFormationAssessment.evaluate(
                asset_id=asset_id,
                event_id=event_id,
                evidence=tuple(
                    ConsensusConditionEvidence(
                        dimension=dimension,
                        result=RelationCheckResult.PRESENT,
                        detail="共识条件成立",
                        source_ref=f"source:{dimension.value}",
                        available_at=(
                            f"{event_date}T08:30:00+08:00"
                        ),
                    )
                    for dimension in ConsensusDimension
                ),
                coverage_complete=True,
                assessed_at=f"{event_date}T09:00:00+08:00",
            )
        },
        reactions={} if predictive else {asset_id: reaction},
    )


if __name__ == "__main__":
    unittest.main()
