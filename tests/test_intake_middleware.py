"""Tests for the deterministic, one-question-at-a-time UI intake."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from intake_middleware import (
    answer_intake,
    clear_intakes,
    format_console_question,
    parse_console_answer,
    start_intake,
)


class IntakeMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_intakes()

    def test_every_flow_starts_day_then_week_then_problem(self) -> None:
        state = start_intake()
        self.assertEqual(state["question"]["key"], "day_option")

        state = answer_intake(state["intake_id"], {"option": 3})
        self.assertEqual(state["question"]["key"], "academic_week")

        state = answer_intake(state["intake_id"], {"option": 2})
        self.assertEqual(state["question"]["key"], "problem_option")
        self.assertEqual(len(state["question"]["options"]), 11)

    def test_console_prints_day_options_and_requires_a_number(self) -> None:
        state = start_intake()
        rendered = format_console_question(state["question"])

        self.assertIn("Select the affected day.", rendered)
        self.assertIn("1. Sunday", rendered)
        self.assertIn("2. Monday", rendered)
        self.assertIn("5. Thursday", rendered)
        self.assertEqual(parse_console_answer(state["question"], "2"), {"option": 2})
        with self.assertRaisesRegex(ValueError, "displayed option numbers"):
            parse_console_answer(state["question"], "cancel monday")

    def test_partial_day_asks_only_relevant_range_fields(self) -> None:
        state = start_intake()
        intake_id = state["intake_id"]
        sequence = [
            ({"option": 4}, "academic_week"),
            ({"option": 3}, "problem_option"),
            ({"option": 5}, "scope_option"),
            ({"option": 2}, "start_period_option"),
            ({"option": 2}, "end_period_option"),
            ({"option": 4}, "reason_option"),
            ({"option": 5}, "confirmation_option"),
        ]
        for answer, expected_question in sequence:
            state = answer_intake(intake_id, answer)
            self.assertEqual(state["question"]["key"], expected_question)

        state = answer_intake(intake_id, {"option": 1})
        self.assertEqual(state["status"], "ready")
        self.assertIsNone(state["question"])

    def test_end_period_cannot_precede_start_period(self) -> None:
        state = start_intake()
        intake_id = state["intake_id"]
        for option in (1, 1, 5, 2, 4):
            state = answer_intake(intake_id, {"option": option})

        with self.assertRaisesRegex(ValueError, "cannot precede"):
            answer_intake(intake_id, {"option": 2})

    def test_all_problem_options_reach_confirmation(self) -> None:
        problem_answers = {
            1: [({"option": 1}, "scope_option"), ({"option": 3}, "confirmation_option")],
            2: [({"options": [1]}, "scope_option"), ({"option": 3}, "confirmation_option")],
            3: [({"options": [1]}, "scope_option"), ({"option": 3}, "confirmation_option")],
            4: [({"option": 1}, "confirmation_option")],
            5: [
                ({"option": 1}, "start_period_option"),
                ({"option": 1}, "reason_option"),
                ({"option": 1}, "confirmation_option"),
            ],
            6: [({"options": [1]}, "confirmation_option")],
            7: [({"option": 1}, "scope_option"), ({"option": 3}, "confirmation_option")],
            8: [
                ({"option": 1}, "start_period_option"),
                ({"option": 1}, "student_group_options"),
                ({"options": [1]}, "confirmation_option"),
            ],
            9: [
                ({"option": 1}, "resource_options"),
                ({"options": [1]}, "scope_option"),
                ({"option": 3}, "confirmation_option"),
            ],
            10: [
                ({"option": 1}, "corrected_room_capacity"),
                ({"number": 80}, "confirmation_option"),
            ],
            11: [
                ({"text": "PRT-REJECTED"}, "description"),
                ({"text": "The proposed slot is unsuitable."}, "confirmation_option"),
            ],
        }
        first_problem_questions = {
            1: "resource_options",
            2: "resource_options",
            3: "resource_options",
            4: "reason_option",
            5: "scope_option",
            6: "session_options",
            7: "resource_options",
            8: "assessment_option",
            9: "resource_catalog_option",
            10: "resource_options",
            11: "related_repair_id",
        }
        fake_catalog = [{"option": 1, "value": "TEST-1", "label": "Test option"}]

        with patch("intake_middleware.catalog", return_value=fake_catalog):
            for problem_option in range(1, 12):
                with self.subTest(problem_option=problem_option):
                    state = start_intake()
                    intake_id = state["intake_id"]
                    answer_intake(intake_id, {"option": 1})
                    answer_intake(intake_id, {"option": 1})
                    state = answer_intake(intake_id, {"option": problem_option})
                    self.assertEqual(
                        state["question"]["key"],
                        first_problem_questions[problem_option],
                    )
                    for answer, expected_question in problem_answers[problem_option]:
                        state = answer_intake(intake_id, answer)
                        self.assertEqual(state["question"]["key"], expected_question)
                    state = answer_intake(intake_id, {"option": 1})
                    self.assertEqual(state["status"], "ready")


if __name__ == "__main__":
    unittest.main()
