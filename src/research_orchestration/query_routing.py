"""Explicit routing from exploration questions to public services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlparse

from src.benchmark_analysis import (
    BenchmarkComponentUnavailableError,
    BenchmarkCoverageError,
    BenchmarkDefinitionIncompleteError,
)
from src.data_access import (
    DATASET_OPEN_FUND_NAV,
    AssetInput,
    DataAccessFetchError,
    DataAccessRequest,
)
from src.peer_analysis import PeerComparisonStatus
from src.research_workflow import PROFILE_OPEN_FUND
from src.user_space import UserContext

from .capabilities import ResearchCapabilityRegistry
from .exploration_models import (
    EvidenceTier,
    ExplorationGapKind,
    ExplorationQueryGap,
    ExplorationQuestion,
    ExplorationTrack,
    QuestionStatus,
    ResearchExplorationSession,
    ResearchFinding,
    ResearchFindingKind,
)
from .exploration_persistence import (
    PersistentResearchExplorationService,
)
from .query_models import (
    ResearchQueryExecutionResult,
    ResearchQueryOutcome,
    ResearchQueryPlan,
    ResearchQueryResult,
    ResearchQueryRoute,
)


CAPABILITY_DATASET_QUERY = "dataset_query"
CAPABILITY_OFFICIAL_DOCUMENT_QUERY = "official_document_query"
CAPABILITY_BENCHMARK_QUERY = "benchmark_comparison_query"
CAPABILITY_EVENT_QUERY = "event_lookup"
CAPABILITY_INDUSTRY_QUERY = "industry_assignment_query"
CAPABILITY_PEER_QUERY = "peer_comparison_query"
CAPABILITY_USER_CONTEXT_QUERY = "user_context_query"
CAPABILITY_PORTFOLIO_QUERY = "portfolio_snapshot_query"
CAPABILITY_EXTERNAL_OFFICIAL_WEB = (
    "external_official_web_research"
)


_ROUTE_CAPABILITIES = {
    ResearchQueryRoute.DATA_ACCESS: CAPABILITY_DATASET_QUERY,
    ResearchQueryRoute.RESEARCH_EVIDENCE: (
        CAPABILITY_OFFICIAL_DOCUMENT_QUERY
    ),
    ResearchQueryRoute.BENCHMARK_ANALYSIS: (
        CAPABILITY_BENCHMARK_QUERY
    ),
    ResearchQueryRoute.EVENT_KNOWLEDGE: CAPABILITY_EVENT_QUERY,
    ResearchQueryRoute.INDUSTRY_ANALYSIS: (
        CAPABILITY_INDUSTRY_QUERY
    ),
    ResearchQueryRoute.PEER_ANALYSIS: CAPABILITY_PEER_QUERY,
    ResearchQueryRoute.USER_RESEARCH_STATE: (
        CAPABILITY_USER_CONTEXT_QUERY
    ),
    ResearchQueryRoute.PORTFOLIO_RISK: (
        CAPABILITY_PORTFOLIO_QUERY
    ),
    ResearchQueryRoute.EXTERNAL_OFFICIAL_WEB: (
        CAPABILITY_EXTERNAL_OFFICIAL_WEB
    ),
}


@dataclass(frozen=True)
class ResearchQueryHandlerOutput:
    outcome: ResearchQueryOutcome
    summary: str
    evidence_refs: tuple[str, ...] = ()
    evidence_tier: EvidenceTier = EvidenceTier.UNKNOWN
    metadata: dict[str, Any] | None = None
    gap_kind: ExplorationGapKind | None = None
    gap_code: str | None = None
    gap_message: str | None = None


class ResearchQueryHandler(Protocol):
    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput: ...


class ResearchQueryRouter:
    def __init__(
        self,
        handlers: dict[
            ResearchQueryRoute,
            ResearchQueryHandler,
        ],
    ) -> None:
        self.handlers = dict(handlers)

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        handler = self.handlers.get(plan.route)
        if handler is None:
            return _unsupported_output(
                plan,
                "No public query handler is registered for route.",
            )
        try:
            return handler.execute(
                plan=plan,
                session=session,
                context=context,
            )
        except (TimeoutError, ConnectionError) as exc:
            return _remote_failure_output(
                plan,
                f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.FAILED,
                summary=f"Public query failed: {exc}",
                metadata={"error_type": type(exc).__name__},
                gap_kind=ExplorationGapKind.EXECUTION,
                gap_code=f"{plan.route.value}_query_failed",
                gap_message=str(exc),
            )


class DataAccessQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del context
        parameters = plan.parameters
        requested_end = _required_str(
            parameters,
            "end_date",
        )
        effective_end = _effective_end_date(
            requested_end,
            plan.as_of_date,
        )
        try:
            response = self.service.get(
                DataAccessRequest(
                    asset=AssetInput(
                        asset_id=session.task.asset_id
                    ),
                    dataset=_required_str(
                        parameters,
                        "dataset",
                    ),
                    start_date=_required_str(
                        parameters,
                        "start_date",
                    ),
                    end_date=effective_end,
                    as_of_date=plan.as_of_date,
                    allow_remote=_bool(
                        parameters,
                        "allow_remote",
                        True,
                    ),
                    allow_stale_on_error=True,
                    remote_sources=_string_tuple(
                        parameters.get("remote_sources", ())
                    ),
                    provider_timeout_seconds=_float(
                        parameters,
                        "provider_timeout_seconds",
                        30.0,
                    ),
                )
            )
        except DataAccessFetchError as exc:
            return _remote_failure_output(plan, str(exc))
        metadata = {
            "dataset": response.dataset,
            "effective_end_date": effective_end,
            "record_count": len(response.records),
            "sources": list(response.sources),
            "complete": response.complete,
            "stale": response.stale,
            "fetched_remote": response.fetched_remote,
            "provider_errors": response.metadata.get(
                "provider_errors",
                {},
            ),
            "empty_sources": response.metadata.get(
                "empty_sources",
                [],
            ),
        }
        if not response.records:
            provider_errors = metadata["provider_errors"]
            if response.error or provider_errors:
                return _remote_failure_output(
                    plan,
                    response.error
                    or "All configured data sources failed.",
                    metadata=metadata,
                )
            return _business_empty_output(
                plan,
                "Public data service returned no business records.",
                metadata=metadata,
            )
        evidence_ref = _query_evidence_ref(plan)
        if response.complete and not response.stale:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary=(
                    f"Public data service returned "
                    f"{len(response.records)} records."
                ),
                evidence_refs=(evidence_ref,),
                evidence_tier=EvidenceTier.DERIVED,
                metadata=metadata,
            )
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.PARTIAL,
            summary=(
                f"Public data service returned "
                f"{len(response.records)} partial or stale records."
            ),
            evidence_refs=(evidence_ref,),
            evidence_tier=EvidenceTier.DERIVED,
            metadata=metadata,
            gap_kind=ExplorationGapKind.REMOTE_FAILURE,
            gap_code=f"{plan.route.value}_partial",
            gap_message=(
                response.error
                or "Coverage is incomplete or stale."
            ),
        )


class ResearchEvidenceQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del context
        parameters = plan.parameters
        effective_end = _effective_end_date(
            _required_str(parameters, "end_date"),
            plan.as_of_date,
        )
        bundle = self.service.get_bundle(
            asset_id=session.task.asset_id,
            start_date=_required_str(
                parameters,
                "start_date",
            ),
            end_date=effective_end,
            as_of_date=plan.as_of_date,
            allow_remote=_bool(
                parameters,
                "allow_remote",
                True,
            ),
            disclosure_sources=_string_tuple(
                parameters.get("disclosure_sources", ())
            ),
            research_sources=_string_tuple(
                parameters.get("research_sources", ())
            ),
            provider_timeout_seconds=_float(
                parameters,
                "provider_timeout_seconds",
                30.0,
            ),
        )
        metadata = {
            "evidence_count": len(bundle.evidence),
            "event_count": len(bundle.events),
            "document_states": [
                {
                    "document_id": item.document_id,
                    "status": item.status.value,
                    "error": item.error,
                }
                for item in bundle.document_states
            ],
            "sources": list(bundle.sources),
            "complete": bundle.complete,
            "errors": bundle.errors,
            "effective_end_date": effective_end,
        }
        if not bundle.evidence:
            if bundle.errors:
                return _remote_failure_output(
                    plan,
                    "Official evidence sources were unavailable.",
                    metadata=metadata,
                )
            return _business_empty_output(
                plan,
                "No official evidence matched the requested range.",
                metadata=metadata,
            )
        refs = tuple(
            item.evidence_id for item in bundle.evidence
        )
        evidence_tier = _evidence_bundle_tier(
            bundle.evidence
        )
        if bundle.complete:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary=(
                    f"Found {len(bundle.evidence)} evidence items "
                    f"and {len(bundle.events)} events."
                ),
                evidence_refs=refs,
                evidence_tier=evidence_tier,
                metadata=metadata,
            )
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.PARTIAL,
            summary=(
                f"Found {len(bundle.evidence)} evidence items, "
                "but one or more sources were incomplete."
            ),
            evidence_refs=refs,
            evidence_tier=evidence_tier,
            metadata=metadata,
            gap_kind=ExplorationGapKind.REMOTE_FAILURE,
            gap_code="research_evidence_partial",
            gap_message="Evidence bundle contains source errors.",
        )


class BenchmarkQueryHandler:
    def __init__(
        self,
        service: object,
        *,
        open_fund_data_access: object | None = None,
        open_fund_research: object | None = None,
        open_fund_resolver: object | None = None,
    ) -> None:
        self.service = service
        self.open_fund_data_access = open_fund_data_access
        self.open_fund_research = open_fund_research
        self.open_fund_resolver = open_fund_resolver

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del context
        parameters = plan.parameters
        if session.task.profile_id == PROFILE_OPEN_FUND:
            return self._execute_open_fund(
                plan=plan,
                session=session,
            )
        try:
            result = self.service.compare(
                asset_id=session.task.asset_id,
                start_date=_required_str(
                    parameters,
                    "start_date",
                ),
                end_date=_required_str(
                    parameters,
                    "end_date",
                ),
                as_of_date=plan.as_of_date,
                benchmark_asset_id=_optional_str(
                    parameters.get("benchmark_asset_id")
                ),
                allow_remote=_bool(
                    parameters,
                    "allow_remote",
                    True,
                ),
                provider_timeout_seconds=_float(
                    parameters,
                    "provider_timeout_seconds",
                    30.0,
                ),
            )
        except BenchmarkComponentUnavailableError as exc:
            return _remote_failure_output(plan, str(exc))
        except DataAccessFetchError as exc:
            return _remote_failure_output(plan, str(exc))
        except BenchmarkCoverageError as exc:
            return _business_empty_output(plan, str(exc))
        except BenchmarkDefinitionIncompleteError as exc:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.FAILED,
                summary=str(exc),
                gap_kind=ExplorationGapKind.EXECUTION,
                gap_code="benchmark_definition_incomplete",
                gap_message=str(exc),
            )
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.SUCCESS,
            summary=(
                f"Compared asset with benchmark "
                f"{result.benchmark_asset_id} across "
                f"{len(result.aligned.dates)} observations."
            ),
            evidence_refs=(_query_evidence_ref(plan),),
            evidence_tier=EvidenceTier.DERIVED,
            metadata={
                "benchmark_asset_id": result.benchmark_asset_id,
                "benchmark_kind": result.benchmark_kind.value,
                "observation_count": len(result.aligned.dates),
                "metric_count": len(result.metrics),
            },
        )

    def _execute_open_fund(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
    ) -> ResearchQueryHandlerOutput:
        if (
            self.open_fund_research is None
            or self.open_fund_data_access is None
            or self.open_fund_resolver is None
        ):
            return _unsupported_output(
                plan,
                "Open-fund composite benchmark services are "
                "not registered.",
            )
        parameters = plan.parameters
        end_date = _effective_end_date(
            _required_str(parameters, "end_date"),
            plan.as_of_date,
        )
        try:
            nav_response = self.open_fund_data_access.get(
                DataAccessRequest(
                    asset=AssetInput(
                        asset_id=session.task.asset_id
                    ),
                    dataset=DATASET_OPEN_FUND_NAV,
                    start_date=_required_str(
                        parameters,
                        "start_date",
                    ),
                    end_date=end_date,
                    as_of_date=plan.as_of_date,
                    allow_remote=_bool(
                        parameters,
                        "allow_remote",
                        True,
                    ),
                    allow_stale_on_error=True,
                    provider_timeout_seconds=_float(
                        parameters,
                        "provider_timeout_seconds",
                        30.0,
                    ),
                )
            )
        except DataAccessFetchError as exc:
            return _remote_failure_output(plan, str(exc))
        if not nav_response.records:
            provider_errors = nav_response.metadata.get(
                "provider_errors",
                {},
            )
            if nav_response.error or provider_errors:
                return _remote_failure_output(
                    plan,
                    nav_response.error
                    or "Open-fund NAV source is unavailable.",
                    metadata={
                        "provider_errors": provider_errors,
                    },
                )
            return _business_empty_output(
                plan,
                "Open-fund NAV returned no business records.",
            )
        profile = (
            self.open_fund_research
            .get_open_fund_profile_snapshot(
                asset_id=session.task.asset_id,
                as_of_date=plan.as_of_date,
            )
        )
        if (
            profile is None
            or not profile.benchmark_text.strip()
        ):
            return _business_empty_output(
                plan,
                "Official open-fund benchmark definition is "
                "unavailable.",
            )
        try:
            definition = self.open_fund_resolver.resolve(
                profile
            )
        except (
            BenchmarkDefinitionIncompleteError,
            ValueError,
        ) as exc:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.FAILED,
                summary=str(exc),
                gap_kind=ExplorationGapKind.EXECUTION,
                gap_code="benchmark_definition_incomplete",
                gap_message=str(exc),
            )
        try:
            result = self.service.compare_composite(
                asset_id=session.task.asset_id,
                asset_records=[
                    dict(record)
                    for record in nav_response.records
                ],
                definition=definition,
                start_date=_required_str(
                    parameters,
                    "start_date",
                ),
                end_date=end_date,
                as_of_date=plan.as_of_date,
                allow_remote=_bool(
                    parameters,
                    "allow_remote",
                    True,
                ),
                provider_timeout_seconds=_float(
                    parameters,
                    "provider_timeout_seconds",
                    30.0,
                ),
            )
        except BenchmarkComponentUnavailableError as exc:
            return _remote_failure_output(plan, str(exc))
        except BenchmarkCoverageError as exc:
            return _business_empty_output(plan, str(exc))
        except BenchmarkDefinitionIncompleteError as exc:
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.FAILED,
                summary=str(exc),
                gap_kind=ExplorationGapKind.EXECUTION,
                gap_code="benchmark_definition_incomplete",
                gap_message=str(exc),
            )
        outcome = (
            ResearchQueryOutcome.SUCCESS
            if nav_response.complete and not nav_response.stale
            else ResearchQueryOutcome.PARTIAL
        )
        return ResearchQueryHandlerOutput(
            outcome=outcome,
            summary=(
                "Compared open fund with its official composite "
                f"benchmark across {len(result.aligned.dates)} "
                "observations."
            ),
            evidence_refs=(_query_evidence_ref(plan),),
            evidence_tier=EvidenceTier.DERIVED,
            metadata={
                "benchmark_asset_id": result.benchmark_asset_id,
                "benchmark_kind": result.benchmark_kind.value,
                "observation_count": len(result.aligned.dates),
                "metric_count": len(result.metrics),
                "effective_end_date": end_date,
                "sources": list(nav_response.sources),
                "nav_complete": nav_response.complete,
                "nav_stale": nav_response.stale,
            },
            gap_kind=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else ExplorationGapKind.REMOTE_FAILURE
            ),
            gap_code=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else "open_fund_nav_partial"
            ),
            gap_message=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else (
                    nav_response.error
                    or "Open-fund NAV is incomplete or stale."
                )
            ),
        )


class EventKnowledgeQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del context
        parameters = plan.parameters
        requested_end = (
            _optional_str(parameters.get("end_date"))
            or plan.as_of_date
        )
        effective_end = min(
            date.fromisoformat(requested_end),
            date.fromisoformat(plan.as_of_date),
        ).isoformat()
        events = self.service.list_events(
            session.task.asset_id,
            start_date=_optional_str(
                parameters.get("start_date")
            ),
            end_date=effective_end,
            event_type=_optional_str(
                parameters.get("event_type")
            ),
        )
        if not events:
            return _business_empty_output(
                plan,
                "No persisted source-backed events matched.",
                metadata={
                    "event_count": 0,
                    "effective_end_date": effective_end,
                },
            )
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.SUCCESS,
            summary=f"Found {len(events)} source-backed events.",
            evidence_refs=tuple(
                f"evidence:event:{item.event_id}"
                for item in events
            ),
            evidence_tier=EvidenceTier.DERIVED,
            metadata={
                "event_count": len(events),
                "event_ids": [
                    item.event_id for item in events
                ],
                "effective_end_date": effective_end,
            },
        )


class PeerAnalysisQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del session, context
        bundle = self.service.compare(
            peer_set_id=_required_str(
                plan.parameters,
                "peer_set_id",
            ),
            as_of_date=plan.as_of_date,
            metric_names=_string_tuple(
                plan.parameters.get("metric_names", ())
            ),
        )
        statuses = [
            item.status for item in bundle.comparisons
        ]
        metadata = {
            "peer_set_id": bundle.peer_set.peer_set_id,
            "comparison_statuses": [
                item.value for item in statuses
            ],
            "metric_names": [
                item.metric_name
                for item in bundle.comparisons
            ],
        }
        available = statuses.count(
            PeerComparisonStatus.AVAILABLE
        )
        if available == 0:
            return _business_empty_output(
                plan,
                "No peer metric had a sufficient compatible sample.",
                metadata=metadata,
            )
        outcome = (
            ResearchQueryOutcome.SUCCESS
            if available == len(statuses)
            else ResearchQueryOutcome.PARTIAL
        )
        return ResearchQueryHandlerOutput(
            outcome=outcome,
            summary=(
                f"{available} of {len(statuses)} peer metrics "
                "had a compatible sample."
            ),
            evidence_refs=(_query_evidence_ref(plan),),
            evidence_tier=EvidenceTier.DERIVED,
            metadata=metadata,
            gap_kind=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else ExplorationGapKind.BUSINESS_EMPTY
            ),
            gap_code=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else "peer_comparison_partial"
            ),
            gap_message=(
                None
                if outcome is ResearchQueryOutcome.SUCCESS
                else "Some peer metrics lacked compatible samples."
            ),
        )


class IndustryAnalysisQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del context
        industry = self.service.get_industry(
            asset_id=session.task.asset_id,
            as_of_date=plan.as_of_date,
        )
        if industry is None:
            return _business_empty_output(
                plan,
                "No effective industry assignment is available.",
            )
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.SUCCESS,
            summary=(
                f"Resolved industry {industry.industry_name} "
                f"({industry.industry_code})."
            ),
            evidence_refs=(_query_evidence_ref(plan),),
            evidence_tier=EvidenceTier.DERIVED,
            metadata={
                "industry_code": industry.industry_code,
                "industry_name": industry.industry_name,
                "classification_source": (
                    industry.classification_source
                ),
                "classification_version": (
                    industry.classification_version
                ),
                "effective_date": industry.effective_date,
            },
        )


class UserResearchStateQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        operation = _required_str(
            plan.parameters,
            "operation",
        )
        if operation == "decision_brief":
            brief = self.service.get_decision_brief(
                context,
                asset_id=session.task.asset_id,
            )
            if brief is None:
                return _user_context_output(
                    plan,
                    "No decision brief is available for this user.",
                )
            if not _on_or_before(
                brief.updated_at,
                plan.as_of_date,
            ):
                return _user_context_output(
                    plan,
                    "The current decision brief postdates the "
                    "research cutoff; no historical brief is "
                    "available.",
                )
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary=(
                    "Loaded the user decision brief and missing "
                    "context requirements."
                ),
                evidence_refs=(
                    f"evidence:user-context:{brief.brief_id}",
                ),
                evidence_tier=EvidenceTier.USER_CONTEXT,
                metadata={
                    "brief_id": brief.brief_id,
                    "missing_user_context": [
                        item.value
                        for item in brief.missing_user_context
                    ],
                },
            )
        if operation == "holding":
            holding = self.service.get_holding(
                context,
                asset_id=session.task.asset_id,
            )
            if holding is None:
                return _user_context_output(
                    plan,
                    "No current holding is recorded for this asset.",
                )
            if not _on_or_before(
                holding.updated_at,
                plan.as_of_date,
            ):
                return _user_context_output(
                    plan,
                    "The current holding postdates the research "
                    "cutoff; no historical holding is available.",
                )
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary="Loaded the user's current holding.",
                evidence_refs=(
                    "evidence:user-context:holding:"
                    f"{session.task.asset_id}",
                ),
                evidence_tier=EvidenceTier.USER_CONTEXT,
                metadata={
                    "quantity": holding.quantity,
                    "average_cost": holding.average_cost,
                    "currency": holding.currency,
                },
            )
        raise ValueError(
            f"Unsupported research-state operation: {operation}"
        )


class PortfolioRiskQueryHandler:
    def __init__(self, service: object) -> None:
        self.service = service

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del session
        operation = _required_str(
            plan.parameters,
            "operation",
        )
        if operation == "snapshot":
            snapshot_id = _required_str(
                plan.parameters,
                "snapshot_id",
            )
            snapshot = self.service.get_snapshot(
                context,
                snapshot_id,
            )
            if snapshot is None:
                return _user_context_output(
                    plan,
                    "The requested portfolio snapshot is unavailable.",
                )
            if not _on_or_before(
                snapshot.snapshot.as_of_date,
                plan.as_of_date,
            ):
                return _user_context_output(
                    plan,
                    "The requested portfolio snapshot postdates "
                    "the research cutoff.",
                )
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary="Loaded the requested portfolio snapshot.",
                evidence_refs=(
                    f"evidence:user-context:{snapshot_id}",
                ),
                evidence_tier=EvidenceTier.USER_CONTEXT,
                metadata={
                    "snapshot_id": snapshot_id,
                    "as_of_date": snapshot.snapshot.as_of_date,
                },
            )
        if operation == "list_snapshots":
            snapshots = [
                item
                for item in self.service.list_snapshots(context)
                if _on_or_before(
                    item.as_of_date,
                    plan.as_of_date,
                )
            ]
            if not snapshots:
                return _user_context_output(
                    plan,
                    "No portfolio snapshots are available.",
                )
            return ResearchQueryHandlerOutput(
                outcome=ResearchQueryOutcome.SUCCESS,
                summary=(
                    f"Found {len(snapshots)} portfolio snapshots."
                ),
                evidence_refs=tuple(
                    f"evidence:user-context:{item.snapshot_id}"
                    for item in snapshots
                ),
                evidence_tier=EvidenceTier.USER_CONTEXT,
                metadata={
                    "snapshot_ids": [
                        item.snapshot_id for item in snapshots
                    ],
                },
            )
        raise ValueError(
            f"Unsupported portfolio operation: {operation}"
        )


class ExternalOfficialWebQueryHandler:
    """Register Agent-reviewed official sources without crawling."""

    def execute(
        self,
        *,
        plan: ResearchQueryPlan,
        session: ResearchExplorationSession,
        context: UserContext,
    ) -> ResearchQueryHandlerOutput:
        del session, context
        url = _required_str(plan.parameters, "url")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                "official source URL must be absolute HTTPS"
            )
        title = _required_str(plan.parameters, "title")
        source_name = _required_str(
            plan.parameters,
            "source_name",
        )
        published_at = _required_str(
            plan.parameters,
            "published_at",
        )
        published_date = date.fromisoformat(
            published_at[:10]
        )
        if published_date > date.fromisoformat(plan.as_of_date):
            raise ValueError(
                "official source exceeds query as_of_date"
            )
        digest = sha256(
            f"{url}|{published_at}".encode("utf-8")
        ).hexdigest()[:20]
        return ResearchQueryHandlerOutput(
            outcome=ResearchQueryOutcome.SUCCESS,
            summary=(
                f"Registered official source '{title}' from "
                f"{source_name}; content interpretation remains "
                "an Agent responsibility."
            ),
            evidence_refs=(f"evidence:external:{digest}",),
            evidence_tier=EvidenceTier.PRIMARY,
            metadata={
                "url": url,
                "title": title,
                "source_name": source_name,
                "published_at": published_at,
                "content_excerpt": _optional_str(
                    plan.parameters.get("content_excerpt")
                ),
                "registration_only": True,
            },
        )


class ResearchExplorationQueryService:
    def __init__(
        self,
        *,
        capability_registry: ResearchCapabilityRegistry,
        router: ResearchQueryRouter,
        exploration: PersistentResearchExplorationService,
    ) -> None:
        self.capability_registry = capability_registry
        self.router = router
        self.exploration = exploration

    def execute(
        self,
        context: UserContext,
        *,
        session_id: str,
        plan: ResearchQueryPlan,
    ) -> ResearchQueryExecutionResult:
        session = self.exploration.get_session(
            context,
            session_id=session_id,
        )
        if session is None:
            raise KeyError(
                f"Unknown exploration session: {session_id}"
            )
        if plan.session_id != session.session_id:
            raise ValueError(
                "query plan session does not match exploration"
            )
        if plan.as_of_date > session.task.as_of_date:
            raise ValueError(
                "query plan exceeds exploration as_of_date"
            )
        question = _require_open_question(
            session,
            plan.question_id,
        )
        if any(
            plan.query_id in item.query_ids
            for item in session.questions
        ):
            raise ValueError(
                f"query_id already recorded: {plan.query_id}"
            )
        required_capability = _ROUTE_CAPABILITIES[plan.route]
        if plan.capability_id != required_capability:
            raise ValueError(
                "query capability does not match public route"
            )
        if (
            question.required_capability is not None
            and question.required_capability
            != plan.capability_id
        ):
            raise ValueError(
                "query capability does not match question"
            )
        allowed_capabilities = self._allowed_capabilities(
            session,
            question,
        )
        if plan.capability_id not in allowed_capabilities:
            output = _unsupported_output(
                plan,
                "Capability is not declared for this profile, "
                "question, and decision track.",
            )
        else:
            output = self.router.execute(
                plan=plan,
                session=session,
                context=context,
            )
        gap = _query_gap(
            plan=plan,
            question=question,
            output=output,
        )
        findings = (
            _query_finding(
                plan=plan,
                question=question,
                output=output,
                gap=gap,
            ),
        )
        result = ResearchQueryResult(
            query_id=plan.query_id,
            question_id=plan.question_id,
            capability_id=plan.capability_id,
            route=plan.route,
            outcome=output.outcome,
            summary=output.summary,
            evidence_refs=output.evidence_refs,
            findings=findings,
            gap=gap,
            metadata=output.metadata or {},
            completed_at=_now(),
        )
        updated = self.exploration.record_query_result(
            context,
            session_id=session.session_id,
            question_id=question.question_id,
            query_id=plan.query_id,
            outcome=result.outcome.value,
            summary=result.summary,
            evidence_refs=result.evidence_refs,
            plan_payload=plan.to_dict(),
            result_payload=result.to_dict(),
            resolve_question=False,
            defer_question=(
                result.outcome
                is ResearchQueryOutcome.UNSUPPORTED
            ),
            gap=gap,
        )
        return ResearchQueryExecutionResult(
            session=updated,
            result=result,
        )

    def _allowed_capabilities(
        self,
        session: ResearchExplorationSession,
        question: ExplorationQuestion,
    ) -> set[str]:
        lanes = _question_lanes(session, question)
        capabilities = set()
        for lane in lanes:
            view = self.capability_registry.resolve(
                profile_id=session.task.profile_id,
                question_type=session.task.question_type,
                decision_lane=lane,
            )
            capabilities.update(view.agent_query_capabilities)
        return capabilities


def default_query_router(
    *,
    data_access: object,
    research_evidence: object,
    benchmark_analysis: object,
    open_fund_research: object,
    open_fund_benchmark_resolver: object,
    event_knowledge: object,
    peer_analysis: object,
    research_state: object,
    portfolio_risk: object,
) -> ResearchQueryRouter:
    return ResearchQueryRouter(
        {
            ResearchQueryRoute.DATA_ACCESS: (
                DataAccessQueryHandler(data_access)
            ),
            ResearchQueryRoute.RESEARCH_EVIDENCE: (
                ResearchEvidenceQueryHandler(
                    research_evidence
                )
            ),
            ResearchQueryRoute.BENCHMARK_ANALYSIS: (
                BenchmarkQueryHandler(
                    benchmark_analysis,
                    open_fund_data_access=data_access,
                    open_fund_research=open_fund_research,
                    open_fund_resolver=(
                        open_fund_benchmark_resolver
                    ),
                )
            ),
            ResearchQueryRoute.EVENT_KNOWLEDGE: (
                EventKnowledgeQueryHandler(event_knowledge)
            ),
            ResearchQueryRoute.INDUSTRY_ANALYSIS: (
                IndustryAnalysisQueryHandler(peer_analysis)
            ),
            ResearchQueryRoute.PEER_ANALYSIS: (
                PeerAnalysisQueryHandler(peer_analysis)
            ),
            ResearchQueryRoute.USER_RESEARCH_STATE: (
                UserResearchStateQueryHandler(research_state)
            ),
            ResearchQueryRoute.PORTFOLIO_RISK: (
                PortfolioRiskQueryHandler(portfolio_risk)
            ),
            ResearchQueryRoute.EXTERNAL_OFFICIAL_WEB: (
                ExternalOfficialWebQueryHandler()
            ),
        }
    )


def _query_gap(
    *,
    plan: ResearchQueryPlan,
    question: ExplorationQuestion,
    output: ResearchQueryHandlerOutput,
) -> ExplorationQueryGap | None:
    if output.gap_kind is None:
        return None
    return ExplorationQueryGap(
        query_id=plan.query_id,
        capability_id=plan.capability_id,
        kind=output.gap_kind,
        code=(
            output.gap_code
            or f"{plan.route.value}_{output.outcome.value}"
        ),
        message=output.gap_message or output.summary,
        blocking=question.blocking,
    )


def _query_finding(
    *,
    plan: ResearchQueryPlan,
    question: ExplorationQuestion,
    output: ResearchQueryHandlerOutput,
    gap: ExplorationQueryGap | None,
) -> ResearchFinding:
    successful = output.outcome in (
        ResearchQueryOutcome.SUCCESS,
        ResearchQueryOutcome.PARTIAL,
    )
    limitations = [
        (
            "Query output requires Agent interpretation before it "
            "can change a hypothesis."
        ),
        *(
            (gap.message,)
            if gap is not None
            else ()
        ),
    ]
    return ResearchFinding(
        finding_id=f"finding:query:{plan.query_id}",
        text=output.summary,
        kind=(
            ResearchFindingKind.INFERENCE
            if successful
            else ResearchFindingKind.UNKNOWN
        ),
        as_of_date=plan.as_of_date,
        track=question.track,
        evidence_refs=output.evidence_refs,
        source_tier=output.evidence_tier,
        limitations=tuple(limitations),
    )


def _require_open_question(
    session: ResearchExplorationSession,
    question_id: str,
) -> ExplorationQuestion:
    try:
        question = next(
            item
            for item in session.questions
            if item.question_id == question_id
        )
    except StopIteration as exc:
        raise KeyError(
            f"Unknown exploration question: {question_id}"
        ) from exc
    if question.status is not QuestionStatus.OPEN:
        raise ValueError("query question is no longer open")
    return question


def _question_lanes(
    session: ResearchExplorationSession,
    question: ExplorationQuestion,
) -> tuple[Any, ...]:
    from src.research_workflow import DecisionLane

    mapping = {
        ExplorationTrack.SUITABILITY: DecisionLane.SUITABILITY,
        ExplorationTrack.ALTERNATIVE: DecisionLane.SUITABILITY,
        ExplorationTrack.TIMING: DecisionLane.TIMING,
        ExplorationTrack.POSITION_SIZING: (
            DecisionLane.POSITION_SIZING
        ),
    }
    lane = mapping.get(question.track)
    if lane is not None:
        return (lane,)
    return (
        (*session.decision_lanes, None)
        if session.decision_lanes
        else (None,)
    )


def _unsupported_output(
    plan: ResearchQueryPlan,
    message: str,
) -> ResearchQueryHandlerOutput:
    return ResearchQueryHandlerOutput(
        outcome=ResearchQueryOutcome.UNSUPPORTED,
        summary=message,
        gap_kind=ExplorationGapKind.CAPABILITY,
        gap_code=f"capability_unavailable:{plan.capability_id}",
        gap_message=message,
    )


def _remote_failure_output(
    plan: ResearchQueryPlan,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ResearchQueryHandlerOutput:
    return ResearchQueryHandlerOutput(
        outcome=ResearchQueryOutcome.REMOTE_FAILURE,
        summary=message,
        metadata=metadata,
        gap_kind=ExplorationGapKind.REMOTE_FAILURE,
        gap_code=f"{plan.route.value}_source_unavailable",
        gap_message=message,
    )


def _business_empty_output(
    plan: ResearchQueryPlan,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ResearchQueryHandlerOutput:
    return ResearchQueryHandlerOutput(
        outcome=ResearchQueryOutcome.BUSINESS_EMPTY,
        summary=message,
        metadata=metadata,
        gap_kind=ExplorationGapKind.BUSINESS_EMPTY,
        gap_code=f"{plan.route.value}_business_empty",
        gap_message=message,
    )


def _user_context_output(
    plan: ResearchQueryPlan,
    message: str,
) -> ResearchQueryHandlerOutput:
    return ResearchQueryHandlerOutput(
        outcome=ResearchQueryOutcome.USER_CONTEXT_REQUIRED,
        summary=message,
        gap_kind=ExplorationGapKind.USER_CONTEXT,
        gap_code=f"{plan.route.value}_user_context_required",
        gap_message=message,
    )


def _query_evidence_ref(plan: ResearchQueryPlan) -> str:
    return f"evidence:query:{plan.query_id}"


def _effective_end_date(
    requested_end: str,
    as_of_date: str,
) -> str:
    return min(
        date.fromisoformat(requested_end),
        date.fromisoformat(as_of_date),
    ).isoformat()


def _on_or_before(
    value: str,
    as_of_date: str,
) -> bool:
    if not value:
        return False
    return date.fromisoformat(value[:10]) <= date.fromisoformat(
        as_of_date
    )


def _evidence_bundle_tier(
    evidence: tuple[object, ...],
) -> EvidenceTier:
    tiers = {
        getattr(getattr(item, "tier", None), "value", None)
        for item in evidence
    }
    if "derived" in tiers:
        return EvidenceTier.DERIVED
    if "third_party" in tiers:
        return EvidenceTier.SECONDARY
    if tiers == {"official"}:
        return EvidenceTier.PRIMARY
    return EvidenceTier.UNKNOWN


def _required_str(
    parameters: dict[str, Any],
    key: str,
) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"query parameter '{key}' must be non-blank"
        )
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "optional query string must be non-blank"
        )
    return value


def _bool(
    parameters: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = parameters.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(
            f"query parameter '{key}' must be boolean"
        )
    return value


def _float(
    parameters: dict[str, Any],
    key: str,
    default: float,
) -> float:
    value = parameters.get(key, default)
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(
            f"query parameter '{key}' must be positive"
        )
    return float(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            "query parameter must be a string list"
        )
    output = tuple(str(item) for item in value)
    if any(not item.strip() for item in output):
        raise ValueError(
            "query string list must contain non-blank values"
        )
    return output


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
