"""IDE-first investment research task contracts."""

from .models import (
    AssetResearchProfile,
    ResearchHorizon,
    ResearchOutputDepth,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from .profiles import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchProfileRegistry,
    default_profile_registry,
    default_research_profiles,
)
from .service import ResearchTaskFactory
from .profile_assignment import (
    ResearchProfileAssignment,
    ResearchProfileAssignmentService,
    SQLiteResearchProfileAssignmentRepository,
)

__all__ = [
    "PROFILE_CYCLICAL_RESOURCE",
    "PROFILE_EXCHANGE_FUND",
    "PROFILE_FINANCIAL_INSTITUTION",
    "PROFILE_HIGH_GROWTH_UNPROFITABLE",
    "PROFILE_OPEN_FUND",
    "PROFILE_ORDINARY_COMPANY",
    "AssetResearchProfile",
    "ResearchHorizon",
    "ResearchOutputDepth",
    "ResearchProfileRegistry",
    "ResearchProfileAssignment",
    "ResearchProfileAssignmentService",
    "ResearchQuestionType",
    "ResearchTaskFactory",
    "ResearchTaskSpec",
    "ResearchExecutionGap",
    "CycleResearchExecutionOptions",
    "CyclicalResourceResearchExecutionService",
    "ResearchExecutionOptions",
    "ResearchExecutionResult",
    "ResearchExecutionService",
    "ETFResearchExecutionService",
    "FinancialInstitutionResearchExecutionService",
    "ResearchExecutionStatus",
    "ResearchPhaseResult",
    "ResearchPhaseStatus",
    "ResearchWindows",
    "SQLiteResearchProfileAssignmentRepository",
    "default_profile_registry",
    "default_research_profiles",
]


def __getattr__(name):
    execution_names = {
        "CycleResearchExecutionOptions",
        "ResearchExecutionGap",
        "ResearchExecutionOptions",
        "ResearchExecutionResult",
        "ResearchExecutionStatus",
        "ResearchPhaseResult",
        "ResearchPhaseStatus",
        "ResearchWindows",
    }
    if name == "ResearchExecutionService":
        from .execution_service import ResearchExecutionService

        return ResearchExecutionService
    if name == "ETFResearchExecutionService":
        from .etf_execution_service import (
            ETFResearchExecutionService,
        )

        return ETFResearchExecutionService
    if name == "FinancialInstitutionResearchExecutionService":
        from .financial_execution_service import (
            FinancialInstitutionResearchExecutionService,
        )

        return FinancialInstitutionResearchExecutionService
    if name == "CyclicalResourceResearchExecutionService":
        from .cycle_execution_service import (
            CyclicalResourceResearchExecutionService,
        )

        return CyclicalResourceResearchExecutionService
    if name in execution_names:
        from . import execution_models

        return getattr(execution_models, name)
    raise AttributeError(name)
