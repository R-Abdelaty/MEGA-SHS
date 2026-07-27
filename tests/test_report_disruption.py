"""Focused tests for the disruption intake contract."""

from __future__ import annotations

import json
import unittest

from tools.report_disruption import report_disruption


def invoke(**arguments: object) -> dict[str, object]:
    return json.loads(report_disruption.invoke(arguments))


class ReportDisruptionTests(unittest.TestCase):
    def test_complete_day_cancellation(self) -> None:
        result = invoke(
            disruption_type="cancel day",
            description="University event requires the campus to close.",
            affected_day_or_date="Sunday",
            academic_week=1,
            whole_day=True,
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["report_complete"])
        report = result["disruption_report"]
        self.assertEqual(report["disruption_type"], "day_cancelled")
        self.assertEqual(report["scope"]["affected_day"], "Sunday")
        self.assertEqual(result["next_action"]["tool"], "find_affected_sessions")

    def test_staff_disruption_requires_time_scope(self) -> None:
        result = invoke(
            disruption_type="lecturer unavailable",
            description="D001 is unavailable for part of the day.",
            affected_day_or_date="Monday",
            academic_week=4,
            affected_resource_ids=["D001"],
        )
        self.assertEqual(result["status"], "information_required")
        self.assertFalse(result["report_complete"])
        fields = {item["field"] for item in result["missing_information"]}
        self.assertIn("whole_day/start_time/end_time", fields)

    def test_exact_date_and_time_are_normalized(self) -> None:
        result = invoke(
            disruption_type="room closed",
            description="Room H11 is unavailable during maintenance.",
            affected_day_or_date="2026-09-14",
            academic_week=2,
            affected_resource_ids=[" H11 ", "h11"],
            start_time="10:00:00",
            end_time="12:00",
        )
        self.assertEqual(result["status"], "success")
        report = result["disruption_report"]
        self.assertEqual(report["scope"]["affected_date"], "2026-09-14")
        self.assertEqual(report["scope"]["start_time"], "10:00")
        self.assertEqual(report["affected_resource_ids"], ["H11"])
        self.assertEqual(result["next_action"]["tool"], "get_schedule")
        self.assertEqual(result["next_action"]["suggested_filters"]["room"], ["H11"])

    def test_unknown_type_requests_confirmation(self) -> None:
        result = invoke(
            disruption_type="something happened",
            description="The scope is not yet classified.",
        )
        self.assertEqual(result["status"], "information_required")
        self.assertFalse(result["report_complete"])
        self.assertIn("supported_disruption_types", result)

    def test_invalid_time_window_is_rejected(self) -> None:
        result = invoke(
            disruption_type="room closed",
            description="Room H11 is unavailable.",
            affected_day_or_date="Monday",
            academic_week=2,
            affected_resource_ids=["H11"],
            start_time="12:00",
            end_time="10:00",
        )
        self.assertEqual(result["status"], "invalid_request")
        self.assertFalse(result["report_complete"])

    def test_capacity_correction_requires_positive_capacity(self) -> None:
        result = invoke(
            disruption_type="capacity corrected",
            description="The confirmed capacity of H11 is 80.",
            affected_resource_ids=["H11"],
            corrected_room_capacity=80,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["disruption_report"]["corrected_room_capacity"],
            80,
        )

    def test_more_than_one_hundred_session_ids_are_preserved(self) -> None:
        session_ids = [f"SESSION-{index:03d}" for index in range(250)]
        result = invoke(
            disruption_type="session cancelled",
            description="A supplied collection of sessions was cancelled.",
            affected_session_ids=session_ids,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            len(result["disruption_report"]["affected_session_ids"]),
            250,
        )

    def test_same_report_has_stable_identifier(self) -> None:
        arguments = {
            "disruption_type": "day cancellation",
            "description": "The university is closed.",
            "affected_day_or_date": "Sunday",
            "academic_week": 1,
            "whole_day": True,
        }
        first = invoke(**arguments)
        second = invoke(**arguments)
        self.assertEqual(
            first["disruption_report"]["disruption_id"],
            second["disruption_report"]["disruption_id"],
        )


if __name__ == "__main__":
    unittest.main()
