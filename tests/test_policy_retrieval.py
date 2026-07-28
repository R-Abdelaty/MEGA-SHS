"""Regression tests for policy retrieval and responsibility separation."""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from knowledge_base.retriever import (
    PolicyKnowledgeError,
    load_deterministic_rules,
    retrieve_policies,
)


class PolicyRetrievalTests(unittest.TestCase):
    def test_agent_prompt_forbids_redundant_cancellation_questions(self) -> None:
        agent_source = (Path(__file__).resolve().parent.parent / "agent.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "A weekday plus an academic week is a complete time scope",
            agent_source,
        )
        self.assertIn(
            "Ask only for the missing mandatory value",
            agent_source,
        )
        self.assertIn(
            "Do not invent additional options",
            agent_source,
        )

    def test_explicit_categories_return_only_requested_documents(self) -> None:
        payload = retrieve_policies(
            categories=["exam_rules", "session_priorities"],
            query="exam conflict",
        )
        self.assertEqual(
            payload["categories"], ["exam_rules", "session_priorities"]
        )
        self.assertEqual(len(payload["documents"]), 2)

    def test_query_retrieval_is_targeted(self) -> None:
        payload = retrieve_policies(query="cancelled tutorial compensation")
        self.assertIn("cancellation_compensation", payload["categories"])
        self.assertLessEqual(len(payload["categories"]), 3)

    def test_entire_knowledge_base_cannot_be_loaded_in_one_call(self) -> None:
        with self.assertRaises(PolicyKnowledgeError):
            retrieve_policies(
                categories=[
                    "session_priorities",
                    "course_sharing",
                    "cohort_rules",
                    "cancellation_compensation",
                    "room_rules",
                ]
            )

    def test_validator_defaults_come_from_policy_configuration(self) -> None:
        configured = load_deterministic_rules()
        validity_module = importlib.import_module("tools.check_validity")
        self.assertEqual(validity_module.DEFAULT_RULES, configured)
        self.assertEqual(configured["final_exam_start_times"], ["09:00", "14:00"])

    def test_tool_descriptions_hold_operational_guidance(self) -> None:
        get_module = importlib.import_module("tools.get_schedule")
        validity_module = importlib.import_module("tools.check_validity")
        approval_module = importlib.import_module("tools.approve_repair")

        get_description = " ".join(get_module.get_schedule.description.split())
        self.assertIn("Filters use AND logic", get_description)
        self.assertIn("next_row_offset", get_description)
        self.assertIn("one-based", get_description)

        validity_description = " ".join(
            validity_module.check_validity.description.split()
        )
        self.assertIn("mapping_requests", validity_description)
        self.assertIn("validation_complete", validity_description)
        self.assertIn("Never use ``ignore``", validity_description)
        self.assertIn("automatically discovers", validity_description)
        self.assertIn("Exact file paths are not required", validity_description)

        approval_description = " ".join(
            approval_module.approve_repair.description.split()
        )
        self.assertIn("explicitly approved", approval_description)
        self.assertIn("validation_complete == true", approval_description)

        cancel_module = importlib.import_module("tools.cancel_day")
        cancel_description = " ".join(cancel_module.cancel_day.description.split())
        self.assertIn(
            "weekday plus ``academic_week`` is a complete time scope",
            cancel_description,
        )
        self.assertIn(
            "day, academic week, reason, and confirmation",
            cancel_description,
        )

    def test_validator_discovers_authoritative_files_without_paths(self) -> None:
        validity_module = importlib.import_module("tools.check_validity")
        discovered = validity_module._discover_authoritative_data_files()
        normalized = {name.casefold() for name in discovered}

        self.assertIn("05_general_schedule.xlsx", normalized)
        self.assertIn("01_room_schedule.xlsx", normalized)
        self.assertIn("07_doctor_schedule_calendar.xlsx", normalized)
        self.assertNotIn("test schedule.xlsx", normalized)
        self.assertNotIn("corrupted schedule.xlsx", normalized)
        self.assertNotIn("general schedule calendar.xlsx", normalized)
        self.assertTrue(
            all(name[:2] in {f"0{number}" for number in range(1, 8)}
                for name in normalized)
        )

    def test_explicit_test_fixture_is_isolated(self) -> None:
        validity_module = importlib.import_module("tools.check_validity")
        files, auto_added, isolated = (
            validity_module._prepare_validation_file_list(
                ["Test Schedule.xlsx"]
            )
        )
        self.assertEqual(files, ["Test Schedule.xlsx"])
        self.assertEqual(auto_added, [])
        self.assertTrue(isolated)

    def test_natural_language_test_schedule_reference_is_resolved(self) -> None:
        validity_module = importlib.import_module("tools.check_validity")
        schedule_module = importlib.import_module("tools.get_schedule")
        reference = "test schedule file in fake data folder"

        validity_path, validity_error = validity_module._resolve_file(reference)
        schedule_path, schedule_error = (
            schedule_module._resolve_requested_file(reference)
        )

        self.assertIsNone(validity_error)
        self.assertIsNone(schedule_error)
        self.assertEqual(validity_path.name, "Test Schedule.xlsx")
        self.assertEqual(schedule_path.name, "Test Schedule.xlsx")


if __name__ == "__main__":
    unittest.main()
