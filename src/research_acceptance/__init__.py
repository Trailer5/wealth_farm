"""Cross-asset acceptance reports for the IDE research workflow."""

from .fixtures import (
    PLAN15_ACCEPTANCE_VERSION,
    PLAN15_AS_OF_DATE,
    PLAN15_BAR_END,
    PLAN15_BAR_START,
    PLAN15_FINANCIAL_END,
    PLAN15_FINANCIAL_START,
    build_plan15_tasks,
    plan15_etf_expense_schedules,
    register_plan15_assets,
)
from .models import (
    AcceptanceStatus,
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    CrossAssetAcceptanceSummary,
    InvalidationCondition,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchGap,
    ResearchScenario,
    ScenarioKind,
)
from .service import (
    REQUIRED_ACCEPTANCE_STEPS,
    CrossAssetAcceptanceService,
    RepresentativeResearchAssembler,
)
from .report_service import ResearchReportAssemblyService

__all__ = [
    "AcceptanceStatus",
    "AcceptanceStepResult",
    "AcceptanceStepStatus",
    "ClaimKind",
    "CrossAssetAcceptanceService",
    "CrossAssetAcceptanceSummary",
    "InvalidationCondition",
    "PLAN15_ACCEPTANCE_VERSION",
    "PLAN15_AS_OF_DATE",
    "PLAN15_BAR_END",
    "PLAN15_BAR_START",
    "PLAN15_FINANCIAL_END",
    "PLAN15_FINANCIAL_START",
    "REQUIRED_ACCEPTANCE_STEPS",
    "RepresentativeResearchAssembler",
    "ResearchAcceptanceReport",
    "ResearchClaim",
    "ResearchGap",
    "ResearchScenario",
    "ResearchReportAssemblyService",
    "ScenarioKind",
    "build_plan15_tasks",
    "plan15_etf_expense_schedules",
    "register_plan15_assets",
]
