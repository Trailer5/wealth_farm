"""Model-agnostic AI research harness."""

from .context import ResearchContextAssembler
from .models import (
    CLAIM_FACT,
    CLAIM_INFERENCE,
    CLAIM_SUGGESTION,
    TASK_COMPLETED,
    TASK_CONTEXT_READY,
    TASK_FAILED,
    AnalysisTask,
    AnalysisClaim,
    EvidenceReference,
    HarnessTool,
    ModelAdapter,
    ModelOutput,
    ToolCall,
    ToolExecution,
    ToolResult,
)
from .service import AIHarnessService
from .sqlite_repository import SQLiteAIHarnessRepository
from .tools import ToolRegistry
from .openai_compatible import (
    ModelAdapterError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)

__all__ = [
    "TASK_COMPLETED",
    "TASK_CONTEXT_READY",
    "TASK_FAILED",
    "CLAIM_FACT",
    "CLAIM_INFERENCE",
    "CLAIM_SUGGESTION",
    "AnalysisTask",
    "AnalysisClaim",
    "EvidenceReference",
    "HarnessTool",
    "ModelAdapter",
    "ModelOutput",
    "ToolCall",
    "ToolExecution",
    "ToolResult",
    "ResearchContextAssembler",
    "AIHarnessService",
    "SQLiteAIHarnessRepository",
    "ToolRegistry",
    "ModelAdapterError",
    "OpenAICompatibleAdapter",
    "OpenAICompatibleConfig",
]
