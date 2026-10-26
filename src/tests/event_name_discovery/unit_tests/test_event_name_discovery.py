import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from src.event_name_discovery import (
    REQUIRED_CATALYST_CHECKS,
    REQUIRED_RELATION_CHECKS,
    AssetNameSnapshot,
    AssetNameSnapshotLoader,
    AlternativeCatalystAssessment,
    AlternativeCatalystEvidence,
    AlternativeCatalystStatus,
    AttentionAttributionAssessment,
    AttentionAttributionMode,
    AttentionAttributionStatus,
    AttentionMatchChannel,
    AttentionOutcomeLayer,
    AttentionOutcomeObservation,
    BusinessRelationAssessment,
    BusinessRelationEvidence,
    BusinessRelationStatus,
    ConsensusConditionEvidence,
    ConsensusDimension,
    ConsensusFormationAssessment,
    ConsensusFormationStatus,
    ChinesePhoneticEncoder,
    EventDirection,
    EventAttentionScope,
    EventMarketReactionCalculator,
    EventNameDiscoveryService,
    EventNameDiscoveryConflictError,
    EventNameDiscoveryMode,
    EventShockAssessment,
    EventShockDimension,
    EventShockEvidence,
    EventShockStatus,
    EventNameDiscoverySpec,
    EventNameMatcher,
    EventNameMemberStatus,
    EventPhrase,
    EventTimingQuality,
    ExternalEventAnchor,
    MarketConfirmationMode,
    MarketReactionSnapshot,
    NameAssociationCaseAssessment,
    NameAssociationConclusion,
    ReactionBar,
    RelationCheckResult,
    SQLiteEventNameDiscoveryRepository,
    assess_evidence,
    map_event_to_trade_date,
)
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    AssetNameRecord,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_contracts import ResearchTaskSpec
from src.research_orchestration import (
    EventNameDiscoveryOrchestrationPipeline,
    EventNameDiscoveryRunRequest,
)


class FakeTaskFactory:
    def create(self, **kwargs):
        return ResearchTaskSpec(
            task_id="research:temporary",
            asset_id=kwargs["asset_id"],
            profile_id=kwargs["profile_id"],
            question_type=kwargs["question_type"],
            horizon=kwargs["horizon"],
            as_of_date=kwargs["as_of_date"],
            requested_outputs=kwargs["requested_outputs"],
        )


class EventNameEvidenceAndMatcherTest(unittest.TestCase):
    def test_external_evidence_gate_is_ready(self):
        result = assess_evidence()

        self.assertTrue(result.implementation_ready)
        self.assertEqual(result.blocking_reasons, ())

    def test_compositional_and_phonetic_matches_are_explainable(self):
        encoder = ChinesePhoneticEncoder(
            lambda text, initials: {
                ("哈尔斯", False): "haersi",
                ("哈里斯", False): "halisi",
                ("哈尔斯", True): "hes",
                ("哈里斯", True): "hls",
                ("川大智胜", False): "chuandazhisheng",
                ("川普胜选", False): "chuanpushengxuan",
                ("川大智胜", True): "cdzs",
                ("川普胜选", True): "cpsx",
            }.get((text, initials), "")
        )
        matcher = EventNameMatcher(
            encoder,
            max_phonetic_distance=2,
        )
        assets = (
            _asset("002253", "川大智胜"),
            _asset("002615", "哈尔斯"),
            AssetNameSnapshot(
                asset_id="asset:stock:old",
                standard_symbol="000001",
                name="川普旧名",
                effective_from="2020-01-01",
                effective_to="2024-01-01",
                aliases=(),
                profile_id="ordinary_company",
                source="unit-test",
            ),
        )
        phrases = (
            EventPhrase(
                phrase_id="trump-win",
                text="川普胜选",
                direction=EventDirection.POSITIVE,
                components=("川", "智胜"),
            ),
            EventPhrase(
                phrase_id="harris",
                text="哈里斯",
                direction=EventDirection.NEGATIVE,
                components=("哈里斯",),
            ),
        )

        result = matcher.match(
            phrases=phrases,
            assets=assets,
            event_date="2024-11-06",
        )

        trump = result["asset:stock:002253"][0]
        harris = result["asset:stock:002615"][0]
        self.assertEqual(trump.match_type.value, "compositional")
        self.assertEqual(trump.matched_components, ("川", "智胜"))
        self.assertIn("同时包含", trump.explanation)
        self.assertEqual(harris.match_type.value, "phonetic")
        self.assertEqual(harris.phonetic_distance, 2)
        self.assertNotIn("asset:stock:old", result)

    def test_explicit_three_letter_cross_language_acronym_matches(self):
        phrase = EventPhrase(
            phrase_id="large-language-model",
            text="大语言模型",
            direction=EventDirection.POSITIVE,
            components=("大语言模型",),
            initials="llm",
            channel=AttentionMatchChannel.CROSS_LANGUAGE_ACRONYM,
        )

        result = EventNameMatcher().match(
            phrases=(phrase,),
            assets=(_asset("06658", "溜溜梅"),),
            event_date="2026-06-15",
        )

        match = result["asset:stock:06658"][0]
        self.assertEqual(match.match_type.value, "pinyin_initials")
        self.assertEqual(
            match.attention_channel,
            AttentionMatchChannel.CROSS_LANGUAGE_ACRONYM,
        )
        self.assertEqual(match.stock_phonetic, "llm")


class BusinessRelationAssessmentTest(unittest.TestCase):
    def test_relation_status_requires_all_negative_checks(self):
        unrelated = _relation("asset:stock:002253")
        related_evidence = list(_relation_evidence())
        related_evidence[0] = BusinessRelationEvidence(
            check_type="business_scope",
            result=RelationCheckResult.PRESENT,
            detail="主营属于事件涉及产业",
            source_ref="source:business",
            available_at="2026-08-09T00:00:00+08:00",
        )
        related = BusinessRelationAssessment.evaluate(
            asset_id="asset:stock:related",
            evidence=tuple(related_evidence),
            assessed_at="2026-08-09T10:00:00+08:00",
        )
        insufficient = BusinessRelationAssessment.evaluate(
            asset_id="asset:stock:unknown",
            evidence=_relation_evidence()[:-1],
            assessed_at="2026-08-09T10:00:00+08:00",
        )

        self.assertEqual(
            unrelated.status,
            BusinessRelationStatus.CONFIRMED_UNRELATED,
        )
        self.assertEqual(
            related.status,
            BusinessRelationStatus.RELATED,
        )
        self.assertEqual(
            insufficient.status,
            BusinessRelationStatus.INSUFFICIENT_EVIDENCE,
        )


class AssetNameSnapshotLoaderTest(unittest.TestCase):
    def test_loader_uses_registered_name_history_without_backfill(self):
        master_data = MasterDataService(
            SQLiteMasterDataRepository(":memory:")
        )
        asset = Asset(
            asset_id="asset:stock:002253",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="002253",
            display_name="川大智胜",
            market="cn_a",
            exchange="sz",
            status="inactive",
        )
        master_data.register_asset(
            asset,
            source="unit-test",
            raw_symbol="002253",
        )
        master_data.register_asset_name(
            AssetNameRecord(
                asset_id=asset.asset_id,
                name="旧简称",
                name_type="official",
                effective_from="2020-01-01",
                effective_to="2023-12-31",
                source="exchange",
            )
        )
        master_data.register_asset_name(
            AssetNameRecord(
                asset_id=asset.asset_id,
                name="川大智胜",
                name_type="official",
                effective_from="2024-01-01",
                effective_to=None,
                source="exchange",
            )
        )
        loader = AssetNameSnapshotLoader(master_data=master_data)

        old = loader.load(as_of_date="2023-11-01")
        current = loader.load(as_of_date="2024-11-06")

        self.assertEqual(old[0].name, "旧简称")
        self.assertEqual(current[0].name, "川大智胜")
        self.assertEqual(current[0].effective_from, "2024-01-01")


class ConsensusFormationAssessmentTest(unittest.TestCase):
    def test_consensus_distinguishes_forming_and_confirmed(self):
        confirmed = _consensus("asset:confirmed")
        forming_dimensions = (
            ConsensusDimension.EVENT_SALIENCE,
            ConsensusDimension.GLOBAL_REACH,
            ConsensusDimension.CHINA_ATTENTION,
            ConsensusDimension.NARRATIVE_DISTANCE,
            ConsensusDimension.NARRATIVE_SIMPLICITY,
            ConsensusDimension.COORDINATION_FOCAL_POINT,
            ConsensusDimension.PRICE_ELASTICITY,
        )
        forming = ConsensusFormationAssessment.evaluate(
            asset_id="asset:forming",
            event_id="event:test",
            evidence=tuple(
                ConsensusConditionEvidence(
                    dimension=dimension,
                    result=RelationCheckResult.PRESENT,
                    detail="条件成立",
                    source_ref=f"source:{dimension.value}",
                    available_at="2026-08-09T09:00:00+08:00",
                )
                for dimension in forming_dimensions
            ),
            coverage_complete=True,
            assessed_at="2026-08-09T09:30:00+08:00",
        )

        self.assertEqual(
            confirmed.status,
            ConsensusFormationStatus.CONFIRMED,
        )
        self.assertEqual(
            forming.status,
            ConsensusFormationStatus.FORMING,
        )

    def test_corona_brand_is_attention_effect_not_sales_decline(self):
        case = NameAssociationCaseAssessment.evaluate(
            event_id="external-event:covid-2020",
            target_id="brand:corona-extra",
            target_name="Corona Extra",
            channel=AttentionMatchChannel.BRAND_NAME,
            observations=(
                AttentionOutcomeObservation(
                    target_id="brand:corona-extra",
                    target_type="brand",
                    target_name="Corona Extra",
                    event_id="external-event:covid-2020",
                    layer=AttentionOutcomeLayer.SEARCH_ATTENTION,
                    expected_direction=EventDirection.NEGATIVE,
                    result=RelationCheckResult.PRESENT,
                    observed_change=None,
                    unit="search_attention",
                    window_start="2020-01-01",
                    window_end="2020-03-31",
                    source_ref="source:search-trends",
                    available_at="2020-04-01T00:00:00+00:00",
                ),
                AttentionOutcomeObservation(
                    target_id="brand:corona-extra",
                    target_type="brand",
                    target_name="Corona Extra",
                    event_id="external-event:covid-2020",
                    layer=AttentionOutcomeLayer.SENTIMENT,
                    expected_direction=EventDirection.NEGATIVE,
                    result=RelationCheckResult.PRESENT,
                    observed_change=None,
                    unit="brand_buzz",
                    window_start="2020-01-01",
                    window_end="2020-03-31",
                    source_ref="source:yougov",
                    available_at="2020-04-01T00:00:00+00:00",
                ),
                AttentionOutcomeObservation(
                    target_id="brand:corona-extra",
                    target_type="brand",
                    target_name="Corona Extra",
                    event_id="external-event:covid-2020",
                    layer=AttentionOutcomeLayer.REALIZED_SALES,
                    expected_direction=EventDirection.NEGATIVE,
                    result=RelationCheckResult.ABSENT,
                    observed_change=Decimal("0.50"),
                    unit="weekly_sales_ratio",
                    window_start="2020-03-01",
                    window_end="2020-03-31",
                    source_ref="source:nielsen",
                    available_at="2020-04-15T00:00:00+00:00",
                ),
            ),
        )

        self.assertEqual(
            case.conclusion,
            NameAssociationConclusion.ATTENTION_ONLY,
        )


class EventMarketReactionCalculatorTest(unittest.TestCase):
    def test_event_time_maps_without_using_future_sessions(self):
        dates = (
            "2026-08-07",
            "2026-08-10",
            "2026-08-11",
        )
        preopen = map_event_to_trade_date(
            "2026-08-07T01:00:00+00:00",
            dates,
        )
        intraday = map_event_to_trade_date(
            "2026-08-07T04:00:00+00:00",
            dates,
        )
        postclose = map_event_to_trade_date(
            "2026-08-07T08:00:00+00:00",
            dates,
        )
        weekend = map_event_to_trade_date(
            "2026-08-08T04:00:00+00:00",
            dates,
        )

        self.assertEqual(preopen.trade_date, "2026-08-07")
        self.assertEqual(
            preopen.timing_quality,
            EventTimingQuality.PREOPEN,
        )
        self.assertEqual(
            intraday.timing_quality,
            EventTimingQuality.INTRADAY_AMBIGUOUS,
        )
        self.assertEqual(postclose.trade_date, "2026-08-10")
        self.assertEqual(weekend.trade_date, "2026-08-10")

    def test_reaction_calculates_abnormal_return_volume_and_reversal(self):
        start = date(2026, 7, 1)
        dates = [
            (start + timedelta(days=index)).isoformat()
            for index in range(28)
        ]
        event_date = dates[21]
        asset_bars = []
        benchmark_bars = []
        for index, value_date in enumerate(dates):
            asset_close = Decimal("10")
            benchmark_close = Decimal("100")
            if index == 21:
                asset_close = Decimal("11")
                benchmark_close = Decimal("101")
            elif index >= 26:
                asset_close = Decimal("10.5")
                benchmark_close = Decimal("101")
            asset_bars.append(
                ReactionBar(
                    value_date,
                    asset_close,
                    Decimal(100 + index * 2)
                    if index != 21
                    else Decimal("1000"),
                    "asset-bars",
                )
            )
            benchmark_bars.append(
                ReactionBar(
                    value_date,
                    benchmark_close,
                    Decimal(1000 + index),
                    "benchmark-bars",
                )
            )
        event = _event(
            first_public_at=(
                f"{event_date}T08:00:00+08:00"
            )
        )

        result = EventMarketReactionCalculator().calculate(
            asset_id="asset:stock:002253",
            event=event,
            asset_bars=tuple(asset_bars),
            benchmark_bars=tuple(benchmark_bars),
            trading_calendar=tuple(dates),
            calendar_id="unit-test-calendar",
            as_of_date=dates[-1],
        )

        self.assertEqual(result.event_trade_date, event_date)
        self.assertEqual(result.abnormal_return, Decimal("0.09"))
        self.assertGreater(result.volume_zscore, Decimal("2"))
        self.assertLess(
            result.reversal_abnormal_return,
            Decimal("-0.02"),
        )

    def test_reaction_rejects_suspension_instead_of_shifting_event(self):
        start = date(2026, 7, 1)
        dates = tuple(
            (start + timedelta(days=index)).isoformat()
            for index in range(28)
        )
        event_date = dates[21]
        asset_bars = tuple(
            ReactionBar(
                value_date,
                Decimal("10"),
                Decimal(100 + index),
                "asset-bars",
            )
            for index, value_date in enumerate(dates)
            if value_date != event_date
        )
        benchmark_bars = tuple(
            ReactionBar(
                value_date,
                Decimal("100"),
                Decimal(1000 + index),
                "benchmark-bars",
            )
            for index, value_date in enumerate(dates)
        )

        with self.assertRaisesRegex(ValueError, "suspended or missing"):
            EventMarketReactionCalculator().calculate(
                asset_id="asset:stock:002253",
                event=_event(
                    first_public_at=(
                        f"{event_date}T08:00:00+08:00"
                    )
                ),
                asset_bars=asset_bars,
                benchmark_bars=benchmark_bars,
                trading_calendar=dates,
                calendar_id="unit-test-calendar",
                as_of_date=dates[-1],
            )


class EventNameDiscoveryServiceTest(unittest.TestCase):
    def test_full_funnel_persists_candidate_and_all_exclusions(self):
        assets = (
            _asset("002253", "川大智胜"),
            _asset("300001", "川普胜选产业"),
            _asset("600000", "普通银行"),
        )
        event = _event()
        relations = {
            assets[0].asset_id: _relation(assets[0].asset_id),
            assets[1].asset_id: _relation(
                assets[1].asset_id,
                related=True,
            ),
        }
        reactions = {
            assets[0].asset_id: MarketReactionSnapshot(
                asset_id=assets[0].asset_id,
                event_id="external-event:trump-win-2024",
                event_first_public_at="2024-11-06T08:00:00+08:00",
                event_trade_date="2024-11-06",
                observed_through="2024-11-11",
                abnormal_return=Decimal("0.10"),
                volume_zscore=Decimal("4"),
                reversal_abnormal_return=Decimal("-0.04"),
                timing_quality=EventTimingQuality.PREOPEN,
                limit_state="limit_up",
                calendar_id="unit-test-calendar",
                calculation_version="event_name_reaction_v1",
                source_refs=("event-source", "market-bars"),
            )
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteEventNameDiscoveryRepository(
                Path(directory) / "discovery.sqlite3"
            )
            service = EventNameDiscoveryService(
                task_factory=FakeTaskFactory(),
                repository=repository,
            )

            result = service.run(
                event=event,
                spec=EventNameDiscoverySpec(
                    as_of_date="2024-11-11",
                    knowledge_cutoff_at=(
                        "2024-11-11T23:59:59+08:00"
                    ),
                ),
                assets=assets,
                relation_assessments=relations,
                catalyst_assessments={
                    assets[0].asset_id: _catalyst(
                        assets[0].asset_id
                    )
                },
                consensus_assessments={
                    assets[0].asset_id: _consensus(
                        assets[0].asset_id
                    )
                },
                reactions=reactions,
            )
            restored = repository.get(result.run_id)
            self.assertEqual(
                repository.save(result).to_dict(),
                result.to_dict(),
            )
            with self.assertRaises(EventNameDiscoveryConflictError):
                repository.save(
                    replace(
                        result,
                        event=replace(
                            result.event,
                            title="同一ID下的冲突标题",
                        ),
                    )
                )

        by_symbol = {
            item.standard_symbol: item for item in result.outcomes
        }
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            by_symbol["002253"].status,
            EventNameMemberStatus.CANDIDATE,
        )
        self.assertEqual(
            by_symbol["300001"].status,
            EventNameMemberStatus.RELATED_EXCLUDED,
        )
        self.assertEqual(
            by_symbol["600000"].status,
            EventNameMemberStatus.NO_LANGUAGE_MATCH,
        )
        self.assertEqual(
            result.candidates[0].selected_track,
            "positive_attention_echo",
        )
        self.assertIn(
            "reversal_watch",
            by_symbol["002253"].matched_tracks,
        )
        self.assertFalse(result.is_buy_recommendation)
        self.assertEqual(restored.to_dict(), result.to_dict())

    def test_predictive_mode_freezes_candidate_without_outcome_data(self):
        asset = _asset("002253", "川大智胜")
        result = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        ).run(
            event=_event(),
            spec=EventNameDiscoverySpec(
                as_of_date="2024-11-06",
                knowledge_cutoff_at=(
                    "2024-11-06T09:00:00+08:00"
                ),
                mode=EventNameDiscoveryMode.PREDICTIVE,
            ),
            assets=(asset,),
            relation_assessments={
                asset.asset_id: _relation(asset.asset_id)
            },
            catalyst_assessments={
                asset.asset_id: _catalyst(asset.asset_id)
            },
            consensus_assessments={
                asset.asset_id: _consensus(asset.asset_id)
            },
            reactions={},
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertIsNone(result.candidates[0].reaction)
        self.assertEqual(
            result.outcomes[0].matched_tracks,
            ("predicted_positive_attention",),
        )

    def test_global_scope_gate_rejects_domestic_event_source(self):
        asset = _asset("002253", "川大智胜")
        with self.assertRaisesRegex(ValueError, "global or cross-border"):
            EventNameDiscoveryService(
                task_factory=FakeTaskFactory()
            ).run(
                event=_event(),
                spec=EventNameDiscoverySpec(
                    as_of_date="2024-11-06",
                    knowledge_cutoff_at=(
                        "2024-11-06T09:00:00+08:00"
                    ),
                    mode=EventNameDiscoveryMode.PREDICTIVE,
                    require_global_scope=True,
                ),
                assets=(asset,),
                relation_assessments={
                    asset.asset_id: _relation(asset.asset_id)
                },
                catalyst_assessments={
                    asset.asset_id: _catalyst(asset.asset_id)
                },
                consensus_assessments={
                    asset.asset_id: _consensus(asset.asset_id)
                },
                reactions={},
            )

        global_result = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        ).run(
            event=replace(
                _event(),
                attention_scope=EventAttentionScope.GLOBAL_EVENT,
            ),
            spec=EventNameDiscoverySpec(
                as_of_date="2024-11-06",
                knowledge_cutoff_at=(
                    "2024-11-06T09:00:00+08:00"
                ),
                mode=EventNameDiscoveryMode.PREDICTIVE,
                require_global_scope=True,
            ),
            assets=(asset,),
            relation_assessments={
                asset.asset_id: _relation(asset.asset_id)
            },
            catalyst_assessments={
                asset.asset_id: _catalyst(asset.asset_id)
            },
            consensus_assessments={
                asset.asset_id: _consensus(asset.asset_id)
            },
            reactions={},
        )
        self.assertEqual(len(global_result.candidates), 1)

    def test_attention_shock_gate_rejects_generic_global_topic(self):
        asset = _asset("002253", "川大智胜")
        event = replace(
            _event(),
            attention_scope=EventAttentionScope.GLOBAL_EVENT,
        )
        spec = EventNameDiscoverySpec(
            as_of_date="2024-11-06",
            knowledge_cutoff_at="2024-11-06T09:00:00+08:00",
            mode=EventNameDiscoveryMode.PREDICTIVE,
            require_global_scope=True,
            require_event_shock=True,
        )
        arguments = {
            "event": event,
            "spec": spec,
            "assets": (asset,),
            "relation_assessments": {
                asset.asset_id: _relation(asset.asset_id)
            },
            "catalyst_assessments": {
                asset.asset_id: _catalyst(asset.asset_id)
            },
            "consensus_assessments": {
                asset.asset_id: _consensus(asset.asset_id)
            },
            "reactions": {},
        }
        service = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        )
        with self.assertRaisesRegex(ValueError, "shock assessment"):
            service.run(**arguments)

        result = service.run(
            **arguments,
            event_shock_assessment=_shock(event.event_id),
        )
        self.assertEqual(len(result.candidates), 1)

    def test_confirmation_mode_can_require_both_price_and_volume(self):
        asset = _asset("002253", "川大智胜")
        result = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        ).run(
            event=_event(),
            spec=EventNameDiscoverySpec(
                as_of_date="2024-11-11",
                knowledge_cutoff_at=(
                    "2024-11-11T23:59:59+08:00"
                ),
                confirmation_mode=(
                    MarketConfirmationMode.PRICE_AND_VOLUME
                ),
            ),
            assets=(asset,),
            relation_assessments={
                asset.asset_id: _relation(asset.asset_id)
            },
            catalyst_assessments={
                asset.asset_id: _catalyst(asset.asset_id)
            },
            consensus_assessments={
                asset.asset_id: _consensus(asset.asset_id)
            },
            reactions={
                asset.asset_id: MarketReactionSnapshot(
                    asset_id=asset.asset_id,
                    event_id="external-event:trump-win-2024",
                    event_first_public_at="2024-11-06T08:00:00+08:00",
                    event_trade_date="2024-11-06",
                    observed_through="2024-11-11",
                    abnormal_return=Decimal("0.1"),
                    volume_zscore=Decimal("1.5"),
                    reversal_abnormal_return=Decimal("-0.03"),
                    timing_quality=EventTimingQuality.PREOPEN,
                    limit_state="tradable_close",
                    calendar_id="unit-test-calendar",
                    calculation_version="event_name_reaction_v1",
                    source_refs=("market-bars",),
                )
            },
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.outcomes[0].status,
            EventNameMemberStatus.MARKET_REACTION_NOT_CONFIRMED,
        )

    def test_concurrent_alternative_catalyst_excludes_name_candidate(self):
        asset = _asset("300894", "火星人")
        event = ExternalEventAnchor(
            event_id="external-event:spacex-ipo",
            title="SpaceX上市",
            event_kind="space",
            occurred_at="2026-06-12T23:47:00+08:00",
            first_public_at="2026-06-12T23:47:00+08:00",
            source="unit-test",
            source_url="https://example.com/spacex",
            phrases=(
                EventPhrase(
                    phrase_id="mars",
                    text="火星",
                    direction=EventDirection.POSITIVE,
                    components=("火星",),
                ),
            ),
            evidence_refs=("https://example.com/spacex",),
        )
        result = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        ).run(
            event=event,
            spec=EventNameDiscoverySpec(
                as_of_date="2026-06-20",
                knowledge_cutoff_at="2026-06-20T23:59:59+08:00",
            ),
            assets=(asset,),
            relation_assessments={
                asset.asset_id: _relation(asset.asset_id)
            },
            catalyst_assessments={
                asset.asset_id: _catalyst(
                    asset.asset_id,
                    contaminated=True,
                )
            },
            consensus_assessments={
                asset.asset_id: _consensus(
                    asset.asset_id,
                    event_id="external-event:spacex-ipo",
                )
            },
            reactions={
                asset.asset_id: MarketReactionSnapshot(
                    asset_id=asset.asset_id,
                    event_id="external-event:spacex-ipo",
                    event_first_public_at="2026-06-12T23:47:00+08:00",
                    event_trade_date="2026-06-15",
                    observed_through="2026-06-20",
                    abnormal_return=Decimal("0.1"),
                    volume_zscore=Decimal("4"),
                    reversal_abnormal_return=Decimal("-0.03"),
                    timing_quality=EventTimingQuality.POSTCLOSE,
                    limit_state="tradable_close",
                    calendar_id="unit-test-calendar",
                    calculation_version="event_name_reaction_v1",
                    source_refs=("market-bars",),
                )
            },
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.outcomes[0].status,
            EventNameMemberStatus.ALTERNATIVE_CATALYST_PRESENT,
        )

    def test_mixed_catalyst_overlay_can_remain_research_candidate(self):
        asset = _asset("06658", "溜溜梅")
        event_id = "external-event:liuliumei-ipo"
        event = ExternalEventAnchor(
            event_id=event_id,
            title="溜溜梅港股上市",
            event_kind="ipo",
            occurred_at="2026-06-15T09:30:00+08:00",
            first_public_at="2026-06-15T09:30:00+08:00",
            source="unit-test",
            source_url="https://example.com/llm-ipo",
            phrases=(
                EventPhrase(
                    phrase_id="llm",
                    text="大语言模型",
                    direction=EventDirection.POSITIVE,
                    components=("大语言模型",),
                    initials="llm",
                    channel=(
                        AttentionMatchChannel.CROSS_LANGUAGE_ACRONYM
                    ),
                ),
            ),
            evidence_refs=("https://example.com/llm-ipo",),
        )
        catalyst = AlternativeCatalystAssessment.evaluate(
            asset_id=asset.asset_id,
            evidence=tuple(
                AlternativeCatalystEvidence(
                    check_type=check_type,
                    result=(
                        RelationCheckResult.PRESENT
                        if check_type == "corporate_action"
                        else RelationCheckResult.ABSENT
                    ),
                    detail="IPO是直接催化",
                    source_ref="source:ipo",
                    available_at="2026-06-15T09:30:00+08:00",
                )
                for check_type in REQUIRED_CATALYST_CHECKS
            ),
            window_start_at="2026-06-01T00:00:00+08:00",
            window_end_at="2026-06-15T16:00:00+08:00",
            queried_sources=("ipo_filings", "financial_media"),
            query_refs=("source:ipo", "source:media"),
            coverage_complete=True,
            assessed_at="2026-06-15T18:00:00+08:00",
        )
        attribution = AttentionAttributionAssessment(
            asset_id=asset.asset_id,
            event_id=event_id,
            mode=AttentionAttributionMode.MIXED_CATALYST_OVERLAY,
            status=AttentionAttributionStatus.PLAUSIBLE,
            direct_catalysts=("initial_public_offering",),
            comparison_refs=(
                "peer-ipo:mixue",
                "peer-ipo:mingminghenmang",
            ),
            observed_return=Decimal("1.9371"),
            baseline_expected_return=None,
            incremental_return=None,
            methodology="peer IPO comparison pending formal panel",
            available_at="2026-06-18T12:00:00+08:00",
        )
        result = EventNameDiscoveryService(
            task_factory=FakeTaskFactory()
        ).run(
            event=event,
            spec=EventNameDiscoverySpec(
                as_of_date="2026-06-18",
                knowledge_cutoff_at="2026-06-18T23:59:59+08:00",
                retrospective_label=True,
            ),
            assets=(asset,),
            relation_assessments={
                asset.asset_id: _relation(asset.asset_id)
            },
            catalyst_assessments={asset.asset_id: catalyst},
            consensus_assessments={
                asset.asset_id: _consensus(
                    asset.asset_id,
                    event_id=event_id,
                )
            },
            attribution_assessments={
                asset.asset_id: attribution
            },
            reactions={
                asset.asset_id: MarketReactionSnapshot(
                    asset_id=asset.asset_id,
                    event_id=event_id,
                    event_first_public_at=event.first_public_at,
                    event_trade_date="2026-06-15",
                    observed_through="2026-06-16",
                    abnormal_return=Decimal("1.9371"),
                    volume_zscore=Decimal("4"),
                    reversal_abnormal_return=Decimal("-0.0156"),
                    timing_quality=EventTimingQuality.PREOPEN,
                    limit_state="tradable_close",
                    calendar_id="hkex-calendar",
                    calculation_version="event_name_reaction_v1",
                    source_refs=("hkex:ipo",),
                )
            },
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertIn(
            "mixed_catalyst_attention_overlay",
            result.outcomes[0].matched_tracks,
        )
        self.assertEqual(
            result.candidates[0].attribution.status,
            AttentionAttributionStatus.PLAUSIBLE,
        )

    def test_future_reaction_is_rejected(self):
        asset = _asset("002253", "川大智胜")
        with self.assertRaisesRegex(ValueError, "future market reaction"):
            EventNameDiscoveryService(
                task_factory=FakeTaskFactory()
            ).run(
                event=_event(),
                spec=EventNameDiscoverySpec(
                    as_of_date="2024-11-06",
                    knowledge_cutoff_at=(
                        "2024-11-06T23:59:59+08:00"
                    ),
                ),
                assets=(asset,),
                relation_assessments={
                    asset.asset_id: _relation(asset.asset_id)
                },
                catalyst_assessments={
                    asset.asset_id: _catalyst(asset.asset_id)
                },
                consensus_assessments={
                    asset.asset_id: _consensus(asset.asset_id)
                },
                reactions={
                    asset.asset_id: MarketReactionSnapshot(
                        asset_id=asset.asset_id,
                        event_id="external-event:trump-win-2024",
                        event_first_public_at="2024-11-06T08:00:00+08:00",
                        event_trade_date="2024-11-06",
                        observed_through="2024-11-07",
                        abnormal_return=Decimal("0.1"),
                        volume_zscore=Decimal("3"),
                        reversal_abnormal_return=None,
                        timing_quality=EventTimingQuality.PREOPEN,
                        limit_state="tradable_close",
                        calendar_id="unit-test-calendar",
                        calculation_version="event_name_reaction_v1",
                        source_refs=("test",),
                    )
                },
            )

    def test_precomputed_reaction_must_match_event_identity(self):
        asset = _asset("002253", "川大智胜")
        reaction = MarketReactionSnapshot(
            asset_id=asset.asset_id,
            event_id="external-event:wrong",
            event_first_public_at="2024-11-06T08:00:00+08:00",
            event_trade_date="2024-11-06",
            observed_through="2024-11-11",
            abnormal_return=Decimal("0.1"),
            volume_zscore=Decimal("3"),
            reversal_abnormal_return=Decimal("-0.03"),
            timing_quality=EventTimingQuality.PREOPEN,
            limit_state="tradable_close",
            calendar_id="unit-test-calendar",
            calculation_version="event_name_reaction_v1",
            source_refs=("market-bars",),
        )
        with self.assertRaisesRegex(ValueError, "event mismatch"):
            EventNameDiscoveryService(
                task_factory=FakeTaskFactory()
            ).run(
                event=_event(),
                spec=EventNameDiscoverySpec(
                    as_of_date="2024-11-11",
                    knowledge_cutoff_at=(
                        "2024-11-11T23:59:59+08:00"
                    ),
                ),
                assets=(asset,),
                relation_assessments={
                    asset.asset_id: _relation(asset.asset_id)
                },
                catalyst_assessments={
                    asset.asset_id: _catalyst(asset.asset_id)
                },
                consensus_assessments={
                    asset.asset_id: _consensus(asset.asset_id)
                },
                reactions={asset.asset_id: reaction},
            )

    def test_orchestration_starts_exploration_for_each_candidate(self):
        asset = _asset("002253", "川大智胜")
        reaction = MarketReactionSnapshot(
            asset_id=asset.asset_id,
            event_id="external-event:trump-win-2024",
            event_first_public_at="2024-11-06T08:00:00+08:00",
            event_trade_date="2024-11-06",
            observed_through="2024-11-11",
            abnormal_return=Decimal("0.1"),
            volume_zscore=Decimal("3"),
            reversal_abnormal_return=Decimal("-0.03"),
            timing_quality=EventTimingQuality.PREOPEN,
            limit_state="limit_up",
            calendar_id="unit-test-calendar",
            calculation_version="event_name_reaction_v1",
            source_refs=("market-bars",),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteEventNameDiscoveryRepository(
                Path(directory) / "handoff.sqlite3"
            )
            pipeline = EventNameDiscoveryOrchestrationPipeline(
                discovery_service=EventNameDiscoveryService(
                    task_factory=FakeTaskFactory(),
                    repository=repository,
                )
            )
            result = pipeline.run(
                EventNameDiscoveryRunRequest(
                    event=_event(),
                    spec=EventNameDiscoverySpec(
                        as_of_date="2024-11-11",
                        knowledge_cutoff_at=(
                            "2024-11-11T23:59:59+08:00"
                        ),
                    ),
                    assets=(asset,),
                    relation_assessments={
                        asset.asset_id: _relation(asset.asset_id)
                    },
                    catalyst_assessments={
                        asset.asset_id: _catalyst(asset.asset_id)
                    },
                    consensus_assessments={
                        asset.asset_id: _consensus(asset.asset_id)
                    },
                    precomputed_reactions={
                        asset.asset_id: reaction
                    },
                )
            )
            handoffs = repository.list_handoffs(
                result.discovery_run.run_id
            )

        self.assertEqual(len(result.exploration_sessions), 1)
        session = result.exploration_sessions[0]
        self.assertEqual(
            {item.hypothesis_id for item in session.hypotheses},
            {
                "name_attention_spillover",
                "economic_unrelatedness",
                "consensus_coordination",
                "alternative_catalysts_cleared",
                "name_effect_attribution",
                "temporary_price_pressure",
            },
        )
        self.assertEqual(result.reaction_preparation_errors, ())
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["asset_id"], asset.asset_id)


def _asset(symbol, name):
    return AssetNameSnapshot(
        asset_id=f"asset:stock:{symbol}",
        standard_symbol=symbol,
        name=name,
        effective_from="2020-01-01",
        effective_to=None,
        aliases=(),
        profile_id="ordinary_company",
        source="unit-test",
    )


def _event(first_public_at="2024-11-06T08:00:00+08:00"):
    return ExternalEventAnchor(
        event_id="external-event:trump-win-2024",
        title="美国大选结果显示特朗普胜选",
        event_kind="election",
        occurred_at="2024-11-06T07:30:00+08:00",
        first_public_at=first_public_at,
        source="unit-test",
        source_url="https://example.com/event",
        phrases=(
            EventPhrase(
                phrase_id="trump-win",
                text="川普胜选",
                direction=EventDirection.POSITIVE,
                components=("川", "智胜"),
            ),
        ),
        evidence_refs=("https://example.com/event",),
    )


def _relation_evidence():
    return tuple(
        BusinessRelationEvidence(
            check_type=check_type,
            result=RelationCheckResult.ABSENT,
            detail=f"{check_type}未发现关系",
            source_ref=f"source:{check_type}",
            available_at="2024-11-06T00:00:00+08:00",
        )
        for check_type in REQUIRED_RELATION_CHECKS
    )


def _relation(asset_id, *, related=False):
    evidence = list(_relation_evidence())
    if related:
        evidence[0] = BusinessRelationEvidence(
            check_type="business_scope",
            result=RelationCheckResult.PRESENT,
            detail="主营与事件存在真实关系",
            source_ref="source:business",
            available_at="2024-11-06T00:00:00+08:00",
        )
    return BusinessRelationAssessment.evaluate(
        asset_id=asset_id,
        evidence=tuple(evidence),
        assessed_at="2024-11-06T09:00:00+08:00",
    )


def _catalyst(asset_id, *, contaminated=False):
    evidence = [
        AlternativeCatalystEvidence(
            check_type=check_type,
            result=RelationCheckResult.ABSENT,
            detail=f"{check_type}未发现替代催化",
            source_ref=f"source:catalyst:{check_type}",
            available_at="2024-11-06T00:00:00+08:00",
        )
        for check_type in REQUIRED_CATALYST_CHECKS
    ]
    if contaminated:
        evidence[1] = AlternativeCatalystEvidence(
            check_type="industry_theme",
            result=RelationCheckResult.PRESENT,
            detail="同期存在机器人题材催化",
            source_ref="source:robot-theme",
            available_at="2024-11-06T00:00:00+08:00",
        )
    return AlternativeCatalystAssessment.evaluate(
        asset_id=asset_id,
        evidence=tuple(evidence),
        window_start_at="2024-11-01T00:00:00+08:00",
        window_end_at="2024-11-06T08:00:00+08:00",
        queried_sources=("company_disclosures", "market_news"),
        query_refs=("query:company", "query:market"),
        coverage_complete=True,
        assessed_at="2024-11-06T09:00:00+08:00",
    )


def _consensus(
    asset_id,
    *,
    event_id="external-event:trump-win-2024",
):
    return ConsensusFormationAssessment.evaluate(
        asset_id=asset_id,
        event_id=event_id,
        evidence=tuple(
            ConsensusConditionEvidence(
                dimension=dimension,
                result=RelationCheckResult.PRESENT,
                detail=f"{dimension.value}条件成立",
                source_ref=f"source:consensus:{dimension.value}",
                available_at="2024-11-06T08:30:00+08:00",
            )
            for dimension in ConsensusDimension
        ),
        coverage_complete=True,
        assessed_at="2024-11-06T09:00:00+08:00",
    )


def _shock(event_id):
    return EventShockAssessment.evaluate(
        event_id=event_id,
        evidence=tuple(
            EventShockEvidence(
                dimension=dimension,
                result=RelationCheckResult.PRESENT,
                detail=f"{dimension.value}条件成立",
                source_ref=f"source:shock:{dimension.value}",
                available_at="2024-11-06T08:30:00+08:00",
            )
            for dimension in EventShockDimension
        ),
        coverage_complete=True,
        assessed_at="2024-11-06T08:45:00+08:00",
    )


if __name__ == "__main__":
    unittest.main()
