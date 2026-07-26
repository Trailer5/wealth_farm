"""Model-agnostic AI task execution with evidence validation."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from uuid import uuid4

from src.user_space import UserContext, UserSpaceService

from .context import ResearchContextAssembler
from .models import (
    TASK_COMPLETED,
    TASK_CONTEXT_READY,
    TASK_FAILED,
    CLAIM_FACT,
    CLAIM_INFERENCE,
    CLAIM_SUGGESTION,
    AnalysisTask,
    EvidenceReference,
    ModelAdapter,
    ToolCall,
    ToolExecution,
)
from .sqlite_repository import SQLiteAIHarnessRepository
from .tools import ToolRegistry


class AIHarnessService:
    """Create context snapshots and execute replaceable model adapters."""

    def __init__(
        self,
        repository: SQLiteAIHarnessRepository,
        user_space: UserSpaceService,
        assembler: ResearchContextAssembler,
    ) -> None:
        self.repository = repository
        self.user_space = user_space
        self.assembler = assembler
        self.repository.initialize()

    def create_asset_analysis(
        self,
        context: UserContext,
        *,
        asset_id: str,
        question: str,
    ) -> AnalysisTask:
        """Create one task and freeze its evidence snapshot."""

        self._require_user(context)
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")
        evidence = self.assembler.assemble(context, asset_id)
        task = AnalysisTask(
            task_id=f"ai-task:{uuid4()}",
            user_id=context.user_id,
            asset_id=asset_id,
            task_type="asset_analysis",
            question=normalized_question,
            status=TASK_CONTEXT_READY,
            created_at=_now(),
        )
        self.repository.create_task(task, evidence)
        return task

    def execute(
        self,
        context: UserContext,
        task_id: str,
        adapter: ModelAdapter,
        *,
        tool_calls: tuple[ToolCall, ...] = (),
        tool_registry: ToolRegistry | None = None,
    ) -> AnalysisTask:
        """Execute one task and validate all model citations."""

        task = self.get_task(context, task_id)
        try:
            if tool_calls and tool_registry is None:
                raise ValueError("tool_registry is required for tool calls")
            for index, call in enumerate(tool_calls):
                self._execute_tool(
                    task,
                    index,
                    call,
                    tool_registry,
                )
            evidence = self.repository.list_evidence(task_id)
            output = adapter.generate(task, evidence)
            valid_evidence_ids = {
                item.evidence_id
                for item in evidence
            }
            self._validate_output(output, valid_evidence_ids)
            completed = replace(
                task,
                status=TASK_COMPLETED,
                model_name=adapter.name,
                result={
                    "content": output.content,
                    "data": output.data,
                    "claims": [
                        asdict(claim)
                        for claim in output.claims
                    ],
                },
                citations=output.citations,
                error=None,
                finished_at=_now(),
            )
            self.repository.update_task(completed)
            return completed
        except Exception as exc:
            failed = replace(
                task,
                status=TASK_FAILED,
                model_name=adapter.name,
                error=f"{type(exc).__name__}: {exc}",
                finished_at=_now(),
            )
            self.repository.update_task(failed)
            raise

    def get_task(
        self,
        context: UserContext,
        task_id: str,
    ) -> AnalysisTask:
        self._require_user(context)
        task = self.repository.get_task(task_id, context.user_id)
        if task is None:
            raise KeyError(f"Unknown task_id for user: {task_id}")
        return task

    def list_tasks(self, context: UserContext) -> list[AnalysisTask]:
        self._require_user(context)
        return self.repository.list_tasks(context.user_id)

    def list_evidence(
        self,
        context: UserContext,
        task_id: str,
    ) -> tuple[EvidenceReference, ...]:
        self.get_task(context, task_id)
        return self.repository.list_evidence(task_id)

    def list_tool_executions(
        self,
        context: UserContext,
        task_id: str,
    ) -> tuple[ToolExecution, ...]:
        self.get_task(context, task_id)
        return self.repository.list_tool_executions(task_id)

    def _execute_tool(
        self,
        task: AnalysisTask,
        call_index: int,
        call: ToolCall,
        registry: ToolRegistry | None,
    ) -> None:
        if registry is None:
            raise ValueError("tool_registry is required")
        try:
            tool = registry.get(call.tool_name)
            result = tool.run(task, call.arguments)
            existing_ids = {
                item.evidence_id
                for item in self.repository.list_evidence(task.task_id)
            }
            duplicate_ids = [
                item.evidence_id
                for item in result.evidence
                if item.evidence_id in existing_ids
            ]
            if duplicate_ids:
                raise ValueError(
                    f"Tool produced duplicate evidence: {duplicate_ids}"
                )
            self.repository.add_evidence(task.task_id, result.evidence)
            self.repository.save_tool_execution(
                ToolExecution(
                    task_id=task.task_id,
                    call_index=call_index,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status=TASK_COMPLETED,
                    output=result.output,
                    created_at=_now(),
                )
            )
        except Exception as exc:
            self.repository.save_tool_execution(
                ToolExecution(
                    task_id=task.task_id,
                    call_index=call_index,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status=TASK_FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    created_at=_now(),
                )
            )
            raise

    def _validate_output(
        self,
        output,
        valid_evidence_ids: set[str],
    ) -> None:
        referenced = list(output.citations)
        for claim in output.claims:
            if claim.claim_type not in {
                CLAIM_FACT,
                CLAIM_INFERENCE,
                CLAIM_SUGGESTION,
            }:
                raise ValueError(
                    f"Unknown claim type: {claim.claim_type}"
                )
            if (
                claim.claim_type in {CLAIM_FACT, CLAIM_INFERENCE}
                and not claim.evidence_ids
            ):
                raise ValueError(
                    f"{claim.claim_type} claim requires evidence"
                )
            referenced.extend(claim.evidence_ids)
        invalid = [
            evidence_id
            for evidence_id in referenced
            if evidence_id not in valid_evidence_ids
        ]
        if invalid:
            raise ValueError(
                f"Model cited unknown evidence: {invalid}"
            )

    def _require_user(self, context: UserContext) -> None:
        if self.user_space.get_user(context.user_id) is None:
            raise KeyError(f"Unknown user_id: {context.user_id}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
