"""End-to-end contract tests for the read-only cancellation orchestrator."""

from __future__ import annotations

import importlib
import json
import unittest
from unittest.mock import patch


cancel_module = importlib.import_module("tools.cancel_day")
cancel_day = cancel_module.cancel_day


class FakeTool:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def invoke(self, arguments: dict):
        self.calls.append(arguments)
        result = self.responder(arguments) if callable(self.responder) else self.responder
        return result if isinstance(result, str) else json.dumps(result)


def extraction_payload(rows: list[dict], sheet: str) -> dict:
    return {
        "status": "ok",
        "extraction": {
            "matching_rows_found": len(rows),
            "matching_rows_returned": len(rows),
            "has_more": False,
            "next_row_offset": None,
            "sheets": [
                {
                    "name": sheet,
                    "tables": [
                        {
                            "name": "Schedule",
                            "rows": [
                                {"excel_row": index + 2, "values": row}
                                for index, row in enumerate(rows)
                            ],
                        }
                    ],
                }
            ],
        },
        "limits": {"truncated": False, "completeness": "complete"},
    }


def session(
    key: str,
    session_type: str,
    period: str,
    start: str,
    end: str,
    group: str,
    staff: str,
    room_type: str,
) -> dict:
    return {
        "affected_session_key": key,
        "session_id": key,
        "course_id": f"C-{key}",
        "course_name": f"Course {key}",
        "session_type": session_type,
        "day": "Sunday",
        "period": period,
        "start": start,
        "end": end,
        "week": "Weeks 1-12",
        "room": "R1",
        "room_type": room_type,
        "expected_students": 25,
        "student_groups": group,
        "instructor": staff,
        "major": "CSE",
        "year": 1,
        "status": "Active",
    }


class CancelDayTests(unittest.TestCase):
    def setUp(self) -> None:
        cancel_module._PROTOTYPE_CACHE.clear()
        self.lecture = session(
            "S1", "Lecture", "P1", "08:30", "10:00", "G1", "Dr. One", "Lecture Hall"
        )
        self.tutorial = session(
            "S2", "Tutorial", "P2", "10:15", "11:45", "G2", "TA One", "Tutorial Room"
        )
        self.report_tool = FakeTool(
            {
                "status": "success",
                "report_complete": True,
                "disruption_report": {
                    "disruption_id": "DSP-CANCELTEST",
                    "disruption_type": "day_cancelled",
                    "scope": {
                        "affected_day": "Sunday",
                        "affected_date": None,
                        "academic_week": 1,
                        "whole_day": True,
                        "start_time": None,
                        "end_time": None,
                    },
                },
            }
        )
        self.find_tool = FakeTool(
            {
                "status": "success",
                "complete": True,
                "affected_session_count": 2,
                "affected_sessions": [self.lecture, self.tutorial],
                "result_pagination": {"has_more": False, "next_result_offset": None},
            }
        )
        self.priority_tool = FakeTool(
            {
                "status": "success",
                "ranking_complete": True,
                "global_repair_order": ["S1", "S2"],
            }
        )

        def schedule_response(arguments: dict) -> dict:
            sheet = arguments["sheet_name"]
            if sheet == "Semester Timetable":
                return extraction_payload([self.lecture, self.tutorial], sheet)
            if sheet == "Regular Assessments":
                return extraction_payload([], sheet)
            if sheet == "Doctor Directory":
                return extraction_payload([{"Doctor ID": "D1", "Doctor Name": "Dr. One"}], sheet)
            raise AssertionError(f"Unexpected sheet: {sheet}")

        self.schedule_tool = FakeTool(schedule_response)
        self.staff_tool = FakeTool(
            {
                "status": "success",
                "all_available": True,
                "available_staff_ids": ["Dr. One", "TA One"],
                "unavailable_staff_ids": [],
                "unknown_staff_ids": [],
                "staff_results": [],
            }
        )

        def room_response(arguments: dict) -> dict:
            return {
                "status": "success",
                "available_room_count": 2,
                "available_rooms": [
                    {"room": "LH1", "type": "Lecture Hall", "capacity": 100},
                    {"room": "T1", "type": "Tutorial Room", "capacity": 30},
                ],
            }

        self.room_tool = FakeTool(room_response)

    def invoke(self, **overrides) -> dict:
        arguments = {
            "day": "Sunday",
            "academic_week": 1,
            "reason": "Confirmed campus closure.",
            "cancellation_approved": True,
            "maximum_following_weeks": 2,
            "result_limit": 1,
        }
        arguments.update(overrides)
        with (
            patch.object(cancel_module, "report_disruption", self.report_tool),
            patch.object(cancel_module, "find_affected_sessions", self.find_tool),
            patch.object(cancel_module, "check_priority", self.priority_tool),
            patch.object(cancel_module, "get_schedule", self.schedule_tool),
            patch.object(
                cancel_module,
                "check_lecturer_or_ta_availability",
                self.staff_tool,
            ),
            patch.object(cancel_module, "check_room_availability", self.room_tool),
        ):
            return json.loads(cancel_day.invoke(arguments))

    def test_returns_one_paginated_following_week_prototype(self) -> None:
        result = self.invoke()

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["prototype_complete"])
        self.assertFalse(result["source_files_modified"])
        self.assertEqual(result["unassigned_session_count"], 0)
        timetable = result["prototype_timetable"]
        self.assertEqual(timetable["status"], "pending_user_confirmation")
        self.assertEqual(timetable["target_academic_weeks"], [2, 3])
        self.assertEqual(timetable["total_compensation_sessions"], 2)
        self.assertEqual(timetable["returned_compensation_sessions"], 1)
        self.assertTrue(timetable["pagination"]["has_more"])
        self.assertEqual(timetable["sessions"][0]["academic_week"], 2)
        self.assertEqual(result["approval"]["status"], "not_requested")

    def test_requires_explicit_cancellation_confirmation(self) -> None:
        result = self.invoke(cancellation_approved=False)

        self.assertEqual(result["status"], "invalid_request")
        self.assertFalse(result["prototype_complete"])
        self.assertEqual(self.report_tool.calls, [])

    def test_following_pages_reuse_the_computed_prototype(self) -> None:
        first = self.invoke(result_offset=0, result_limit=1)
        report_call_count = len(self.report_tool.calls)
        second = self.invoke(result_offset=1, result_limit=1)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(len(self.report_tool.calls), report_call_count)
        self.assertTrue(second["orchestration"]["cache_hit"])
        self.assertEqual(
            {
                first["prototype_timetable"]["sessions"][0]["session_id"],
                second["prototype_timetable"]["sessions"][0]["session_id"],
            },
            {"S1", "S2"},
        )

    def test_week_twelve_has_no_following_teaching_week(self) -> None:
        result = self.invoke(academic_week=12)

        self.assertEqual(result["status"], "invalid_request")
        self.assertIn("finals blackout", result["summary"])


if __name__ == "__main__":
    unittest.main()
