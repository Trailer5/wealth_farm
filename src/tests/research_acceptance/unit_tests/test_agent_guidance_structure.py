"""Structural checks on Agent guidance: entry points, references and public names.

Wording is deliberately not frozen; only commands, files and code names that
the Agent must be able to find are asserted.
"""

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
AGENT_ROOT = PROJECT_ROOT / ".agent"
RESEARCH_DOCS = AGENT_ROOT / "docs" / "research"

REQUIRED_RESEARCH_GUIDES = (
    "question_routing.md",
    "mainline.md",
    "cold_start_intake.md",
    "agent_charter.md",
    "user_decision_model.md",
    "user_context_questions.md",
    "profile_routing.md",
    "project_capability_map.md",
    "research_strategy_playbook.md",
    "exploration_playbook.md",
    "exploration_api.md",
    "final_report_contract.md",
    "agent_acceptance.md",
    "discovery_handoff.md",
    "event_name_discovery.md",
)


class AgentGuidanceTest(unittest.TestCase):
    def test_research_guidance_references_existing_markdown(self):
        paths = [
            AGENT_ROOT / "SKILL.md",
            AGENT_ROOT / "docs" / "README.md",
            *(RESEARCH_DOCS / filename for filename in REQUIRED_RESEARCH_GUIDES),
        ]
        missing = []
        for path in paths:
            content = self._read(path)
            for reference in re.findall(r"`([^`\n]+\.md)`", content):
                candidates = (PROJECT_ROOT / reference, path.parent / reference)
                if not any(candidate.is_file() for candidate in candidates):
                    missing.append(f"{path.name}: {reference}")

        self.assertEqual(missing, [])

    def test_every_cli_command_named_in_guidance_exists(self):
        from src.research_orchestration.cli import build_parser

        commands = set(
            build_parser()._subparsers._group_actions[0].choices  # noqa: SLF001
        )
        mentioned = set()
        for filename in REQUIRED_RESEARCH_GUIDES:
            content = self._read(RESEARCH_DOCS / filename)
            mentioned.update(re.findall(r"cli ([a-z][a-z-]+)", content))
        routing = self._read(RESEARCH_DOCS / "question_routing.md")
        for command in ("route", "run-recipe", "ledger", "macro", "market-archive", "macro-archive"):
            self.assertIn(command, routing)
            self.assertIn(command, commands)
        self.assertEqual(sorted(mentioned - commands), [])

    def test_research_entry_starts_from_router_and_mainline_links_stage_guides(self):
        mainline = self._read(RESEARCH_DOCS / "mainline.md")

        self.assertIn("question_routing.md", mainline)
        for guide in (
            "cold_start_intake.md",
            "user_decision_model.md",
            "profile_routing.md",
            "project_capability_map.md",
            "exploration_playbook.md",
            "final_report_contract.md",
            "agent_acceptance.md",
        ):
            self.assertIn(guide, mainline)

    def test_documented_public_entries_exist(self):
        import src.research_orchestration as orchestration

        capability_map = self._read(RESEARCH_DOCS / "project_capability_map.md")
        for name in re.findall(r"`([A-Z][A-Za-z]+(?:Service|Planner|Reviewer|Registry|Runner))`", capability_map):
            with self.subTest(name=name):
                self.assertTrue(
                    _defined_in_src(name),
                    f"{name} is documented but not defined in src",
                )
        self.assertTrue(hasattr(orchestration, "create_default_container"))

    def _read(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"Missing guidance file: {path}")
        return path.read_text(encoding="utf-8")


def _defined_in_src(name: str) -> bool:
    pattern = re.compile(rf"^class {name}\b", re.MULTILINE)
    return any(
        pattern.search(path.read_text(encoding="utf-8"))
        for path in (PROJECT_ROOT / "src").rglob("*.py")
        if "tests" not in path.parts
    )


if __name__ == "__main__":
    unittest.main()
