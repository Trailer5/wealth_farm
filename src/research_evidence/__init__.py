"""Source-backed research evidence models."""

from src.data_access import (
    DATASET_DISCLOSURES,
    DATASET_RESEARCH_REPORTS,
)

from .models import (
    DocumentContentStatus,
    DocumentEvidenceState,
    EvidenceTier,
    ResearchEvidenceBundle,
    ResearchEvidenceItem,
)
from .service import ResearchEvidenceService

__all__ = [
    "DATASET_DISCLOSURES",
    "DATASET_RESEARCH_REPORTS",
    "DocumentContentStatus",
    "DocumentEvidenceState",
    "EvidenceTier",
    "ResearchEvidenceBundle",
    "ResearchEvidenceItem",
    "ResearchEvidenceService",
]
