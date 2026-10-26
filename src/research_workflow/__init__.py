"""Single-asset research templates, task factory and execution services."""

from .profiles import (
    AssetResearchProfile,
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
    "AssetResearchProfile",
    "ResearchProfileRegistry",
    "ResearchProfileAssignment",
    "ResearchProfileAssignmentService",
    "ResearchTaskFactory",
    "ResearchExecutionGap",
    "CycleResearchExecutionOptions",
    "CyclicalResourceResearchExecutionService",
    "ResearchExecutionOptions",
    "ResearchExecutionResult",
    "ResearchExecutionService",
    "ETFResearchExecutionService",
    "FinancialInstitutionResearchExecutionService",
    "OpenFundResearchExecutionService",
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
    if name == "OpenFundResearchExecutionService":
        from .open_fund_execution_service import (
            OpenFundResearchExecutionService,
        )

        return OpenFundResearchExecutionService
    if name == "CyclicalResourceResearchExecutionService":
        from .cycle_execution_service import (
            CyclicalResourceResearchExecutionService,
        )

        return CyclicalResourceResearchExecutionService
    if name in execution_names:
        from . import execution_models

        return getattr(execution_models, name)
    raise AttributeError(name)
