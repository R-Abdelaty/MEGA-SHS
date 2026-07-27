"""Regression tests for policy retrieval and responsibility separation."""

from __future__ import annotations

import importlib
import unittest

from knowledge_base.retriever import (
    PolicyKnowledgeError,
    load_deterministic_rules,
    retrieve_policies,
)


class PolicyRetrievalTests(unittest.TestCase):
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

        approval_description = " ".join(
            approval_module.approve_repair.description.split()
        )
        self.assertIn("explicitly approved", approval_description)
        self.assertIn("validation_complete == true", approval_description)


if __name__ == "__main__":
    unittest.main()
