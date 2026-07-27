from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from self_healing_scheduler.agent import load_system_prompt  # noqa: E402
from self_healing_scheduler.tools.definitions import TOOL_SPECS  # noqa: E402
from self_healing_scheduler.tools.handlers import SchedulerToolHandlers  # noqa: E402
from self_healing_scheduler.tools.registry import ToolRegistry  # noqa: E402


class ToolDefinitionTests(unittest.TestCase):
    def test_tool_names_are_unique(self) -> None:
        names = [spec.name for spec in TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_tool_accepts_an_object(self) -> None:
        for spec in TOOL_SPECS:
            with self.subTest(tool=spec.name):
                self.assertEqual(spec.input_schema["type"], "object")
                self.assertFalse(spec.input_schema["additionalProperties"])

    def test_expected_write_tools_are_guarded(self) -> None:
        write_tools = {spec.name for spec in TOOL_SPECS if spec.write_operation}
        self.assertEqual(
            write_tools,
            {"record_change_decision", "apply_schedule_patch", "publish_schedule_changes"},
        )

        registry = ToolRegistry(SchedulerToolHandlers())
        with self.assertRaises(PermissionError):
            registry.execute("apply_schedule_patch", {})

    def test_system_prompt_contains_approval_boundary(self) -> None:
        prompt = load_system_prompt()
        self.assertIn("explicitly approves", prompt)
        self.assertIn("Preserve unaffected sessions", prompt)


if __name__ == "__main__":
    unittest.main()

