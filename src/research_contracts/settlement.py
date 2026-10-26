"""How a published judgement is settled against later observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from ._checks import optional_text, require_date, require_instance, require_text
from .tasks import ResearchHorizon


# Calendar days after the reference-price date, confirmed by the user on 2026-10-25.
DEFAULT_CHECKPOINT_DAYS: dict[ResearchHorizon, tuple[int, ...]] = {
    ResearchHorizon.SHORT: (30,),
    ResearchHorizon.MEDIUM_SHORT: (90, 180),
    ResearchHorizon.MEDIUM_LONG: (180, 365),
    ResearchHorizon.LONG: (730, 1095),
}


def last_default_checkpoint(horizon: ResearchHorizon, start: str) -> str:
    days = DEFAULT_CHECKPOINT_DAYS[horizon][-1]
    return (require_date(start, "start") + timedelta(days=days)).isoformat()


def settlement_checkpoints(
    horizon: ResearchHorizon,
    start: str,
    expires_on: str | None = None,
) -> tuple[str, ...]:
    """Default checkpoints, plus a later own expiry as the final one."""

    origin = require_date(start, "start")
    checkpoints = [
        origin + timedelta(days=days)
        for days in DEFAULT_CHECKPOINT_DAYS[horizon]
    ]
    if expires_on is not None:
        expiry = require_date(expires_on, "expires_on")
        if expiry > checkpoints[-1]:
            checkpoints.append(expiry)
    return tuple(item.isoformat() for item in checkpoints)


class SettlementMethod(str, Enum):
    PRICE_RELATIVE = "price_relative"
    METRIC_OBSERVATION = "metric_observation"


@dataclass(frozen=True)
class SettlementRule:
    """price_relative observes an asset; metric_observation a metric series."""

    method: SettlementMethod
    observed_id: str
    benchmark_id: str | None = None
    alternative_id: str | None = None

    def __post_init__(self) -> None:
        require_instance(self.method, SettlementMethod, "method")
        require_text(self.observed_id, "observed_id")
        optional_text(self.benchmark_id, "benchmark_id")
        optional_text(self.alternative_id, "alternative_id")
        if self.method is SettlementMethod.METRIC_OBSERVATION and (
            self.benchmark_id is not None
            or self.alternative_id is not None
        ):
            raise ValueError(
                "metric_observation does not use benchmark or alternative"
            )
        if self.observed_id in (self.benchmark_id, self.alternative_id):
            raise ValueError(
                "benchmark and alternative must differ from observed_id"
            )
