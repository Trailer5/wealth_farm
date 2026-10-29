"""Strategy archives as a public service: list, show, register versions and move lifecycle states.

Every registration and state change is also recorded in the ledger as an objective state assessment
of `strategy:<id>`. Design notes: docs/modules/3-分析工具/策略规则模块.md「策略档案与生命周期」.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_contracts import AssessmentDimension, Confidence, StateAssessment, SubjectType
from src.research_ledger import LedgerScope, ResearchLedgerService
from src.strategy import (
    NEXT_STATE,
    NOT_EXPRESSIBLE,
    STATE_LABELS,
    STRATEGY_KINDS,
    STRATEGY_TEMPLATES,
    ForwardRecord,
    LifecycleState,
    SQLiteStrategyArchiveRepository,
    StrategyArchive,
    TemplateError,
    ValidationRecord,
    archive_from_template,
    default_archives,
    transition_blockers,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StrategyArchiveError(ValueError):
    pass


class StrategyArchiveService:
    def __init__(
        self,
        *,
        repository: SQLiteStrategyArchiveRepository,
        ledger: ResearchLedgerService,
        project_root: Path = _PROJECT_ROOT,
    ) -> None:
        self.repository = repository
        self.ledger = ledger
        self.project_root = project_root
        self.repository.initialize()

    def ensure_defaults(self, as_of_date: str) -> None:
        """Register the built-in research rules once; those with registered sources move on to validating."""

        for archive in default_archives():
            if self.repository.get(archive.strategy_id, archive.version) is not None:
                continue
            self._register(archive, as_of_date, reason="内置研究规则登记为档案")
            if archive.source_refs:
                self.transition(
                    archive.strategy_id,
                    LifecycleState.VALIDATING,
                    as_of_date=as_of_date,
                    reason="来源已登记",
                    version=archive.version,
                )

    def list_archives(self, as_of_date: str) -> dict[str, Any]:
        self.ensure_defaults(as_of_date)
        rows = []
        for archive, state, _ in self.repository.list_latest():
            rows.append(
                {
                    "strategy_id": archive.strategy_id,
                    "version": archive.version,
                    "name": archive.name,
                    "kind": STRATEGY_KINDS[archive.kind],
                    "horizons": [item.value for item in archive.horizons],
                    "state": state.value,
                    "state_label": STATE_LABELS[state],
                    **self._next_step(archive, state, as_of_date),
                }
            )
        return {"as_of_date": as_of_date, "strategies": rows}

    def show(self, strategy_id: str, as_of_date: str, version: str | None = None) -> dict[str, Any]:
        self.ensure_defaults(as_of_date)
        found = self.repository.get(strategy_id, version)
        if found is None:
            raise StrategyArchiveError(f"没有策略档案 {strategy_id}；用 strategy-archive 列出已登记的策略")
        archive, state, registered_on = found
        runs = self.repository.backtests(archive.family)
        return {
            "archive": archive.to_dict(),
            "state": state.value,
            "state_label": STATE_LABELS[state],
            "registered_on": registered_on,
            "versions": self.repository.versions(strategy_id),
            "events": self.repository.events(strategy_id),
            "family_backtests": {
                "family": archive.family,
                "runs": len(runs),
                "trials": len({(str(sorted(r["parameters"].items())), r["start_date"], r["end_date"]) for r in runs}),
            },
            **self._next_step(archive, state, as_of_date),
        }

    def register(self, payload: dict[str, Any], as_of_date: str) -> dict[str, Any]:
        """Register a new strategy or a new version; it starts in research. Re-registering identical content is a no-op."""

        self.ensure_defaults(as_of_date)
        archive = StrategyArchive.from_dict(payload)
        existing = self.repository.get(archive.strategy_id, archive.version)
        if existing is not None:
            if existing[0] != archive:
                raise StrategyArchiveError(
                    f"{archive.strategy_id}@{archive.version} 已登记且内容不同；修改请登记新版本号"
                )
            return {"status": "unchanged", **self.show(archive.strategy_id, as_of_date, archive.version)}
        self._register(archive, as_of_date, reason="登记新策略或新版本")
        return {"status": "registered", **self.show(archive.strategy_id, as_of_date, archive.version)}

    @staticmethod
    def templates() -> dict[str, Any]:
        return {
            "templates": [template.to_dict() for template in STRATEGY_TEMPLATES.values()],
            "not_expressible": list(NOT_EXPRESSIBLE),
        }

    def register_from_template(
        self,
        template_id: str,
        *,
        strategy_id: str,
        as_of_date: str,
        universe_assets: tuple[str, ...] = (),
        parameters: dict[str, str] | None = None,
        name: str | None = None,
        version: str = "v1",
    ) -> dict[str, Any]:
        """Register "template + parameters"; template sources are registered, so a new archive moves on to validating."""

        try:
            archive = archive_from_template(
                template_id,
                strategy_id=strategy_id,
                universe_assets=universe_assets,
                parameters=parameters,
                name=name,
                version=version,
            )
        except TemplateError as exc:
            raise StrategyArchiveError(str(exc)) from exc
        result = self.register(archive.to_dict(), as_of_date)
        if result["status"] != "registered":
            return result
        self.transition(
            strategy_id, LifecycleState.VALIDATING, as_of_date=as_of_date, reason="模板来源已登记", version=version
        )
        return {"status": "registered", **self.show(strategy_id, as_of_date, version)}

    def transition(
        self,
        strategy_id: str,
        target: LifecycleState,
        *,
        as_of_date: str,
        reason: str,
        version: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Move one step (or retire). A move that misses the gate is refused, logged, and the blockers returned."""

        found = self.repository.get(strategy_id, version)
        if found is None:
            raise StrategyArchiveError(f"没有策略档案 {strategy_id}")
        archive, state, _ = found
        blockers = transition_blockers(
            archive,
            state,
            target,
            as_of_date=as_of_date,
            reason=reason,
            sources_found=self._sources_found(archive),
            validation=self._validation(archive),
            forward=self._forward(archive),
        )
        latest = max(
            (event["on_date"] for event in self.repository.events(strategy_id) if event["version"] == archive.version),
            default=None,
        )
        if latest is not None and as_of_date < latest:
            blockers.append(f"迁移日期 {as_of_date} 早于档案最近一次事件（{latest}），时间线会倒序")
        detail = {"evidence_refs": list(evidence_refs), "blockers": blockers}
        if blockers:
            self.repository.record_event(
                archive,
                event_type="rejected",
                from_state=state,
                to_state=target,
                on_date=as_of_date,
                reason=reason,
                detail=detail,
            )
            return {
                "status": "rejected",
                "strategy_id": strategy_id,
                "version": archive.version,
                "state": state.value,
                "state_label": STATE_LABELS[state],
                "target": target.value,
                "blockers": blockers,
            }
        entry_id = self._record_ledger(archive, target, as_of_date, reason, evidence_refs)
        self.repository.record_event(
            archive,
            event_type="transition",
            from_state=state,
            to_state=target,
            on_date=as_of_date,
            reason=reason,
            detail=detail,
            ledger_entry_id=entry_id,
        )
        return {
            "status": "moved",
            "strategy_id": strategy_id,
            "version": archive.version,
            "from_state": state.value,
            "state": target.value,
            "state_label": STATE_LABELS[target],
            "ledger_entry_id": entry_id,
        }

    def _register(self, archive: StrategyArchive, as_of_date: str, *, reason: str) -> None:
        self.repository.add(archive, on_date=as_of_date)
        entry_id = self._record_ledger(archive, LifecycleState.RESEARCH, as_of_date, reason, ())
        self.repository.record_event(
            archive,
            event_type="registered",
            from_state=None,
            to_state=LifecycleState.RESEARCH,
            on_date=as_of_date,
            reason=reason,
            detail={"archive": archive.to_dict()},
            ledger_entry_id=entry_id,
        )

    def _next_step(self, archive: StrategyArchive, state: LifecycleState, as_of_date: str) -> dict[str, Any]:
        target = NEXT_STATE.get(state)
        if target is None:
            return {"next_state": None, "next_blockers": []}
        return {
            "next_state": target.value,
            "next_blockers": transition_blockers(
                archive,
                state,
                target,
                as_of_date=as_of_date,
                sources_found=self._sources_found(archive),
                validation=self._validation(archive),
                forward=self._forward(archive),
            ),
        }

    def _sources_found(self, archive: StrategyArchive) -> tuple[str, ...]:
        return tuple(ref for ref in archive.source_refs if (self.project_root / ref).is_file())

    def _validation(self, archive: StrategyArchive) -> ValidationRecord | None:
        latest = self.repository.latest_evaluation(archive.strategy_id, archive.version)
        if latest is None:
            return None
        flags = latest["flags"]
        return ValidationRecord(
            evaluation_ref=latest["evaluation_id"],
            as_of_date=latest["as_of_date"],
            rolling_out_of_sample=bool(flags.get("rolling_out_of_sample")),
            parameter_neighborhood=bool(flags.get("parameter_neighborhood")),
            deflated_sharpe=bool(flags.get("deflated_sharpe")),
            out_of_sample_not_worse_than_benchmark=bool(flags.get("out_of_sample_not_worse_than_benchmark")),
        )

    def _forward(self, archive: StrategyArchive) -> ForwardRecord | None:
        status = self.repository.latest_forward_status(archive.strategy_id, archive.version)
        if status is None:
            return None
        return ForwardRecord(
            tracking_since=status["tracking_since"],
            settled_signals=int(status["settled_signals"]),
            hit_rate_not_worse_than_benchmark=status["hit_rate_not_worse"],
            excess_not_worse_than_benchmark=status["excess_not_worse"],
        )

    def _record_ledger(
        self,
        archive: StrategyArchive,
        state: LifecycleState,
        as_of_date: str,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> str:
        refs = (*archive.source_refs, *evidence_refs) or (f"strategy-archive:{archive.strategy_id}@{archive.version}",)
        assessment = StateAssessment(
            assessment_id=f"strategy-archive:{archive.strategy_id}:{archive.version}:{state.value}:{as_of_date}",
            subject_type=SubjectType.STRATEGY,
            subject_id=archive.subject_id,
            as_of_date=as_of_date,
            dimensions=(
                AssessmentDimension("lifecycle_state", state.value, refs),
                AssessmentDimension("version", archive.version, refs),
            ),
            label=f"{archive.name}（{archive.version}）：{STATE_LABELS[state]}",
            confidence=Confidence.MEDIUM,
            change_signals=(reason,),
        )
        (entry,) = self.ledger.record(
            (assessment,),
            scope=LedgerScope.OBJECTIVE,
            user_id=None,
            source_ref=f"strategy-archive:{archive.strategy_id}:{archive.version}:{state.value}",
        )
        return entry.entry_id
