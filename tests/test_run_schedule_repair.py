"""Tests for the side-effect-free schedule repair transformer."""

from __future__ import annotations

import copy
import json
import unittest

from tools.run_schedule_repair import run_schedule_repair


def report(disruption_type: str = "day_cancelled") -> dict:
    return {
        "status": "success",
        "report_complete": True,
        "disruption_report": {
            "disruption_id": "DSP-TEST000001",
            "disruption_type": disruption_type,
            "affected_resource_ids": ["R1"],
            "scope": {
                "affected_day": "Monday",
                "affected_date": None,
                "academic_week": 1,
                "whole_day": disruption_type == "day_cancelled",
                "start_time": None if disruption_type == "day_cancelled" else "08:30",
                "end_time": None if disruption_type == "day_cancelled" else "10:00",
            },
        },
    }


def schedule() -> list[dict]:
    return [
        {
            "Session ID": "S1",
            "Course": "Algorithms",
            "Day": "Monday",
            "Period": "P1",
            "Start": "08:30",
            "End": "10:00",
            "Room": "R1",
            "Weeks": "Weeks 1-12",
        },
        {
            "Session ID": "S2",
            "Course": "Databases",
            "Day": "Wednesday",
            "Period": "P2",
            "Start": "10:15",
            "End": "11:45",
            "Room": "R2",
            "Weeks": "Weeks 1-12",
        },
    ]


def assignment(**overrides) -> dict:
    value = {
        "session_id": "S1",
        "day": "Tuesday",
        "period": "P3",
        "start": "12:00",
        "end": "13:30",
        "room": "R3",
        "week": 2,
    }
    value.update(overrides)
    return value


class RunScheduleRepairTests(unittest.TestCase):
    def invoke(self, **overrides) -> dict:
        arguments = {
            "disruption_report": report(),
            "schedule_rows": schedule(),
            "affected_session_keys": ["S1"],
            "repair_assignments": [assignment()],
        }
        arguments.update(overrides)
        return json.loads(run_schedule_repair.invoke(arguments))

    def test_returns_one_complete_modified_schedule(self) -> None:
        result = self.invoke()

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["repair_complete"])
        self.assertNotIn("repair_options", result)
        rows = result["modified_schedule"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Day"], "Tuesday")
        self.assertEqual(rows[0]["Period"], "P3")
        self.assertEqual(rows[0]["Start"], "12:00")
        self.assertEqual(rows[0]["End"], "13:30")
        self.assertEqual(rows[0]["Room"], "R3")
        self.assertEqual(rows[0]["Weeks"], "Week 2")

    def test_preserves_unaffected_rows_and_does_not_mutate_input(self) -> None:
        original = schedule()
        untouched_copy = copy.deepcopy(original)
        result = self.invoke(schedule_rows=original)

        self.assertEqual(original, untouched_copy)
        self.assertEqual(result["modified_schedule"]["rows"][1], original[1])
        self.assertFalse(result["side_effects"]["source_modified"])
        self.assertFalse(result["side_effects"]["file_written"])

    def test_requires_exactly_one_assignment_for_every_affected_session(self) -> None:
        result = self.invoke(affected_session_keys=["S1", "S2"])

        self.assertEqual(result["status"], "invalid_request")
        self.assertFalse(result["repair_complete"])
        self.assertIsNone(result["modified_schedule"])
        self.assertIn("missing assignments", result["summary"])

    def test_rejects_assignment_outside_the_parent_defined_scope(self) -> None:
        result = self.invoke(
            repair_assignments=[assignment(), assignment(session_id="S2")]
        )

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("outside the repair scope", result["summary"])

    def test_rejects_duration_change(self) -> None:
        result = self.invoke(repair_assignments=[assignment(end="14:00")])

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("changes the session duration", result["summary"])

    def test_rejects_assignment_that_remains_on_cancelled_day(self) -> None:
        result = self.invoke(
            repair_assignments=[assignment(day="Monday", week=1)]
        )

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("inside the disruption scope", result["summary"])

    def test_same_weekday_in_a_following_week_is_allowed(self) -> None:
        result = self.invoke(repair_assignments=[assignment(day="Monday", week=2)])

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["modified_schedule"]["rows"][0]["Weeks"],
            "Week 2",
        )

    def test_partial_day_rejects_assignment_inside_cancelled_time_block(self) -> None:
        result = self.invoke(
            disruption_report=report("partial_day_cancelled"),
            repair_assignments=[
                assignment(day="Monday", start="08:30", end="10:00", week=1)
            ],
        )

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("inside the disruption scope", result["summary"])

    def test_room_closure_allows_same_time_only_with_a_different_room(self) -> None:
        closed_room = self.invoke(
            disruption_report=report("room_closed"),
            repair_assignments=[
                assignment(day="Monday", start="08:30", end="10:00", room="R1", week=1)
            ],
        )
        replacement_room = self.invoke(
            disruption_report=report("room_closed"),
            repair_assignments=[
                assignment(day="Monday", start="08:30", end="10:00", room="R4", week=1)
            ],
        )

        self.assertEqual(closed_room["status"], "invalid_request")
        self.assertEqual(replacement_room["status"], "success")
        self.assertEqual(
            replacement_room["modified_schedule"]["rows"][0]["Room"], "R4"
        )


if __name__ == "__main__":
    unittest.main()
