"""In-memory registry for versioned strategy specifications."""

from __future__ import annotations

from .models import StrategySpec


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[
            tuple[str, str],
            StrategySpec,
        ] = {}

    def register(self, spec: StrategySpec) -> StrategySpec:
        key = (spec.strategy_id, spec.version)
        if key in self._strategies:
            raise ValueError(
                f"Strategy already registered: "
                f"{spec.strategy_id}@{spec.version}"
            )
        self._strategies[key] = spec
        return spec

    def get(
        self,
        strategy_id: str,
        version: str,
    ) -> StrategySpec | None:
        return self._strategies.get((strategy_id, version))

    def require(
        self,
        strategy_id: str,
        version: str,
    ) -> StrategySpec:
        spec = self.get(strategy_id, version)
        if spec is None:
            raise KeyError(
                f"Unknown strategy: {strategy_id}@{version}"
            )
        return spec

    def list_specs(self) -> tuple[StrategySpec, ...]:
        return tuple(
            self._strategies[key]
            for key in sorted(self._strategies)
        )
