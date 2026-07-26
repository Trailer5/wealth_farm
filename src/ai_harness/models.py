"""Model-agnostic AI research task and evidence models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


TASK_CONTEXT_READY = "context_ready"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"

CLAIM_FACT = "fact"
CLAIM_INFERENCE = "inference"
CLAIM_SUGGESTION = "suggestion"


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    evidence_type: str
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisTask:
    task_id: str
    user_id: str
    asset_id: str
    task_type: str
    question: str
    status: str
    model_name: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    citations: tuple[str, ...] = ()
    error: str | None = None
    created_at: str = ""
    finished_at: str | None = None


@dataclass(frozen=True)
class AnalysisClaim:
    claim_type: str
    text: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class ToolExecution:
    task_id: str
    call_index: int
    tool_name: str
    arguments: dict[str, Any]
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class ModelOutput:
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    citations: tuple[str, ...] = ()
    claims: tuple[AnalysisClaim, ...] = ()


class ModelAdapter(Protocol):
    name: str

    def generate(
        self,
        task: AnalysisTask,
        evidence: tuple[EvidenceReference, ...],
    ) -> ModelOutput:
        ...


class HarnessTool(Protocol):
    name: str

    def run(
        self,
        task: AnalysisTask,
        arguments: dict[str, Any],
    ) -> ToolResult:
        ...
