"""Enforce the layer dependency direction defined in .agent/docs/develop/architecture.md."""

import ast
import unittest
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"
IGNORED_DIRS = frozenset({"tests", "__pycache__"})

# Keep in sync with the module ownership table in docs/long_term/系统模块总图.md.
MODULE_LAYERS: dict[str, int] = {
    "research_contracts": 0,
    "data_src": 1,
    "master_data": 1,
    "data_store": 1,
    "data_access": 1,
    "financial_data": 1,
    "data_quality": 1,
    "document_processing": 1,
    "daily_data": 1,
    "macro_data": 1,
    "sector_data": 1,
    "cross_section": 1,
    "research_universe": 2,
    "market_analysis": 2,
    "macro_state": 2,
    "state_capital_flow": 2,
    "event_knowledge": 2,
    "factor_engineering": 3,
    "fundamental_analysis": 3,
    "benchmark_analysis": 3,
    "analog_history": 3,
    "peer_analysis": 3,
    "fund_research": 3,
    "strategy": 3,
    "backtest": 3,
    "research_visualization": 3,
    "user_space": 4,
    "research_state": 4,
    "portfolio_risk": 4,
    "alerts": 4,
    "research_workflow": 5,
    "research_evidence": 5,
    "research_acceptance": 5,
    "security_discovery": 5,
    "event_name_discovery": 5,
    "research_ledger": 5,
    "research_orchestration": 6,
    "research_routing": 6,
    "web_api": 7,
    "web_ui": 7,
    "ai_harness": 7,
}

ALLOWED_LOWER_LAYERS: dict[int, frozenset[int]] = {
    0: frozenset(),
    1: frozenset({0}),
    2: frozenset({0, 1}),
    3: frozenset({0, 1, 2}),
    4: frozenset({0, 1, 2}),
    5: frozenset({0, 1, 2, 3, 4}),
    6: frozenset({0, 1, 2, 3, 4, 5}),
    7: frozenset({0, 6}),
}

# Deviation types follow architecture.md; remove an entry as soon as the import is gone.
DATA_LAYER = "data_layer_cycle_or_upward_type"
TOOL_LAYER = "tool_layer_cycle_or_upward_import"
INTERFACE_BYPASS = "interface_bypasses_orchestration"

KNOWN_DEVIATIONS: dict[tuple[str, str], str] = {
    ("data_src", "data_store"): DATA_LAYER,
    ("data_store", "data_src"): DATA_LAYER,
    ("data_src", "master_data"): DATA_LAYER,
    ("master_data", "data_src"): DATA_LAYER,
    ("data_src", "document_processing"): DATA_LAYER,
    ("document_processing", "data_src"): DATA_LAYER,
    ("data_src", "fund_research"): DATA_LAYER,
    ("document_processing", "fund_research"): DATA_LAYER,
    ("benchmark_analysis", "fund_research"): TOOL_LAYER,
    ("fund_research", "benchmark_analysis"): TOOL_LAYER,
    ("strategy", "research_evidence"): TOOL_LAYER,
    ("backtest", "event_name_discovery"): TOOL_LAYER,
    ("ai_harness", "event_knowledge"): INTERFACE_BYPASS,
    ("ai_harness", "factor_engineering"): INTERFACE_BYPASS,
    ("ai_harness", "master_data"): INTERFACE_BYPASS,
    ("ai_harness", "research_state"): INTERFACE_BYPASS,
    ("ai_harness", "user_space"): INTERFACE_BYPASS,
    ("web_api", "alerts"): INTERFACE_BYPASS,
    ("web_api", "data_access"): INTERFACE_BYPASS,
    ("web_api", "data_quality"): INTERFACE_BYPASS,
    ("web_api", "data_src"): INTERFACE_BYPASS,
    ("web_api", "data_store"): INTERFACE_BYPASS,
    ("web_api", "event_knowledge"): INTERFACE_BYPASS,
    ("web_api", "factor_engineering"): INTERFACE_BYPASS,
    ("web_api", "master_data"): INTERFACE_BYPASS,
    ("web_api", "portfolio_risk"): INTERFACE_BYPASS,
    ("web_api", "research_state"): INTERFACE_BYPASS,
    ("web_api", "user_space"): INTERFACE_BYPASS,
}


def scan_module_imports(src_root: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for module_dir in _module_dirs(src_root):
        for path in module_dir.rglob("*.py"):
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                for target in _imported_src_modules(node):
                    if target != module_dir.name:
                        graph[module_dir.name].add(target)
    return graph


def find_violations(
    graph: dict[str, set[str]],
    layers: dict[str, int],
    allowed: dict[int, frozenset[int]],
) -> set[tuple[str, str]]:
    violations: set[tuple[str, str]] = set()
    for importer, targets in graph.items():
        for target in targets:
            if importer not in layers or target not in layers:
                continue
            importer_layer = layers[importer]
            target_layer = layers[target]
            if importer_layer == target_layer:
                if _reaches(graph, layers, target, importer):
                    violations.add((importer, target))
            elif target_layer not in allowed[importer_layer]:
                violations.add((importer, target))
    return violations


def _module_dirs(src_root: Path) -> list[Path]:
    return sorted(
        path
        for path in src_root.iterdir()
        if path.is_dir() and path.name not in IGNORED_DIRS
    )


def _imported_src_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0:
        if node.module == "src":
            names = [f"src.{alias.name}" for alias in node.names]
        else:
            names = [node.module or ""]
    else:
        return []
    return [
        name.split(".")[1]
        for name in names
        if name.startswith("src.")
    ]


def _reaches(
    graph: dict[str, set[str]],
    layers: dict[str, int],
    start: str,
    goal: str,
) -> bool:
    layer = layers[start]
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(
            neighbor
            for neighbor in graph.get(node, ())
            if layers.get(neighbor) == layer
        )
    return False


class LayerDependencyTest(unittest.TestCase):
    def test_every_source_module_has_a_layer(self):
        modules = {path.name for path in _module_dirs(SRC_ROOT)}

        self.assertEqual(sorted(modules - set(MODULE_LAYERS)), [])
        self.assertEqual(sorted(set(MODULE_LAYERS) - modules), [])

    def test_imports_follow_layer_direction(self):
        violations = find_violations(
            scan_module_imports(SRC_ROOT),
            MODULE_LAYERS,
            ALLOWED_LOWER_LAYERS,
        )

        self.assertEqual(sorted(violations - set(KNOWN_DEVIATIONS)), [])

    def test_known_deviations_are_still_present(self):
        violations = find_violations(
            scan_module_imports(SRC_ROOT),
            MODULE_LAYERS,
            ALLOWED_LOWER_LAYERS,
        )

        self.assertEqual(sorted(set(KNOWN_DEVIATIONS) - violations), [])

    def test_upward_imports_and_same_layer_cycles_are_violations(self):
        layers = {
            "data": 1,
            "tool": 3,
            "tool_peer": 3,
            "tool_leaf": 3,
            "personal": 4,
            "world": 2,
        }
        graph = {
            "data": {"tool"},
            "tool": {"tool_peer", "tool_leaf", "data"},
            "tool_peer": {"tool"},
            "personal": {"world"},
        }

        violations = find_violations(graph, layers, ALLOWED_LOWER_LAYERS)

        self.assertEqual(
            violations,
            {
                ("data", "tool"),
                ("tool", "tool_peer"),
                ("tool_peer", "tool"),
            },
        )

    def test_interfaces_reach_lower_layers_only_through_orchestration(self):
        layers = {"web": 7, "orchestration": 6, "tool": 3}
        graph = {"web": {"orchestration", "tool"}}

        violations = find_violations(graph, layers, ALLOWED_LOWER_LAYERS)

        self.assertEqual(violations, {("web", "tool")})


if __name__ == "__main__":
    unittest.main()
