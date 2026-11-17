"""Debates between the user, the Agent and persona lenses on two layers of views: positions, rebuttals,
resolutions, coverage and signposts.

Design notes: docs/modules/5-研究产物/观点辩论模块.md.
"""

from .models import (
    DEBATE_CONCLUDED,
    DEBATE_OPEN,
    LAYER_ASSET,
    LAYER_LABELS,
    LAYER_WORLD,
    LAYERS,
    PARTY_AGENT,
    PARTY_USER,
    PERSONA_PREFIX,
    POSITION_LABELS,
    POSITION_OPEN,
    POSITION_REFINED,
    POSITION_REFUTED,
    POSITION_STANDS,
    POSITION_WITHDRAWN,
    Debate,
    DebateRecord,
    Participation,
    Position,
    Rebuttal,
    Signpost,
)
from .personas import PERSONAS, Persona, persona, persona_brief, personas_for
from .service import DebateService
from .sqlite_repository import SQLiteDebateRepository
from .topics import WORLD_TOPICS, WorldTopic

__all__ = [
    "DEBATE_CONCLUDED",
    "DEBATE_OPEN",
    "LAYERS",
    "LAYER_ASSET",
    "LAYER_LABELS",
    "LAYER_WORLD",
    "PARTY_AGENT",
    "PARTY_USER",
    "PERSONAS",
    "PERSONA_PREFIX",
    "POSITION_LABELS",
    "POSITION_OPEN",
    "POSITION_REFINED",
    "POSITION_REFUTED",
    "POSITION_STANDS",
    "POSITION_WITHDRAWN",
    "WORLD_TOPICS",
    "Debate",
    "DebateRecord",
    "DebateService",
    "Participation",
    "Persona",
    "Position",
    "Rebuttal",
    "SQLiteDebateRepository",
    "Signpost",
    "WorldTopic",
    "persona",
    "persona_brief",
    "personas_for",
]
